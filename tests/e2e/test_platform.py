"""Infrastructure-free unit tests for selecting the platform profile the OSMO lifecycle e2e tests run on.

These cover profile selection and resolution through ``scripts/platform-profile.sh``, the client
environment and OSMO login check, GPU node detection, the per-profile submit arguments, policy and
dataset sources, carrier references, and the evaluation assertions the local scenarios gate on. None of
them reaches OSMO, MLflow, a registry, or Azure.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.e2e import _osmo, conftest
from tests.e2e._aml import AzureMLWorkspace
from tests.e2e._common import RegisteredModel
from tests.e2e._lerobot_dataset import data_root_dataset_source
from tests.e2e._lerobot_lifecycle import LeRobotScenario
from tests.e2e._mlflow import LeRobotEvalResult, assert_evaluation_schema
from tests.e2e._platform import (
    AzurePlatform,
    LocalPlatform,
    common_default,
    parse_carrier_reference,
)
from tests.e2e._profile import (
    AZURE_PROFILE,
    LOCAL_PROFILE,
    PlatformProfile,
    PlatformProfileError,
    osmo_login_url,
    parse_profile,
    profile_selected,
    resolve_platform_profile,
    selected_profile_name,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIGEST = "sha256:" + "a" * 64
_LOCAL = PlatformProfile(
    name=LOCAL_PROFILE,
    tracking_backend="mlflow",
    osmo_url="http://10.43.250.20",
    osmo_config_dir="/tmp/osmo-local",
    mlflow_host_uri="http://10.43.250.30:5000",
    datasets_host_dir="/srv/example/datasets",
    image_registry="localhost:5000",
    kubeconfig="/tmp/example/kubeconfig.yaml",
    kube_context="example-edge",
)
_AZURE = PlatformProfile(name=AZURE_PROFILE, tracking_backend="azureml")
_WORKSPACE = AzureMLWorkspace(subscription_id="sub", resource_group="rg", workspace_name="ws")


def test_azure_is_the_default_profile() -> None:
    assert selected_profile_name({}) == AZURE_PROFILE
    assert selected_profile_name({"PLATFORM_PROFILE": " local "}) == LOCAL_PROFILE


def test_an_unknown_profile_is_rejected_but_selects_none_at_collection() -> None:
    with pytest.raises(PlatformProfileError, match="Unknown PLATFORM_PROFILE 'cloud'"):
        selected_profile_name({"PLATFORM_PROFILE": "cloud"})

    assert profile_selected(LOCAL_PROFILE, {"PLATFORM_PROFILE": "cloud"}) is False
    assert profile_selected(LOCAL_PROFILE, {"PLATFORM_PROFILE": "local"}) is True


def test_parse_profile_reads_the_resolver_lines_it_uses() -> None:
    profile = parse_profile(
        "PROFILE_NAME=local\nPROFILE_TRACKING_BACKEND=mlflow\nPROFILE_MLFLOW_HOST_URI=http://10.43.250.30:5000\n"
        "PROFILE_KUBECONFIG=/tmp/kubeconfig.yaml\nPROFILE_TASK_CPU=12\n"
    )

    assert profile.is_local
    assert profile.mlflow_host_uri == "http://10.43.250.30:5000"
    assert profile.kubeconfig == "/tmp/kubeconfig.yaml"


def test_parse_profile_rejects_output_without_a_profile() -> None:
    with pytest.raises(PlatformProfileError, match="printed no profile"):
        parse_profile("unrelated output\n")


def test_resolves_the_local_profile_through_the_repository_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LOCAL_DATA_ROOT": "/srv/example",
        "LOCAL_KUBECONFIG": "/tmp/example/kubeconfig.yaml",
        "LOCAL_KUBE_CONTEXT": "example-edge",
        "LOCAL_OSMO_CONFIG_DIR": "/tmp/osmo-local",
        "TRACKING_BACKEND": "",
    }.items():
        monkeypatch.setenv(name, value)

    profile = resolve_platform_profile(_REPO_ROOT, {"PLATFORM_PROFILE": "local"})

    assert profile == PlatformProfile(
        name=LOCAL_PROFILE,
        tracking_backend="mlflow",
        osmo_url="http://10.43.250.20",
        osmo_config_dir="/tmp/osmo-local",
        mlflow_host_uri="http://10.43.250.30:5000",
        datasets_host_dir="/srv/example/datasets",
        image_registry="localhost:5000",
        kubeconfig="/tmp/example/kubeconfig.yaml",
        kube_context="example-edge",
    )


def test_resolves_the_azure_profile_without_local_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKING_BACKEND", "")

    profile = resolve_platform_profile(_REPO_ROOT, {})

    assert profile.name == AZURE_PROFILE
    assert profile.tracking_backend == "azureml"
    assert profile.client_environment() == {}


def test_local_clients_target_the_profile_login_and_kubeconfig() -> None:
    assert _LOCAL.client_environment() == {
        "OSMO_CONFIG_FILE_DIR": "/tmp/osmo-local",
        "KUBECONFIG": "/tmp/example/kubeconfig.yaml",
    }


def test_osmo_login_url_reads_the_cli_login(tmp_path: Path) -> None:
    (tmp_path / "login.yaml").write_text("dev_login:\n  username: admin\nurl: http://10.43.250.20\n", encoding="utf-8")

    assert osmo_login_url(str(tmp_path)) == "http://10.43.250.20"
    assert osmo_login_url(str(tmp_path / "missing")) is None


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        ({"metadata": {"labels": {"nvidia.com/gpu.present": "true"}}, "status": {}}, True),
        ({"metadata": {"labels": {}}, "status": {"allocatable": {"nvidia.com/gpu": "2"}}}, True),
        ({"metadata": {"labels": {}}, "status": {"allocatable": {"nvidia.com/gpu": "0"}}}, False),
        ({"metadata": {"labels": {}}, "status": {"allocatable": {"cpu": "32"}}}, False),
        ("not-a-node", False),
    ],
)
def test_node_has_gpu_reads_the_label_or_allocatable_devices(node: object, expected: bool) -> None:
    assert conftest._node_has_gpu(node) is expected


def test_azure_only_fixtures_skip_under_the_local_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_PROFILE", "local")

    with pytest.raises(pytest.skip.Exception, match="Azure ML needs the azure platform profile"):
        conftest._skip_under_local_profile("Azure ML")


class _SetupItem:
    def __init__(self, fixturenames: tuple[str, ...], *, hf_token: bool = False) -> None:
        self.fixturenames = fixturenames
        self._hf_token = hf_token
        self.stash = pytest.Stash()

    def get_closest_marker(self, name: str) -> object | None:
        return object() if self._hf_token and name == "requires_hf_token" else None


def test_azure_tests_skip_before_the_token_gate_under_the_local_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_PROFILE", "local")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    item = _SetupItem(("repo_root", "aml_workspace", "aml_compute_target"), hf_token=True)

    with pytest.raises(pytest.skip.Exception, match="Needs the azure platform profile for aml_workspace"):
        conftest.pytest_runtest_setup(item)  # type: ignore[arg-type]


def test_local_profile_runs_tests_without_azure_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_PROFILE", "local")

    conftest.pytest_runtest_setup(_SetupItem(("repo_root", "e2e_platform")))  # type: ignore[arg-type]


def test_azure_profile_keeps_azure_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLATFORM_PROFILE", raising=False)

    conftest.pytest_runtest_setup(_SetupItem(("aml_workspace", "storage_account")))  # type: ignore[arg-type]


def test_rl_submissions_select_the_profile_and_size_azure_tasks_for_an_a100() -> None:
    azure = _osmo._osmo_rl_smoke_training_args(_AZURE, task="Isaac-Ant-v0", max_iterations=10, num_envs=64)
    local = _osmo._osmo_rl_smoke_training_args(_LOCAL, task="Isaac-Ant-v0", max_iterations=10, num_envs=64)

    assert azure[:2] == ["--profile", AZURE_PROFILE]
    assert azure[-4:] == ["--cpu", "20", "--memory", "180Gi"]
    assert local == ["--profile", LOCAL_PROFILE, "--task", "Isaac-Ant-v0", "--max-iterations", "10", "--num-envs", "64"]


def test_isaac_eval_needs_exactly_one_policy() -> None:
    platform = LocalPlatform(profile=_LOCAL, repo_root=_REPO_ROOT)

    with pytest.raises(ValueError, match="exactly one"):
        _osmo.submit_osmo_isaaclab_eval(
            _REPO_ROOT, platform, task="t", num_envs=1, max_steps=1, eval_episodes=1, success_threshold=0.0
        )


def test_each_platform_names_its_registry_policy_source() -> None:
    model = RegisteredModel("policy", "3")

    assert LocalPlatform(profile=_LOCAL, repo_root=_REPO_ROOT).lerobot_policy_source(model).args == (
        "--policy-uri",
        "models:/policy/3",
    )
    assert AzurePlatform(profile=_AZURE, workspace=_WORKSPACE, repo_root=_REPO_ROOT).lerobot_policy_source(
        model
    ).args == ("--from-aml-model", "--model-name", "policy", "--model-version", "3")


def test_azure_platform_passes_the_workspace_and_local_passes_nothing() -> None:
    assert AzurePlatform(profile=_AZURE, workspace=_WORKSPACE, repo_root=_REPO_ROOT).workspace_args() == [
        "--azure-subscription-id",
        "sub",
        "--azure-resource-group",
        "rg",
        "--azure-workspace-name",
        "ws",
    ]
    assert LocalPlatform(profile=_LOCAL, repo_root=_REPO_ROOT).workspace_args() == []


def test_registered_model_override_uses_the_platform_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_osmo._LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.setenv(_osmo._LEROBOT_EVAL_MODEL_ENV, "policy:3")
    platform = LocalPlatform(profile=_LOCAL, repo_root=_REPO_ROOT)

    source = _osmo.resolve_osmo_lerobot_eval_policy_override(platform.lerobot_policy_source)

    assert source is not None
    assert source.args == ("--policy-uri", "models:/policy/3")
    assert _osmo.registered_model_override() == RegisteredModel("policy", "3")


def test_data_root_dataset_source_mounts_an_existing_dataset(tmp_path: Path) -> None:
    (tmp_path / "pick" / "meta").mkdir(parents=True)
    (tmp_path / "pick" / "meta" / "info.json").write_text("{}", encoding="utf-8")

    source = data_root_dataset_source(str(tmp_path), "pick")

    assert source.training_args == ("--local-dataset", "pick")
    assert source.eval_args == ("--local-dataset", "pick")


def test_data_root_dataset_source_skips_a_missing_dataset(tmp_path: Path) -> None:
    with pytest.raises(pytest.skip.Exception, match=r"infrastructure/local/datasets\.sh"):
        data_root_dataset_source(str(tmp_path), "missing")


def test_scenario_dataset_can_be_renamed_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = LeRobotScenario(
        policy_type="act",
        training_steps=1,
        batch_size=1,
        save_freq=1,
        log_freq=1,
        learning_rate="1e-4",
        eval_episodes=1,
        data_root_dataset="default-dataset",
        dataset_variable="E2E_EXAMPLE_DATASET",
    )
    monkeypatch.delenv("E2E_EXAMPLE_DATASET", raising=False)
    assert scenario.dataset_name() == "default-dataset"

    monkeypatch.setenv("E2E_EXAMPLE_DATASET", "other-dataset")
    assert scenario.dataset_name() == "other-dataset"


def test_parse_carrier_reference_reads_the_build_summary() -> None:
    output = (
        f"Image (tag):         localhost:5000/policy:3-sha-abc\nImage (digest):      localhost:5000/policy@{_DIGEST}\n"
    )

    assert parse_carrier_reference(output) == f"localhost:5000/policy@{_DIGEST}"
    with pytest.raises(AssertionError, match="no digest-pinned image"):
        parse_carrier_reference("Image (tag): localhost:5000/policy:3\n")


def test_remove_policy_carrier_deletes_the_manifest_and_tolerates_a_missing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[urllib.request.Request] = []
    responses = iter([None, urllib.error.HTTPError("url", 404, "missing", {}, io.BytesIO())])

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def _urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        requests.append(request)
        error = next(responses)
        if error is not None:
            raise error
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    platform = LocalPlatform(profile=_LOCAL, repo_root=_REPO_ROOT)

    platform.remove_policy_carrier(f"localhost:5000/team/policy@{_DIGEST}")
    platform.remove_policy_carrier(f"localhost:5000/team/policy@{_DIGEST}")

    assert [request.method for request in requests] == ["DELETE", "DELETE"]
    assert requests[0].full_url == f"http://localhost:5000/v2/team/policy/manifests/{_DIGEST}"


def test_common_default_reads_pinned_images_and_rejects_other_names() -> None:
    assert "@sha256:" in common_default(_REPO_ROOT, "DEFAULT_LEROBOT_EVAL_IMAGE")
    with pytest.raises(ValueError, match="Not a shell variable name"):
        common_default(_REPO_ROOT, "DEFAULT; rm")


def _schema(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "evaluation_schema_version": 1,
        "aggregate_verdict": "pass",
        "metrics": [{"name": "action_accuracy_l2", "value": 0.25}],
    }
    payload.update(overrides)
    return payload


def test_evaluation_schema_accepts_finite_metrics_without_failure_cases() -> None:
    assert_evaluation_schema(_schema(), "")


@pytest.mark.parametrize(
    ("payload", "failure_cases", "message"),
    [
        (_schema(evaluation_schema_version=2), "", "not evaluation schema version 1"),
        (_schema(aggregate_verdict="fail"), "", "aggregate verdict"),
        (_schema(metrics=[]), "", "lists no metrics"),
        (_schema(metrics=[{"name": "action_accuracy_l2", "value": float("nan")}]), "", "finite value"),
        (_schema(), json.dumps({"episode_id": "0"}) + "\n", "1 failure case"),
    ],
)
def test_evaluation_schema_rejects_invalid_results(payload: object, failure_cases: str, message: str) -> None:
    with pytest.raises(AssertionError, match=message):
        assert_evaluation_schema(payload, failure_cases)


def test_action_error_leaves_out_timing_metrics() -> None:
    result = LeRobotEvalResult(
        run_id="run",
        metrics={
            "ep0_mse": 0.1,
            "ep0_mae": 0.2,
            "ep0_avg_inference_ms": 9.0,
            "aggregate_mse": 0.1,
            "aggregate_throughput_hz": 90.0,
            "episodes_evaluated": 1.0,
        },
    )

    assert result.action_error() == {"ep0_mse": 0.1, "ep0_mae": 0.2, "aggregate_mse": 0.1}
