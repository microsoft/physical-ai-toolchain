"""
End-to-end test for the CPU-only Azure ML to OSMO proxy path.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_osmo_proxy.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLJob,
    AzureMLWorkspace,
    _aml_job_from_submission,
    archive_aml_data_asset,
    assert_aml_data_asset_exists,
    cancel_aml_job,
    fetch_aml_job_logs,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import delete_blob_prefix, e2e_name, format_command_failure, log_e2e, run_command
from tests.e2e._mlflow import (
    assert_aml_osmo_proxy_has_mlflow_tracking,
    delete_mlflow_experiment,
    delete_mlflow_run,
)
from tests.e2e._osmo import OSMOWorkflow, assert_workflow_task_succeeded, cancel_osmo_workflow

_TASK_NAME = "write-output"
_CONTAINER = "osmo"


def _submit_proxy(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    job_name: str,
    experiment_name: str,
    output_url: str,
) -> AzureMLJob:
    result = run_command(
        [
            str(repo_root / "workflows/azureml/submit-osmo-proxy-job.sh"),
            "--job-name",
            job_name,
            "--experiment-name",
            experiment_name,
            "--output-url",
            output_url,
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AML-to-OSMO proxy submission failed\n\n{format_command_failure(result)}")
    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AML-to-OSMO proxy")


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_aml_osmo_proxy_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    job_name = e2e_name("osmo-proxy-e2e-aml")
    experiment_name = e2e_name("osmo-proxy-e2e")
    request.addfinalizer(lambda: delete_mlflow_experiment(aml_workspace, experiment_name))
    prefix = f"e2e/proxy/{job_name}"
    output_url = f"azure://{storage_account}/{_CONTAINER}/{prefix}/"
    request.addfinalizer(
        lambda: delete_blob_prefix(
            repo_root,
            storage_account,
            _CONTAINER,
            prefix,
            description="AML-to-OSMO proxy output",
        )
    )

    job = _submit_proxy(
        repo_root,
        aml_workspace,
        job_name=job_name,
        experiment_name=experiment_name,
        output_url=output_url,
    )
    request.addfinalizer(lambda: cancel_aml_job(job, repo_root))

    wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    wait_until_aml_completed(job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
    logs = fetch_aml_job_logs(job, repo_root)
    if "Done. OSMO workflow" not in logs:
        raise AssertionError("AzureML proxy logs did not contain the OSMO completion marker")
    workflow_id, run_id = assert_aml_osmo_proxy_has_mlflow_tracking(job, aml_workspace)
    request.addfinalizer(lambda: delete_mlflow_run(aml_workspace, run_id))

    workflow = OSMOWorkflow(
        workflow_id=workflow_id,
        workflow_name=workflow_id,
        experiment_name=experiment_name,
        correlation_id=job.name,
        handle=job.handle,
        is_terminal=True,
        terminal_status="COMPLETED",
    )
    request.addfinalizer(lambda: cancel_osmo_workflow(workflow, repo_root))
    assert_workflow_task_succeeded(workflow, repo_root, _TASK_NAME)

    result = run_command(
        [
            "az",
            "storage",
            "blob",
            "exists",
            "--account-name",
            storage_account,
            "--container-name",
            _CONTAINER,
            "--name",
            f"{prefix}/result.txt",
            "--auth-mode",
            "login",
            "--query",
            "exists",
            "-o",
            "tsv",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0 or result.stdout.strip().lower() != "true":
        raise AssertionError(f"Proxy durable output was not found\n\n{format_command_failure(result)}")

    asset_name = f"osmo-{workflow_id}-output-0"
    request.addfinalizer(lambda: archive_aml_data_asset(repo_root, aml_workspace, asset_name))
    expected_asset_path = f"abfss://{_CONTAINER}@{storage_account}.dfs.core.windows.net/{prefix}/"
    assert_aml_data_asset_exists(
        repo_root,
        aml_workspace,
        asset_name=asset_name,
        expected_path=expected_asset_path,
    )
    log_e2e("AML-to-OSMO proxy e2e test finished successfully")
