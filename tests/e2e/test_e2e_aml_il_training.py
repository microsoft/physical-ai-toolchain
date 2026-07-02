"""
End-to-end test for the Azure ML IL (LeRobot/ACT) training submission path.

Submits a real training job, waits for it to complete, and validates that the
expected outputs (code snapshot, MLflow tracking, checkpoint) are present.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_il_training.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLWorkspace,
    assert_job_has_checkpoint,
    assert_job_snapshot_contains_only_training,
    cancel_aml_job,
    submit_aml_lerobot_training,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import log_e2e
from tests.e2e._lerobot_dataset import stage_synthetic_lerobot_dataset
from tests.e2e._mlflow import assert_aml_lerobot_job_has_mlflow_tracking


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_il_training_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    log_e2e("Starting AzureML IL (LeRobot) e2e test")
    dataset = stage_synthetic_lerobot_dataset(request, repo_root, storage_account)
    job = submit_aml_lerobot_training(
        repo_root,
        aml_workspace,
        blob_url=dataset.blob_url,
        policy_type="act",
        training_steps=10,
        save_freq=5,
        batch_size=8,
        log_freq=1,
    )
    request.addfinalizer(lambda: cancel_aml_job(job, repo_root))

    log_e2e(f"Waiting for AzureML LeRobot job {job.name} to start")
    wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML LeRobot job {job.name} to complete")
    wait_until_aml_completed(job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
    log_e2e("Validating AzureML LeRobot uploaded code snapshot")
    assert_job_snapshot_contains_only_training(job, repo_root)
    log_e2e("Validating AzureML LeRobot MLflow tracking")
    assert_aml_lerobot_job_has_mlflow_tracking(job, aml_workspace)
    log_e2e("Validating AzureML LeRobot checkpoint output")
    assert_job_has_checkpoint(job)
    log_e2e("AzureML LeRobot e2e test finished successfully")
