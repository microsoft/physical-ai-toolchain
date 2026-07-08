"""
End-to-end test for the Azure ML IL (LeRobot) end-to-end pipeline submission path.

Exercises ``submit-azureml-lerobot-pipeline.sh``, which submits an AzureML Pipeline job
(``type=pipeline``) chaining preprocess -> train -> evaluate. Distinct from the CommandJob
path (``submit-azureml-lerobot-training.sh``): per-step compute, per-step environment
variables, and a single uri_folder data asset input. The synthetic LeRobot dataset is
registered as an AzureML data asset and fed to the pipeline; ``continue_on_step_failure``
is false, so a COMPLETED parent job means all three steps succeeded.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_il_pipeline.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLWorkspace,
    cancel_aml_job,
    submit_aml_lerobot_pipeline,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import log_e2e
from tests.e2e._lerobot_dataset import register_synthetic_lerobot_data_asset


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_il_pipeline_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
) -> None:
    log_e2e("Starting AzureML IL (LeRobot) pipeline e2e test")
    dataset_asset = register_synthetic_lerobot_data_asset(request, repo_root, aml_workspace)
    job = submit_aml_lerobot_pipeline(
        repo_root,
        aml_workspace,
        dataset_asset=dataset_asset,
        dataset_repo_id="e2e/synthetic-pusht",
        policy_type="act",
        training_steps=10,
        save_freq=5,
        batch_size=8,
        eval_episodes=1,
    )
    request.addfinalizer(lambda: cancel_aml_job(job, repo_root))

    log_e2e(f"Waiting for AzureML pipeline job {job.name} to start")
    wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML pipeline job {job.name} to complete")
    # A COMPLETED parent pipeline (continue_on_step_failure=false) means preprocess, train,
    # and evaluate all succeeded end-to-end.
    wait_until_aml_completed(job, repo_root, timeout_minutes=45, poll_interval_seconds=30)
    log_e2e("AzureML IL pipeline e2e test finished successfully")
