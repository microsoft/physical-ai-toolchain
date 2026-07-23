"""
End-to-end test for the OSMO RL (Isaac/SKRL) dataset-injection training submission path.

Exercises ``submit-osmo-dataset-training.sh``, which delivers the training code as a
versioned OSMO dataset via ``localpath`` rather than the base64 archive used by
``submit-osmo-training.sh``. The training itself is identical to the archive path (whose
MLflow tracking is covered by ``test_e2e_osmo_rl_lifecycle``), so this test verifies the
distinct delivery mechanism by asserting the workflow task ran to success.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_rl_dataset_training.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._common import log_e2e
from tests.e2e._osmo import (
    assert_workflow_task_succeeded,
    monitor_osmo_workflow,
    submit_osmo_dataset_training,
)

_ISAAC_TASK_NAME = "isaac-training"


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_rl_dataset_training_e2e(
    request: pytest.FixtureRequest,
    repo_root: Path,
) -> None:
    log_e2e("Starting OSMO RL dataset-injection e2e test")
    workflow = submit_osmo_dataset_training(
        repo_root,
        task="Isaac-Velocity-Rough-Anymal-C-v0",
        max_iterations=10,
        num_envs=64,
    )
    monitor_osmo_workflow(request, workflow, repo_root, _ISAAC_TASK_NAME)
    # MLflow tracking for the identical training path is covered by test_e2e_osmo_rl_lifecycle;
    # the dataset-injection script exposes no --correlation-id, so success is verified via task status.
    log_e2e("Validating OSMO dataset-injection workflow task success")
    assert_workflow_task_succeeded(workflow, repo_root, _ISAAC_TASK_NAME)
    log_e2e("OSMO RL dataset-injection e2e test finished successfully")
