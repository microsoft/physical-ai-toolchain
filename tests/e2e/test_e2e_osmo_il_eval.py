"""
End-to-end test for the OSMO IL (LeRobot/ACT) evaluation submission path.

Submits a real OSMO workflow, waits for it to complete, and validates that
MLflow tracking and the workflow task succeeded. The dataset is generated
synthetically and staged to blob; the policy defaults to a base ACT policy
minted in-container from LeRobot's built-in architecture (no external policy).
Override the eval policy source via environment (see tests/e2e/_osmo.py).

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_il_eval.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import AzureMLWorkspace
from tests.e2e._common import log_e2e
from tests.e2e._lerobot_dataset import stage_synthetic_lerobot_dataset
from tests.e2e._mlflow import assert_osmo_lerobot_eval_has_mlflow_tracking
from tests.e2e._osmo import (
    assert_workflow_task_succeeded,
    cancel_osmo_workflow,
    start_task_pod_log_stream,
    submit_osmo_lerobot_eval,
    wait_until_osmo_completed,
    wait_until_osmo_started,
)

_LEROBOT_EVAL_TASK_NAME = "lerobot-eval"


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_il_eval_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    log_e2e("Starting OSMO IL (LeRobot) eval e2e test")
    dataset = stage_synthetic_lerobot_dataset(request, repo_root, storage_account)
    workflow = submit_osmo_lerobot_eval(
        repo_root,
        aml_workspace,
        policy_type="act",
        eval_episodes=1,
        eval_batch_size=1,
        blob_storage_account=dataset.storage_account,
        blob_container=dataset.container,
        blob_prefix=dataset.prefix,
    )
    request.addfinalizer(lambda: cancel_osmo_workflow(workflow, repo_root))

    log_stream = start_task_pod_log_stream(workflow, repo_root, _LEROBOT_EVAL_TASK_NAME)
    request.addfinalizer(log_stream.stop)

    log_e2e(f"Waiting for OSMO LeRobot eval workflow {workflow.workflow_id} to start")
    wait_until_osmo_started(workflow, repo_root)
    log_e2e(f"Waiting for OSMO LeRobot eval workflow {workflow.workflow_id} to complete")
    wait_until_osmo_completed(workflow, repo_root, timeout_minutes=30)
    log_stream.stop()
    log_e2e("Validating OSMO LeRobot eval MLflow tracking")
    assert_osmo_lerobot_eval_has_mlflow_tracking(workflow, aml_workspace)
    log_e2e("Validating OSMO LeRobot eval workflow task success")
    assert_workflow_task_succeeded(workflow, repo_root, _LEROBOT_EVAL_TASK_NAME)
    log_e2e("OSMO LeRobot eval e2e test finished successfully")
