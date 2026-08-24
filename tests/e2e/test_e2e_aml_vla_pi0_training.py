"""
End-to-end test for Azure ML VLA pi0 training.

Stages a synthetic LeRobot dataset, submits a short pi0 training job, and
validates its code snapshot, MLflow tracking, checkpoint output, and registered
model. The gated PaliGemma backbone requires ``HF_TOKEN``.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_vla_pi0_training.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLWorkspace,
    archive_all_model_versions,
    assert_job_has_checkpoint,
    assert_job_snapshot_contains_only_training,
    cancel_aml_job,
    resolve_registered_model,
    submit_aml_vla_pi0_training,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import e2e_name, env_value, log_e2e
from tests.e2e._lerobot_dataset import stage_synthetic_lerobot_dataset
from tests.e2e._mlflow import assert_aml_lerobot_job_has_mlflow_tracking


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_vla_pi0_training_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    hf_token = env_value("HF_TOKEN")
    if hf_token is None:
        pytest.skip("HF_TOKEN is required to download the gated PaliGemma backbone")

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
        register_model_name=register_model_name,
    )
    request.addfinalizer(lambda: cancel_aml_job(job, repo_root))
    request.addfinalizer(lambda: archive_all_model_versions(repo_root, aml_workspace, register_model_name))

    log_e2e(f"Waiting for AzureML VLA pi0 training job {job.name} to start")
    wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML VLA pi0 training job {job.name} to complete")
    wait_until_aml_completed(job, repo_root, timeout_minutes=45, poll_interval_seconds=30)

    log_e2e("Validating AzureML VLA pi0 registered model")
    resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
    log_e2e("Validating AzureML VLA pi0 uploaded code snapshot")
    assert_job_snapshot_contains_only_training(job, repo_root)
    log_e2e("Validating AzureML VLA pi0 MLflow tracking")
    assert_aml_lerobot_job_has_mlflow_tracking(job, aml_workspace)
    log_e2e("Validating AzureML VLA pi0 checkpoint output")
    assert_job_has_checkpoint(job)
    log_e2e("AzureML VLA pi0 training e2e test finished successfully")
