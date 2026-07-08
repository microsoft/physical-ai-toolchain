"""
End-to-end lifecycle test for the Azure ML IL (LeRobot/ACT) train -> eval path.

Stages a synthetic dataset, submits a real training job that registers its checkpoint
under a unique model name, validates the training outputs, resolves the concrete
registered model version (never ``latest``), then evaluates that model against the same
dataset via ``submit-azureml-lerobot-eval.sh``.

Set ``E2E_AML_LEROBOT_EVAL_MODEL`` (AzureML ``name:version``) to skip training and evaluate a
pre-existing policy — a fast inner loop while fixing the eval path.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_il_lifecycle.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    _AML_LEROBOT_EVAL_MODEL_ENV,
    AzureMLWorkspace,
    aml_lerobot_policy_source_from_model,
    archive_all_model_versions,
    assert_job_has_checkpoint,
    assert_job_snapshot_contains_only_training,
    cancel_aml_job,
    resolve_aml_lerobot_eval_policy_override,
    resolve_registered_model,
    submit_aml_lerobot_eval,
    submit_aml_lerobot_training,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._lerobot_dataset import stage_synthetic_lerobot_dataset
from tests.e2e._mlflow import (
    assert_aml_lerobot_eval_has_mlflow_tracking,
    assert_aml_lerobot_job_has_mlflow_tracking,
)


def test_resolve_aml_lerobot_eval_policy_override_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_AML_LEROBOT_EVAL_MODEL_ENV, "name:version")

    source = resolve_aml_lerobot_eval_policy_override()

    assert source is not None
    assert source.args == ("--from-aml-model", "--model-name", "name", "--model-version", "version")
    assert source.description == "AzureML model name:version"


def test_resolve_aml_lerobot_eval_policy_override_model_without_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_AML_LEROBOT_EVAL_MODEL_ENV, "name_only")

    with pytest.raises(pytest.skip.Exception):
        resolve_aml_lerobot_eval_policy_override()


def test_resolve_aml_lerobot_eval_policy_override_model_empty_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_AML_LEROBOT_EVAL_MODEL_ENV, "name:")

    with pytest.raises(pytest.skip.Exception):
        resolve_aml_lerobot_eval_policy_override()


def test_resolve_aml_lerobot_eval_policy_override_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_AML_LEROBOT_EVAL_MODEL_ENV, raising=False)

    source = resolve_aml_lerobot_eval_policy_override()

    assert source is None


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_il_lifecycle_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    log_e2e("Starting AzureML IL (LeRobot) lifecycle e2e test")
    policy_source = resolve_aml_lerobot_eval_policy_override()
    dataset = stage_synthetic_lerobot_dataset(request, repo_root, storage_account)
    if policy_source is None:
        register_model_name = e2e_name("il-e2e-aml-model")
        job = submit_aml_lerobot_training(
            repo_root,
            aml_workspace,
            blob_url=dataset.blob_url,
            policy_type="act",
            training_steps=10,
            save_freq=5,
            batch_size=8,
            log_freq=1,
            register_model_name=register_model_name,
        )
        request.addfinalizer(lambda: cancel_aml_job(job, repo_root))

        log_e2e(f"Waiting for AzureML LeRobot training job {job.name} to start")
        wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
        log_e2e(f"Waiting for AzureML LeRobot training job {job.name} to complete")
        wait_until_aml_completed(job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
        model = resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
        request.addfinalizer(lambda: archive_all_model_versions(repo_root, aml_workspace, register_model_name))
        policy_source = aml_lerobot_policy_source_from_model(model)
        log_e2e("Validating AzureML LeRobot uploaded code snapshot")
        assert_job_snapshot_contains_only_training(job, repo_root)
        log_e2e("Validating AzureML LeRobot training MLflow tracking")
        assert_aml_lerobot_job_has_mlflow_tracking(job, aml_workspace)
        log_e2e("Validating AzureML LeRobot checkpoint output")
        assert_job_has_checkpoint(job)
    else:
        log_e2e(f"Using pre-configured eval policy {policy_source.description} (training skipped)")

    eval_job = submit_aml_lerobot_eval(
        repo_root,
        aml_workspace,
        policy_source=policy_source,
        policy_type="act",
        eval_episodes=1,
        eval_batch_size=1,
        blob_storage_account=dataset.storage_account,
        blob_container=dataset.container,
        blob_prefix=dataset.prefix,
    )
    request.addfinalizer(lambda: cancel_aml_job(eval_job, repo_root))

    log_e2e(f"Waiting for AzureML LeRobot eval job {eval_job.name} to start")
    wait_until_aml_started(eval_job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML LeRobot eval job {eval_job.name} to complete")
    wait_until_aml_completed(eval_job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
    log_e2e("Validating AzureML LeRobot eval MLflow tracking")
    assert_aml_lerobot_eval_has_mlflow_tracking(eval_job, aml_workspace)
    log_e2e("AzureML LeRobot lifecycle e2e test finished successfully")
