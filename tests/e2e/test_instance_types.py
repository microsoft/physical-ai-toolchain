"""Infrastructure-free tests for choosing and checking the GPU instance types of the Azure ML checks.

Every GPU job in the RL, IL, VLA, and GPU smoke checks requests the instance type set for its
category in ``E2E_AML_INSTANCE_TYPE_<CATEGORY>`` or ``E2E_AML_INSTANCE_TYPE``, read from the
repository-root ``.env.local`` before the environment. These tests cover that resolution, the
preflight that checks the choice against the compute's instance types before anything is
submitted, the submit helpers that pass it on, and the script defaults the preflight assumes.
"""

# cspell:ignore amlcompute

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.e2e import _aml
from tests.e2e._environment import (
    INSTANCE_TYPE_VARIABLE,
    LOCAL_ENV_FILE,
    InstanceTypeChoice,
    LocalEnvError,
    instance_type_variables,
    resolve_instance_type,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RL_VARIABLE = f"{INSTANCE_TYPE_VARIABLE}_RL"
_WORKSPACE = _aml.AzureMLWorkspace("sub", "rg-sample", "mlw-sample")


@pytest.fixture(autouse=True)
def _no_exported_instance_types(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(INSTANCE_TYPE_VARIABLE):
            monkeypatch.delenv(name)


def _write_local_env(root: Path, text: str) -> None:
    (root / LOCAL_ENV_FILE).write_text(text, encoding="utf-8")


# Resolution


@pytest.mark.parametrize(
    ("category", "expected"),
    [("rl", f"{INSTANCE_TYPE_VARIABLE}_RL"), ("gpu-smoke", f"{INSTANCE_TYPE_VARIABLE}_GPU_SMOKE")],
)
def test_each_category_has_its_own_variable_before_the_shared_one(category: str, expected: str) -> None:
    assert instance_type_variables(category) == (expected, INSTANCE_TYPE_VARIABLE)


def test_nothing_set_keeps_the_script_defaults(tmp_path: Path) -> None:
    choice = resolve_instance_type(tmp_path, "rl", {})

    assert choice == InstanceTypeChoice(None)
    assert choice.describe() == "script default"


def test_the_shared_variable_in_local_env_applies_to_every_category(tmp_path: Path) -> None:
    _write_local_env(tmp_path, f"{INSTANCE_TYPE_VARIABLE}=gpu-sample-1x\n")

    for category in ("rl", "il", "vla", "gpu-smoke"):
        choice = resolve_instance_type(tmp_path, category, {})
        assert choice == InstanceTypeChoice("gpu-sample-1x", INSTANCE_TYPE_VARIABLE, LOCAL_ENV_FILE)


def test_a_category_variable_wins_over_the_shared_one(tmp_path: Path) -> None:
    _write_local_env(tmp_path, f"{INSTANCE_TYPE_VARIABLE}=gpu-small\n{_RL_VARIABLE}='gpu-rtx'\n")

    assert resolve_instance_type(tmp_path, "rl", {}).value == "gpu-rtx"
    assert resolve_instance_type(tmp_path, "vla", {}).value == "gpu-small"


def test_local_env_wins_over_the_environment_for_the_same_variable(tmp_path: Path) -> None:
    _write_local_env(tmp_path, f"export {INSTANCE_TYPE_VARIABLE}=gpu-from-file\n")

    choice = resolve_instance_type(tmp_path, "il", {INSTANCE_TYPE_VARIABLE: "gpu-from-shell"})

    assert choice == InstanceTypeChoice("gpu-from-file", INSTANCE_TYPE_VARIABLE, LOCAL_ENV_FILE)
    assert choice.describe() == f"gpu-from-file ({INSTANCE_TYPE_VARIABLE} in {LOCAL_ENV_FILE})"


def test_an_exported_category_variable_wins_over_a_shared_one_in_the_file(tmp_path: Path) -> None:
    _write_local_env(tmp_path, f"{INSTANCE_TYPE_VARIABLE}=gpu-from-file\n")

    choice = resolve_instance_type(tmp_path, "rl", {_RL_VARIABLE: " gpu-rtx "})

    assert choice == InstanceTypeChoice("gpu-rtx", _RL_VARIABLE, "environment")


def test_an_exported_empty_value_omits_the_instance_type(tmp_path: Path) -> None:
    choice = resolve_instance_type(tmp_path, "vla", {INSTANCE_TYPE_VARIABLE: ""})

    assert choice == InstanceTypeChoice("", INSTANCE_TYPE_VARIABLE, "environment")
    assert choice.describe().startswith("omitted")


@pytest.mark.parametrize("assignment", ["", "''", '"  "'])
def test_an_empty_value_in_local_env_is_an_error(tmp_path: Path, assignment: str) -> None:
    _write_local_env(tmp_path, f"{_RL_VARIABLE}={assignment}\n")

    with pytest.raises(LocalEnvError, match=rf"{_RL_VARIABLE} is empty in {re.escape(LOCAL_ENV_FILE)}"):
        resolve_instance_type(tmp_path, "rl", {_RL_VARIABLE: "gpu-from-shell"})


def test_an_invalid_value_in_local_env_is_an_error_that_hides_it(tmp_path: Path) -> None:
    _write_local_env(tmp_path, f"{INSTANCE_TYPE_VARIABLE}=$(hidden-value)\n")

    with pytest.raises(LocalEnvError) as error:
        resolve_instance_type(tmp_path, "rl", {})

    assert INSTANCE_TYPE_VARIABLE in str(error.value)
    assert "hidden-value" not in str(error.value)


# Preflight

_AKS_TYPES = {
    "defaultinstancetype": {"resources": {"limits": {"cpu": "2", "memory": "8Gi"}}},
    "gpuspot": {"resources": {"limits": {"nvidia.com/gpu": 1}}},
    "gpu": {"resources": {"limits": {"nvidia.com/gpu": "1"}}},
}
_HIL_TYPES = {
    "defaultinstancetype": {"resources": {"limits": {"cpu": "2", "memory": "8Gi"}}},
    "gpu": {"resources": {"limits": {"nvidia.com/gpu": 1}}},
}


def _compute(types: dict[str, object] | None, name: str = "k8s-sample") -> _aml.AzureMLCompute:
    payload: dict[str, object] = {
        "type": "kubernetes" if types is not None else "amlcompute",
        "properties": {"instance_types": types, "relayConnectionString": "not-kept"},
    }
    return _aml.aml_compute_from_payload(name, payload)


def test_the_compute_keeps_only_its_instance_types() -> None:
    compute = _compute(_AKS_TYPES)

    assert compute == _aml.AzureMLCompute("k8s-sample", frozenset(_AKS_TYPES), frozenset({"gpu", "gpuspot"}))
    assert _compute(None) == _aml.AzureMLCompute("k8s-sample")


def _problem(choice: InstanceTypeChoice, types: dict[str, object] | None, category: str = "rl") -> str | None:
    scripts = {
        "rl": (_aml.RL_TRAINING_SCRIPT, _aml.ISAAC_EVAL_SCRIPT),
        "vla": (_aml.VLA_PI0_TRAINING_SCRIPT, _aml.LEROBOT_EVAL_SCRIPT),
        "gpu-smoke": (_aml.GPU_SMOKE_SCRIPT,),
    }[category]
    return _aml.instance_type_problem(choice, _compute(types), category=category, scripts=scripts)


def test_a_named_gpu_type_the_compute_defines_passes() -> None:
    assert _problem(InstanceTypeChoice("gpu", INSTANCE_TYPE_VARIABLE, LOCAL_ENV_FILE), _HIL_TYPES) is None


def test_a_named_type_the_compute_lacks_names_the_fix() -> None:
    problem = _problem(InstanceTypeChoice("gpu-missing", _RL_VARIABLE, LOCAL_ENV_FILE), _AKS_TYPES)

    assert problem is not None
    assert f"{_RL_VARIABLE} in {LOCAL_ENV_FILE} requests instance type gpu-missing" in problem
    assert "doesn't define" in problem
    assert f"Set {_RL_VARIABLE} or {INSTANCE_TYPE_VARIABLE} in {LOCAL_ENV_FILE}" in problem
    assert "GPU instance types: gpu, gpuspot" in problem


def test_a_named_type_without_a_gpu_is_rejected() -> None:
    problem = _problem(InstanceTypeChoice("defaultinstancetype", INSTANCE_TYPE_VARIABLE, "environment"), _AKS_TYPES)

    assert problem is not None
    assert "defines without a GPU" in problem


def test_the_script_defaults_pass_on_a_compute_that_defines_them() -> None:
    assert _problem(InstanceTypeChoice(None), _AKS_TYPES) is None
    assert _problem(InstanceTypeChoice(None), _AKS_TYPES, "vla") is None


def test_a_hil_compute_without_gpuspot_asks_for_an_instance_type() -> None:
    problem = _problem(InstanceTypeChoice(None), _HIL_TYPES)

    assert problem is not None
    assert "no GPU instance type named gpuspot" in problem
    assert "submit-azureml-training.sh, submit-azureml-isaaclab-evaluation.sh" in problem
    assert f"Set {_RL_VARIABLE} or {INSTANCE_TYPE_VARIABLE} in {LOCAL_ENV_FILE}" in problem
    assert "GPU instance types: gpu" in problem


def test_vla_needs_both_of_its_script_defaults() -> None:
    problem = _problem(InstanceTypeChoice(None), {"gpuspot": _AKS_TYPES["gpuspot"]}, "vla")

    assert problem is not None
    assert "named gpu," in problem


@pytest.mark.parametrize("category", ["rl", "gpu-smoke"])
def test_an_empty_choice_is_rejected_on_a_kubernetes_compute(category: str) -> None:
    problem = _problem(InstanceTypeChoice("", INSTANCE_TYPE_VARIABLE, "environment"), _AKS_TYPES, category)

    assert problem is not None
    assert "omits the instance type" in problem
    assert "managed AmlCompute" in problem


@pytest.mark.parametrize(
    "choice",
    [InstanceTypeChoice(None), InstanceTypeChoice(""), InstanceTypeChoice("anything", INSTANCE_TYPE_VARIABLE)],
)
def test_a_compute_without_instance_types_is_not_checked(choice: InstanceTypeChoice) -> None:
    assert _problem(choice, None, "gpu-smoke") is None


def test_a_compute_without_gpu_types_says_so() -> None:
    problem = _problem(InstanceTypeChoice(None), {"defaultinstancetype": _AKS_TYPES["defaultinstancetype"]})

    assert problem is not None
    assert "it defines none, only defaultinstancetype" in problem


def _record_submissions(monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[str]], list[str]]:
    commands: list[list[str]] = []
    messages: list[str] = []

    def fake_run(args: list[str], *, cwd: Path, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="stopped by the test")

    monkeypatch.setattr(_aml, "run_command", fake_run)
    monkeypatch.setattr(_aml, "log_e2e", messages.append)
    return commands, messages


def test_the_preflight_fails_before_anything_is_submitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands, _ = _record_submissions(monkeypatch)
    _write_local_env(tmp_path, f"{INSTANCE_TYPE_VARIABLE}=gpu-missing\n")

    with pytest.raises(pytest.fail.Exception, match="requests instance type gpu-missing"):
        _aml.require_gpu_instance_type(
            _compute(_HIL_TYPES), tmp_path, category="rl", scripts=(_aml.RL_TRAINING_SCRIPT,)
        )

    assert commands == []


def test_the_preflight_reports_an_invalid_local_env_without_its_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_submissions(monkeypatch)
    _write_local_env(tmp_path, f"{_RL_VARIABLE}=`hidden-value`\n")

    with pytest.raises(pytest.fail.Exception) as failure:
        _aml.require_gpu_instance_type(
            _compute(_AKS_TYPES), tmp_path, category="rl", scripts=(_aml.RL_TRAINING_SCRIPT,)
        )

    assert "Can't choose a GPU instance type for rl" in str(failure.value)
    assert "hidden-value" not in str(failure.value)


def test_the_preflight_returns_and_logs_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, messages = _record_submissions(monkeypatch)
    monkeypatch.setenv(_RL_VARIABLE, "gpu")

    value = _aml.require_gpu_instance_type(
        _compute(_HIL_TYPES), tmp_path, category="rl", scripts=(_aml.RL_TRAINING_SCRIPT,)
    )

    assert value == "gpu"
    assert messages == [f"GPU instance type for the rl jobs: gpu ({_RL_VARIABLE} in environment)"]


# Submission


def _instance_type_argument(command: list[str]) -> str | None:
    if "--instance-type" not in command:
        return None
    return command[command.index("--instance-type") + 1]


def _rl_training(root: Path, instance_type: str | None) -> None:
    _aml.submit_aml_training(
        root,
        _WORKSPACE,
        task="Sample-Task-v0",
        max_iterations=1,
        num_envs=1,
        register_model_name="sample-model",
        instance_type=instance_type,
    )


def _isaac_eval(root: Path, instance_type: str | None) -> None:
    _aml.submit_aml_isaaclab_eval(
        root,
        _WORKSPACE,
        model=_aml.AmlModelRef("sample-model", "1"),
        task="Sample-Task-v0",
        eval_episodes=1,
        num_envs=1,
        instance_type=instance_type,
    )


def _lerobot_training(root: Path, instance_type: str | None) -> None:
    _aml.submit_aml_lerobot_training(
        root,
        _WORKSPACE,
        blob_url="https://sample.invalid/datasets/sample",
        policy_type="diffusion",
        training_steps=2,
        save_freq=1,
        batch_size=1,
        log_freq=1,
        register_model_name="sample-model",
        instance_type=instance_type,
    )


def _vla_training(root: Path, instance_type: str | None) -> None:
    _aml.submit_aml_vla_pi0_training(
        root,
        _WORKSPACE,
        blob_url="https://sample.invalid/datasets/sample",
        training_steps=2,
        save_freq=1,
        batch_size=1,
        log_freq=1,
        register_model_name="sample-model",
        instance_type=instance_type,
    )


def _lerobot_eval(policy_type: str) -> Callable[[Path, str | None], None]:
    def submit(root: Path, instance_type: str | None) -> None:
        _aml.submit_aml_lerobot_eval(
            root,
            _WORKSPACE,
            policy_source=_aml.AmlLeRobotEvalPolicySource(args=("--from-aml-model",), description="sample model"),
            policy_type=policy_type,
            eval_episodes=1,
            eval_batch_size=1,
            blob_storage_account="sample-account",
            blob_container="sample-container",
            blob_prefix="sample/prefix",
            instance_type=instance_type,
        )

    return submit


@pytest.mark.parametrize(
    "submit",
    [
        pytest.param(_rl_training, id="rl-training"),
        pytest.param(_isaac_eval, id="isaac-eval"),
        pytest.param(_lerobot_training, id="il-training"),
        pytest.param(_lerobot_eval("diffusion"), id="il-eval"),
        pytest.param(_vla_training, id="vla-training"),
        pytest.param(_lerobot_eval("pi0"), id="vla-eval"),
    ],
)
@pytest.mark.parametrize(
    ("instance_type", "expected_argument", "expected_label"),
    [
        pytest.param(None, None, "<script default>", id="script-default"),
        pytest.param("gpu-sample-1x", "gpu-sample-1x", "gpu-sample-1x", id="named"),
        pytest.param("", "", "<managed-compute>", id="omitted"),
    ],
)
def test_every_gpu_submission_requests_the_chosen_instance_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    submit: Callable[[Path, str | None], None],
    instance_type: str | None,
    expected_argument: str | None,
    expected_label: str,
) -> None:
    commands, messages = _record_submissions(monkeypatch)

    with pytest.raises(AssertionError, match="submission failed"):
        submit(tmp_path, instance_type)

    assert _instance_type_argument(commands[0]) == expected_argument
    assert f"instance_type={expected_label}" in messages[0]


# Script defaults

_DEFAULT_ASSIGNMENT = re.compile(r'^instance_type="([^"]*)"$', re.MULTILINE)


@pytest.mark.parametrize(("script", "default"), sorted(_aml.SCRIPT_DEFAULT_INSTANCE_TYPES.items()))
def test_the_preflight_assumes_each_scripts_current_default(script: str, default: str) -> None:
    text = (_REPO_ROOT / script).read_text(encoding="utf-8")

    assert _DEFAULT_ASSIGNMENT.findall(text) == [default], f"update SCRIPT_DEFAULT_INSTANCE_TYPES for {script}"


def test_the_pi0_evaluation_wrapper_uses_the_lerobot_evaluation_default() -> None:
    wrapper = (_REPO_ROOT / "evaluation/sil/scripts/submit-azureml-vla-pi0-eval.sh").read_text(encoding="utf-8")

    assert 'exec "$SCRIPT_DIR/submit-azureml-lerobot-eval.sh"' in wrapper
    assert _DEFAULT_ASSIGNMENT.search(wrapper) is None
