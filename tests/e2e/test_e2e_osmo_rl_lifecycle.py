"""
End-to-end lifecycle test for the OSMO RL (Isaac/SKRL) train -> eval path.

Runs against the platform profile ``PLATFORM_PROFILE`` selects. Submits a real OSMO training
workflow that registers its checkpoint under a unique model name, validates MLflow tracking and
task success, resolves the concrete registered model version (never ``latest``), then runs the
gated evaluation of the ``models:/<name>/<version>`` checkpoint via ``submit-osmo-eval.sh``.
The azure profile registers in Azure ML and smoke-tests the pipeline. The local profile registers
in the local MLflow registry, trains Anymal-C on flat ground until the gate passes, has the VLM
judge score the checkpoint's rollout videos, and also gates the runnable carrier image built from
the registered checkpoint.

Set ``E2E_OSMO_ISAAC_EVAL_CHECKPOINT_URI`` to skip training and evaluate a pre-existing
checkpoint — a fast inner loop while fixing the eval path. A ``models:/<name>/<version>`` URI
also feeds the local carrier evaluation.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_rl_lifecycle.py
PLATFORM_PROFILE=local uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_rl_lifecycle.py
```
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.e2e._common import (
    E2EHandle,
    RegisteredModel,
    assert_e2e_handle_complete,
    e2e_name,
    log_e2e,
    parse_provenance_marker,
    register_cleanup,
)
from tests.e2e._judge import JUDGE_TASK_NAME, delete_osmo_dataset, read_judgments, submit_osmo_judge
from tests.e2e._mlflow import IsaacEvalGate, assert_osmo_isaac_eval_gate, assert_osmo_workflow_has_mlflow_tracking
from tests.e2e._osmo import (
    _OSMO_ISAAC_EVAL_CHECKPOINT_URI_ENV,
    assert_workflow_task_succeeded,
    cancel_osmo_workflows_by_identifier,
    fetch_workflow_task_logs,
    monitor_osmo_workflow,
    resolve_osmo_isaac_eval_checkpoint_override,
    submit_osmo_isaaclab_eval,
    submit_osmo_training,
)
from tests.e2e._platform import E2EPlatform, LocalPlatform, common_default
from tests.e2e._profile import AZURE_PROFILE, LOCAL_PROFILE

_ISAAC_TRAINING_TASK_NAME = "isaac-training"
_ISAAC_INFERENCE_TASK_NAME = "isaac-inference"
_REGISTERED_MODEL_URI = re.compile(r"^models:/(?P<name>[^/]+)/(?P<version>[^/]+)$")


@dataclass(frozen=True)
class _RLScenario:
    """What one platform profile trains, and how strictly its evaluation gates the result."""

    task: str
    max_iterations: int
    num_envs: int
    eval_num_envs: int
    eval_max_steps: int
    eval_episodes: int
    success_threshold: float
    evaluate_carrier: bool
    # Instruction the VLM judge scores the checkpoint's rollout videos against; None skips the judge.
    judge_instruction: str | None = None


# The azure scenario smoke-tests the pipeline on a spot A100: ten iterations cannot teach Anymal to walk,
# so its gate only requires the evaluation to finish. The local scenario trains Anymal-C on flat ground,
# whose episodes end early when the robot falls, until the gate passes for the checkpoint and its carrier.
# Cartpole would train faster, but in the pinned Isaac Lab 3.0 image the direct task ignores its actions
# on the default PhysX backend, and the manager-based task never ends when the pole falls.
_SCENARIOS = {
    AZURE_PROFILE: _RLScenario(
        task="Isaac-Velocity-Rough-Anymal-C-v0",
        max_iterations=10,
        num_envs=64,
        eval_num_envs=4,
        eval_max_steps=50,
        eval_episodes=4,
        success_threshold=0.0,
        evaluate_carrier=False,
    ),
    LOCAL_PROFILE: _RLScenario(
        task="Isaac-Velocity-Flat-Anymal-C-v0",
        max_iterations=300,
        num_envs=4096,
        eval_num_envs=32,
        eval_max_steps=500,
        eval_episodes=100,
        success_threshold=0.7,
        evaluate_carrier=True,
        judge_instruction="The four-legged robot walks across the flat ground without falling over.",
    ),
}


def _declared_runtime_versions(repo_root: Path) -> dict[str, str]:
    payload = tomllib.loads((repo_root / "training/rl/pyproject.toml").read_text(encoding="utf-8"))
    versions = {}
    for requirement in payload["project"]["dependencies"]:
        name, separator, version = requirement.partition("==")
        if separator:
            versions[name.lower().replace("_", "-")] = version
    return versions


def _registered_model(checkpoint_uri: str) -> RegisteredModel | None:
    match = _REGISTERED_MODEL_URI.fullmatch(checkpoint_uri)
    return RegisteredModel(name=match["name"], version=match["version"]) if match else None


def test_resolve_osmo_isaac_eval_checkpoint_override_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OSMO_ISAAC_EVAL_CHECKPOINT_URI_ENV, "models:/name/1")

    assert resolve_osmo_isaac_eval_checkpoint_override() == "models:/name/1"


def test_resolve_osmo_isaac_eval_checkpoint_override_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_OSMO_ISAAC_EVAL_CHECKPOINT_URI_ENV, raising=False)

    assert resolve_osmo_isaac_eval_checkpoint_override() is None


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("models:/cartpole-policy/3", RegisteredModel("cartpole-policy", "3")),
        ("runs:/abc/checkpoints", None),
        ("models:/cartpole-policy", None),
    ],
)
def test_registered_model_reads_models_uris_only(uri: str, expected: RegisteredModel | None) -> None:
    assert _registered_model(uri) == expected


@pytest.mark.parametrize("profile", [AZURE_PROFILE, LOCAL_PROFILE])
def test_every_profile_has_a_scenario_that_fits_its_gate(profile: str) -> None:
    scenario = _SCENARIOS[profile]

    assert 0.0 <= scenario.success_threshold <= 1.0
    assert scenario.eval_episodes >= scenario.eval_num_envs


def _evaluate(
    request: pytest.FixtureRequest,
    platform: E2EPlatform,
    scenario: _RLScenario,
    *,
    label: str,
    checkpoint_uri: str | None = None,
    policy_image: str | None = None,
    output_dataset: str | None = None,
) -> IsaacEvalGate:
    eval_workflow = submit_osmo_isaaclab_eval(
        platform.repo_root,
        platform,
        task=scenario.task,
        num_envs=scenario.eval_num_envs,
        max_steps=scenario.eval_max_steps,
        eval_episodes=scenario.eval_episodes,
        success_threshold=scenario.success_threshold,
        checkpoint_uri=checkpoint_uri,
        policy_image=policy_image,
        output_dataset=output_dataset,
    )
    monitor_osmo_workflow(
        request, eval_workflow, platform.repo_root, _ISAAC_INFERENCE_TASK_NAME, phase=f"eval ({label})"
    )
    log_e2e(f"Validating OSMO eval ({label}) workflow task success")
    assert_workflow_task_succeeded(eval_workflow, platform.repo_root, _ISAAC_INFERENCE_TASK_NAME)
    gate = assert_osmo_isaac_eval_gate(
        eval_workflow, platform.tracking, minimum_success_rate=scenario.success_threshold
    )
    assert_e2e_handle_complete(
        eval_workflow.handle,
        required_resources=("osmo_workflow", "mlflow_run"),
        required_logs=(_ISAAC_INFERENCE_TASK_NAME,),
        required_cleanups=("cancel_osmo_workflow", "stop_task_log_stream"),
        required_executions=("osmo_workflow",),
    )
    return gate


def _judge_rollouts(request: pytest.FixtureRequest, platform: E2EPlatform, rollouts: str, instruction: str) -> None:
    """Have the VLM judge score the published rollout videos and require a schema-valid verdict for each."""
    verdicts_dataset = e2e_name("rl-rollout-verdicts")
    request.addfinalizer(lambda: delete_osmo_dataset(platform.repo_root, verdicts_dataset))
    workflow = submit_osmo_judge(
        platform.repo_root,
        platform.profile,
        input_args=("--input-dataset", rollouts),
        output_dataset=verdicts_dataset,
        instruction=instruction,
    )
    monitor_osmo_workflow(
        request, workflow, platform.repo_root, JUDGE_TASK_NAME, phase="VLM judge (rollouts)", timeout_minutes=45
    )
    assert_workflow_task_succeeded(workflow, platform.repo_root, JUDGE_TASK_NAME)
    for verdict in read_judgments(platform.repo_root, verdicts_dataset):
        log_e2e(f"Rollout verdict {verdict.episode_id}: success={verdict.success} confidence={verdict.confidence:.3f}")


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_rl_lifecycle_e2e(
    request: pytest.FixtureRequest,
    e2e_platform: E2EPlatform,
    repo_root: Path,
) -> None:
    scenario = _SCENARIOS[e2e_platform.profile.name]
    log_e2e(f"Starting OSMO RL (Isaac Lab) lifecycle e2e test on the {e2e_platform.profile.name} profile")
    checkpoint_uri = resolve_osmo_isaac_eval_checkpoint_override()
    if checkpoint_uri is None:
        register_model_name = e2e_name("rl-e2e-osmo-model")
        correlation_id = e2e_name("osmo-rl-e2e")
        handle = E2EHandle()
        register_cleanup(
            request,
            handle,
            "remove_registered_model",
            lambda: e2e_platform.remove_registered_model(register_model_name),
        )
        register_cleanup(
            request,
            handle,
            "cancel_osmo_workflows_by_identifier",
            lambda: cancel_osmo_workflows_by_identifier(correlation_id, repo_root),
        )
        workflow = submit_osmo_training(
            repo_root,
            e2e_platform.profile,
            task=scenario.task,
            max_iterations=scenario.max_iterations,
            num_envs=scenario.num_envs,
            register_model_name=register_model_name,
            correlation_id=correlation_id,
            handle=handle,
        )
        monitor_osmo_workflow(request, workflow, repo_root, _ISAAC_TRAINING_TASK_NAME, phase="training")
        log_e2e("Validating OSMO training MLflow tracking")
        assert_osmo_workflow_has_mlflow_tracking(workflow, e2e_platform.tracking)
        log_e2e("Validating OSMO training workflow task success")
        assert_workflow_task_succeeded(workflow, repo_root, _ISAAC_TRAINING_TASK_NAME)
        provenance = parse_provenance_marker(
            fetch_workflow_task_logs(workflow, repo_root, _ISAAC_TRAINING_TASK_NAME),
            "RUNTIME_PROVENANCE=",
        )
        assert provenance["install_mode"] == "reinstall_from_frozen_lock"
        assert provenance["missing"] == []
        assert provenance["unresolved"] == []
        assert provenance["actual"] == provenance["expected"]
        expected_lock_sha256 = hashlib.sha256((repo_root / "training/rl/uv.lock").read_bytes()).hexdigest()
        assert provenance["lock_sha256"] == expected_lock_sha256
        expected_versions = provenance["expected"]
        assert isinstance(expected_versions, dict)
        for name, version in _declared_runtime_versions(repo_root).items():
            assert expected_versions[name] == version
        model = e2e_platform.resolve_registered_model(register_model_name)
        checkpoint_uri = model.uri
        workflow.handle.resource_identifiers["registered_model"] = f"{model.name}:{model.version}"
        assert_e2e_handle_complete(
            workflow.handle,
            required_resources=("osmo_workflow", "mlflow_run", "registered_model"),
            required_logs=(_ISAAC_TRAINING_TASK_NAME,),
            required_cleanups=(
                "remove_registered_model",
                "cancel_osmo_workflows_by_identifier",
                "cancel_osmo_workflow",
                "stop_task_log_stream",
            ),
            required_executions=("osmo_workflow",),
        )
    else:
        log_e2e(f"Using pre-configured eval checkpoint {checkpoint_uri} (training skipped)")

    rollouts = e2e_name("rl-rollouts") if scenario.judge_instruction else None
    if rollouts:
        request.addfinalizer(lambda: delete_osmo_dataset(repo_root, rollouts))
    _evaluate(
        request, e2e_platform, scenario, label="checkpoint", checkpoint_uri=checkpoint_uri, output_dataset=rollouts
    )
    if rollouts and scenario.judge_instruction:
        _judge_rollouts(request, e2e_platform, rollouts, scenario.judge_instruction)

    if scenario.evaluate_carrier:
        model = _registered_model(checkpoint_uri)
        if model is None or not isinstance(e2e_platform, LocalPlatform):
            log_e2e(f"Skipping the carrier evaluation: {checkpoint_uri} is not a registered model")
        else:
            image = e2e_platform.build_policy_carrier(
                model, base_image=common_default(repo_root, "DEFAULT_ISAAC_LAB_IMAGE")
            )
            request.addfinalizer(lambda: e2e_platform.remove_policy_carrier(image))
            _evaluate(request, e2e_platform, scenario, label="policy carrier", policy_image=image)
    log_e2e("OSMO RL lifecycle e2e test finished successfully")
