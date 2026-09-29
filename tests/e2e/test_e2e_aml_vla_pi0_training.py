"""
End-to-end lifecycle test for Azure ML VLA pi0 training and evaluation.

Stages a synthetic LeRobot dataset, submits a short pi0 training job, and
validates its code snapshot, MLflow tracking, checkpoint output, registered
model, and schema-v1 evaluation artifacts. The gated PaliGemma backbone requires
``HF_TOKEN``.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_vla_pi0_training.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLWorkspace,
    aml_lerobot_policy_source_from_model,
    assert_aml_lerobot_eval_artifact_contract,
    assert_job_has_checkpoint,
    assert_job_snapshot_contains_only_training,
    cancel_aml_job,
    cleanup_aml_job_and_model_versions,
    resolve_registered_model,
    submit_aml_lerobot_eval,
    submit_aml_vla_pi0_training,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._lerobot_dataset import stage_synthetic_lerobot_dataset
from tests.e2e._mlflow import (
    assert_aml_lerobot_eval_has_mlflow_tracking,
    assert_aml_lerobot_job_has_mlflow_tracking,
)


@pytest.mark.e2e
@pytest.mark.requires_hf_token
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_vla_pi0_lifecycle_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    log_e2e("Starting AzureML VLA pi0 training e2e test")
    dataset = stage_synthetic_lerobot_dataset(
        request,
        repo_root,
        storage_account,
        container="ml-workspace",
    )
    register_model_name = e2e_name("vla-pi0-e2e-aml-model")
    job = submit_aml_vla_pi0_training(
        repo_root,
        aml_workspace,
        blob_url=dataset.blob_url,
        training_steps=2,
        save_freq=1,
        batch_size=1,
        log_freq=1,
        register_model_name=register_model_name,
    )
    request.addfinalizer(lambda: cleanup_aml_job_and_model_versions(job, repo_root, aml_workspace, register_model_name))

    log_e2e(f"Waiting for AzureML VLA pi0 training job {job.name} to start")
    wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML VLA pi0 training job {job.name} to complete")
    wait_until_aml_completed(job, repo_root, timeout_minutes=45, poll_interval_seconds=30)

    log_e2e("Validating AzureML VLA pi0 registered model")
    model = resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
    log_e2e("Validating AzureML VLA pi0 uploaded code snapshot")
    assert_job_snapshot_contains_only_training(job, repo_root)
    log_e2e("Validating AzureML VLA pi0 MLflow tracking")
    assert_aml_lerobot_job_has_mlflow_tracking(job, aml_workspace)
    log_e2e("Validating AzureML VLA pi0 checkpoint output")
    assert_job_has_checkpoint(job)

    eval_job = submit_aml_lerobot_eval(
        repo_root,
        aml_workspace,
        policy_source=aml_lerobot_policy_source_from_model(model),
        policy_type="pi0",
        eval_episodes=1,
        eval_batch_size=1,
        blob_storage_account=dataset.storage_account,
        blob_container=dataset.container,
        blob_prefix=dataset.prefix,
    )
    request.addfinalizer(lambda: cancel_aml_job(eval_job, repo_root))

    log_e2e(f"Waiting for AzureML VLA pi0 eval job {eval_job.name} to start")
    wait_until_aml_started(eval_job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML VLA pi0 eval job {eval_job.name} to complete")
    wait_until_aml_completed(eval_job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
    log_e2e("Validating AzureML VLA pi0 eval artifact contract")
    assert_aml_lerobot_eval_artifact_contract(eval_job, eval_episodes=1)
    log_e2e("Validating AzureML VLA pi0 eval MLflow tracking")
    assert_aml_lerobot_eval_has_mlflow_tracking(eval_job, aml_workspace)
    log_e2e("AzureML VLA pi0 lifecycle e2e test finished successfully")
