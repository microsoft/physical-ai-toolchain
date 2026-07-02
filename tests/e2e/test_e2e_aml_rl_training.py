"""
End-to-end test for the Azure ML RL (Isaac/SKRL) training submission path.

Submits a real training job, waits for it to complete, and validates that the
expected outputs (code snapshot, MLflow tracking, checkpoint) are present.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_rl_training.py
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
    submit_aml_training,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import log_e2e
from tests.e2e._mlflow import assert_aml_job_has_mlflow_tracking


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_rl_training_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
) -> None:
    log_e2e("Starting AzureML RL e2e test")
    job = submit_aml_training(
        repo_root,
        aml_workspace,
        task="Isaac-Velocity-Rough-Anymal-C-v0",
        max_iterations=10,
        num_envs=64,
    )
    request.addfinalizer(lambda: cancel_aml_job(job, repo_root))

    log_e2e(f"Waiting for AzureML job {job.name} to start")
    wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML job {job.name} to complete")
    wait_until_aml_completed(job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
    log_e2e("Validating AzureML uploaded code snapshot")
    assert_job_snapshot_contains_only_training(job, repo_root)
    log_e2e("Validating AzureML MLflow tracking")
    assert_aml_job_has_mlflow_tracking(job, aml_workspace)
    log_e2e("Validating AzureML checkpoint output")
    assert_job_has_checkpoint(job)
    log_e2e("AzureML RL e2e test finished successfully")
