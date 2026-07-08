"""
End-to-end lifecycle test for the Azure ML RL (Isaac/SKRL) train -> eval path.

Submits a real training job that registers its checkpoint under a unique model name,
waits for completion, validates the training outputs, resolves the concrete registered
model version (never ``latest``), then evaluates that model via
``submit-azureml-isaaclab-evaluation.sh``.

Set ``E2E_AML_ISAAC_EVAL_MODEL`` (AzureML ``name:version``) to skip training and evaluate
a pre-existing model — a fast inner loop while fixing the eval path.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_rl_lifecycle.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    _AML_ISAAC_EVAL_MODEL_ENV,
    AzureMLWorkspace,
    archive_all_model_versions,
    assert_job_has_checkpoint,
    assert_job_snapshot_contains_only_training,
    cancel_aml_job,
    resolve_aml_isaac_eval_model_override,
    resolve_registered_model,
    submit_aml_isaaclab_eval,
    submit_aml_training,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._mlflow import assert_aml_job_has_mlflow_tracking

_TASK = "Isaac-Velocity-Rough-Anymal-C-v0"


def test_resolve_aml_isaac_eval_model_override_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_AML_ISAAC_EVAL_MODEL_ENV, "name:version")

    model = resolve_aml_isaac_eval_model_override()

    assert model is not None
    assert model.name == "name"
    assert model.version == "version"


def test_resolve_aml_isaac_eval_model_override_model_without_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_AML_ISAAC_EVAL_MODEL_ENV, "name_only")

    with pytest.raises(pytest.skip.Exception):
        resolve_aml_isaac_eval_model_override()


def test_resolve_aml_isaac_eval_model_override_model_empty_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_AML_ISAAC_EVAL_MODEL_ENV, "name:")

    with pytest.raises(pytest.skip.Exception):
        resolve_aml_isaac_eval_model_override()


def test_resolve_aml_isaac_eval_model_override_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_AML_ISAAC_EVAL_MODEL_ENV, raising=False)

    model = resolve_aml_isaac_eval_model_override()

    assert model is None


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_rl_lifecycle_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
) -> None:
    log_e2e("Starting AzureML RL (Isaac Lab) lifecycle e2e test")
    model = resolve_aml_isaac_eval_model_override()
    if model is None:
        register_model_name = e2e_name("rl-e2e-aml-model")
        job = submit_aml_training(
            repo_root,
            aml_workspace,
            task=_TASK,
            max_iterations=10,
            num_envs=64,
            register_model_name=register_model_name,
        )
        request.addfinalizer(lambda: cancel_aml_job(job, repo_root))

        log_e2e(f"Waiting for AzureML training job {job.name} to start")
        wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
        log_e2e(f"Waiting for AzureML training job {job.name} to complete")
        wait_until_aml_completed(job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
        log_e2e("Validating AzureML uploaded code snapshot")
        assert_job_snapshot_contains_only_training(job, repo_root)
        log_e2e("Validating AzureML training MLflow tracking")
        assert_aml_job_has_mlflow_tracking(job, aml_workspace)
        log_e2e("Validating AzureML checkpoint output")
        assert_job_has_checkpoint(job)
        model = resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
        request.addfinalizer(lambda: archive_all_model_versions(repo_root, aml_workspace, register_model_name))
    else:
        log_e2e(f"Using pre-registered eval model {model.name}:{model.version} (training skipped)")

    eval_job = submit_aml_isaaclab_eval(
        repo_root,
        aml_workspace,
        model=model,
        task=_TASK,
        eval_episodes=2,
        num_envs=4,
    )
    request.addfinalizer(lambda: cancel_aml_job(eval_job, repo_root))

    log_e2e(f"Waiting for AzureML Isaac Lab eval job {eval_job.name} to start")
    wait_until_aml_started(eval_job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML Isaac Lab eval job {eval_job.name} to complete")
    wait_until_aml_completed(eval_job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
    log_e2e("AzureML RL lifecycle e2e test finished successfully")
