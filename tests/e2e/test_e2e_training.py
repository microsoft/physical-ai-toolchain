"""
End-to-end tests for training using AzureML and OSMO.
These tests submit actual training jobs to the respective orchestrators, wait for them to complete,
and validate that key expected outputs (e.g. checkpoints, MLflow tracking) are present.

To run these:

```shell
# All e2e tests
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_training.py

# Only AML e2e test
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_training.py::test_aml_rl_training_e2e

# Only OSMO e2e test
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_training.py::test_osmo_rl_training_e2e
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
from tests.e2e._mlflow import (
    assert_aml_job_has_mlflow_tracking,
    assert_osmo_workflow_has_mlflow_tracking,
)
from tests.e2e._osmo import (
    assert_workflow_task_succeeded,
    cancel_osmo_workflow,
    submit_osmo_training,
    wait_until_osmo_completed,
    wait_until_osmo_started,
)


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
    assert_job_has_checkpoint(job, repo_root)
    log_e2e("AzureML RL e2e test finished successfully")


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_rl_training_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
) -> None:
    log_e2e("Starting OSMO RL e2e test")
    workflow = submit_osmo_training(
        repo_root,
        task="Isaac-Velocity-Rough-Anymal-C-v0",
        max_iterations=10,
        num_envs=64,
    )
    request.addfinalizer(lambda: cancel_osmo_workflow(workflow, repo_root))

    log_e2e(f"Waiting for OSMO workflow {workflow.workflow_id} to start")
    wait_until_osmo_started(workflow, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for OSMO workflow {workflow.workflow_id} to complete")
    wait_until_osmo_completed(workflow, repo_root, timeout_minutes=30, poll_interval_seconds=30)
    log_e2e("Validating OSMO MLflow tracking")
    assert_osmo_workflow_has_mlflow_tracking(workflow, aml_workspace)
    log_e2e("Validating OSMO workflow task success")
    assert_workflow_task_succeeded(workflow, repo_root, "isaac-training")
    log_e2e("OSMO RL e2e test finished successfully")
