"""
End-to-end test for the Azure ML IL (LeRobot) end-to-end pipeline submission path.

Exercises ``submit-azureml-lerobot-pipeline.sh``, which submits an AzureML Pipeline job
(``type=pipeline``) chaining preprocess -> train -> evaluate, with an optional register
step. Distinct from the CommandJob path (``submit-azureml-lerobot-training.sh``):
per-step compute, per-step environment variables, and a single uri_folder data asset
input. The synthetic LeRobot dataset is registered as an AzureML data asset and fed to
both pipeline variants; ``continue_on_step_failure`` is false, so a COMPLETED parent job
means every step succeeded.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_il_pipeline.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLJob,
    AzureMLWorkspace,
    archive_all_model_versions,
    cancel_aml_job,
    cleanup_aml_job_and_model_versions,
    resolve_registered_model,
    submit_aml_lerobot_pipeline,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._lerobot_dataset import register_synthetic_lerobot_data_asset


@pytest.fixture(scope="module")
def pipeline_dataset_asset(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
) -> str:
    return register_synthetic_lerobot_data_asset(request, repo_root, aml_workspace)


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
@pytest.mark.parametrize("should_register", [False, True], ids=["without-register", "with-register"])
def test_aml_il_pipeline_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    pipeline_dataset_asset: str,
    should_register: bool,
) -> None:
    variant = "with register" if should_register else "without register"
    log_e2e(f"Starting AzureML IL (LeRobot) pipeline e2e test {variant}")
    register_model_name = e2e_name("il-pipeline-e2e-aml-model") if should_register else None
    registered_jobs: list[AzureMLJob] = []
    if register_model_name is not None:
        model_name = register_model_name

        def cleanup_registered_pipeline() -> None:
            if registered_jobs:
                cleanup_aml_job_and_model_versions(
                    registered_jobs[0],
                    repo_root,
                    aml_workspace,
                    model_name,
                )
            else:
                archive_all_model_versions(repo_root, aml_workspace, model_name)

        request.addfinalizer(cleanup_registered_pipeline)
    job = submit_aml_lerobot_pipeline(
        repo_root,
        aml_workspace,
        dataset_asset=pipeline_dataset_asset,
        dataset_repo_id="e2e/synthetic-pusht",
        policy_type="act",
        training_steps=10,
        save_freq=5,
        batch_size=8,
        eval_episodes=1,
        register_model_name=register_model_name,
    )
    if register_model_name is not None:
        registered_jobs.append(job)
    else:
        request.addfinalizer(lambda: cancel_aml_job(job, repo_root))

    log_e2e(f"Waiting for AzureML pipeline job {job.name} to start")
    wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML pipeline job {job.name} to complete")
    wait_until_aml_completed(job, repo_root, timeout_minutes=45, poll_interval_seconds=30)
    if register_model_name is not None:
        log_e2e("Validating AzureML LeRobot pipeline registered model")
        resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
    log_e2e(f"AzureML IL pipeline e2e test {variant} finished successfully")
