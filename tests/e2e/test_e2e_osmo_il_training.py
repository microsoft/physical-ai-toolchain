"""
End-to-end test for the OSMO IL (LeRobot/ACT) training submission path.

Submits a real OSMO workflow, waits for it to complete, and validates that
MLflow tracking and the workflow task succeeded.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_il_training.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import AzureMLWorkspace
from tests.e2e._common import log_e2e
from tests.e2e._lerobot_dataset import stage_synthetic_lerobot_dataset
from tests.e2e._mlflow import assert_osmo_lerobot_training_has_mlflow_tracking
from tests.e2e._osmo import (
    assert_workflow_task_succeeded,
    cancel_osmo_workflow,
    start_task_pod_log_stream,
    submit_osmo_lerobot_training,
    wait_until_osmo_completed,
    wait_until_osmo_started,
)

_LEROBOT_TASK_NAME = "lerobot-train"


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_il_training_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    log_e2e("Starting OSMO IL (LeRobot) training e2e test")
    dataset = stage_synthetic_lerobot_dataset(request, repo_root, storage_account)
    workflow = submit_osmo_lerobot_training(
        repo_root,
        aml_workspace,
        blob_url=dataset.blob_url,
        policy_type="act",
        training_steps=10,
        save_freq=5,
        batch_size=8,
        learning_rate="1e-4",
        log_freq=1,
    )
    request.addfinalizer(lambda: cancel_osmo_workflow(workflow, repo_root))

    log_stream = start_task_pod_log_stream(workflow, repo_root, _LEROBOT_TASK_NAME)
    request.addfinalizer(log_stream.stop)

    log_e2e(f"Waiting for OSMO LeRobot workflow {workflow.workflow_id} to start")
    wait_until_osmo_started(workflow, repo_root)
    log_e2e(f"Waiting for OSMO LeRobot workflow {workflow.workflow_id} to complete")
    wait_until_osmo_completed(workflow, repo_root, timeout_minutes=30)
    log_stream.stop()
    log_e2e("Validating OSMO LeRobot training MLflow tracking")
    assert_osmo_lerobot_training_has_mlflow_tracking(workflow, aml_workspace)
    log_e2e("Validating OSMO LeRobot training workflow task success")
    assert_workflow_task_succeeded(workflow, repo_root, _LEROBOT_TASK_NAME)
    log_e2e("OSMO LeRobot training e2e test finished successfully")
