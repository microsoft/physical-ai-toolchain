"""
End-to-end test for the CPU-only Azure ML to OSMO proxy path.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_osmo_proxy.py
```
"""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e import _aml
from tests.e2e._aml import (
    AzureMLCompute,
    AzureMLJob,
    AzureMLWorkspace,
    _aml_job_from_submission,
    archive_aml_data_asset,
    assert_aml_data_asset_exists,
    cancel_aml_job,
    fetch_aml_job_logs,
    submit_target_args,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import (
    E2EHandle,
    assert_e2e_handle_complete,
    delete_blob_prefix,
    e2e_name,
    format_command_failure,
    log_e2e,
    parse_provenance_marker,
    register_cleanup,
    run_command,
)
from tests.e2e._mlflow import (
    assert_aml_osmo_proxy_has_mlflow_tracking,
    delete_mlflow_experiment,
    delete_mlflow_run,
)
from tests.e2e._osmo import OSMOWorkflow, assert_workflow_task_succeeded, cancel_osmo_workflow

_TASK_NAME = "write-output"
_CONTAINER = "osmo"


def test_archive_missing_data_asset_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
) -> None:
    commands: list[list[str]] = []

    def fake_run_command(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=3,
            stdout="",
            stderr="ERROR: (UserError) missing container was not found.",
        )

    monkeypatch.setattr(_aml, "run_command", fake_run_command)

    archive_aml_data_asset(repo_root, AzureMLWorkspace("subscription", "resource-group", "workspace"), "missing")

    assert len(commands) == 1


def _validate_output_urls(repo_root: Path, output_urls: list[str]) -> list[str]:
    module = runpy.run_path(str(repo_root / "workflows/azureml/osmo-proxy/osmo_proxy.py"))
    validator = module["_validate_output_urls"]
    assert callable(validator)
    return validator(output_urls)


def test_proxy_output_urls_require_trusted_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
) -> None:
    monkeypatch.delenv("OSMO_OUTPUT_STORAGE_ACCOUNT", raising=False)
    monkeypatch.delenv("OSMO_OUTPUT_CONTAINER", raising=False)

    with pytest.raises(ValueError, match="boundaries are required"):
        _validate_output_urls(repo_root, ["azure://trusted/osmo/path/"])


def test_proxy_output_urls_reject_boundary_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
) -> None:
    monkeypatch.setenv("OSMO_OUTPUT_STORAGE_ACCOUNT", "trusted")
    monkeypatch.setenv("OSMO_OUTPUT_CONTAINER", _CONTAINER)

    with pytest.raises(ValueError, match="outside the expected storage account or container"):
        _validate_output_urls(repo_root, ["azure://attacker/osmo/path/"])

    with pytest.raises(ValueError, match="outside the expected storage account or container"):
        _validate_output_urls(repo_root, ["azure://trusted/other/path/"])


_PROXY_SCRIPT = "workflows/azureml/submit-osmo-proxy-job.sh"


def test_submit_proxy_names_the_validated_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="stopped by the test")

    monkeypatch.setattr(sys.modules[__name__], "run_command", fake_run)
    workspace = AzureMLWorkspace(subscription_id="sub", resource_group="rg", workspace_name="mlw")

    with pytest.raises(AssertionError, match="proxy submission failed"):
        _submit_proxy(
            tmp_path,
            workspace,
            job_name="job",
            experiment_name="experiment",
            output_url="azure://account/osmo/prefix/",
            handle=E2EHandle(),
            compute="k8s-validated",
        )

    command = commands[0]
    for flag, value in (
        ("--subscription-id", "sub"),
        ("--resource-group", "rg"),
        ("--workspace-name", "mlw"),
        ("--compute", "k8s-validated"),
    ):
        assert command[command.index(flag) + 1] == value


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("jq") is None, reason="requires bash and jq")
def test_proxy_flags_win_over_local_env(tmp_path: Path, repo_root: Path) -> None:
    for relative in (_PROXY_SCRIPT, "scripts/lib/common.sh", "scripts/lib/terraform-outputs.sh"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, tmp_path / relative)
    (tmp_path / ".env.local").write_text(
        "AZURE_SUBSCRIPTION_ID=sub-from-file\n"
        "AZURE_RESOURCE_GROUP=rg-from-file\n"
        "AZUREML_WORKSPACE_NAME=mlw-from-file\n"
        "AZURE_STORAGE_ACCOUNT_NAME=account\n"
        "AZUREML_COMPUTE_NAME=k8s-from-file\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "az").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (bin_dir / "az").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    for name in ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZUREML_WORKSPACE_NAME", "AZUREML_COMPUTE_NAME"):
        env.pop(name, None)

    result = subprocess.run(
        [
            "bash",
            str(tmp_path / _PROXY_SCRIPT),
            "--config-preview",
            "--subscription-id",
            "sub-from-flag",
            "--resource-group",
            "rg-from-flag",
            "--workspace-name",
            "mlw-from-flag",
            "--compute",
            "k8s-from-flag",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for value in ("sub-from-flag", "rg-from-flag", "mlw-from-flag", "k8s-from-flag"):
        assert value in result.stdout
    assert "from-file" not in result.stdout


def _submit_proxy(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    job_name: str,
    experiment_name: str,
    output_url: str,
    handle: E2EHandle,
    compute: str,
) -> AzureMLJob:
    result = run_command(
        [
            str(repo_root / _PROXY_SCRIPT),
            "--job-name",
            job_name,
            "--experiment-name",
            experiment_name,
            "--output-url",
            output_url,
            *submit_target_args(aml_workspace, compute),
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AML-to-OSMO proxy submission failed\n\n{format_command_failure(result)}")
    return _aml_job_from_submission(
        result,
        aml_workspace,
        experiment_name,
        "AML-to-OSMO proxy",
        handle=handle,
        expected_job_name=job_name,
    )


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_aml_osmo_proxy_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    aml_compute_target: AzureMLCompute,
    repo_root: Path,
    storage_account: str,
) -> None:
    job_name = e2e_name("osmo-proxy-e2e-aml")
    experiment_name = e2e_name("osmo-proxy-e2e")
    prefix = f"e2e/proxy/{job_name}"
    output_url = f"azure://{storage_account}/{_CONTAINER}/{prefix}/"
    handle = E2EHandle()
    register_cleanup(
        request,
        handle,
        "delete_mlflow_experiment",
        lambda: delete_mlflow_experiment(aml_workspace, experiment_name),
    )
    register_cleanup(
        request,
        handle,
        "delete_blob_prefix",
        lambda: delete_blob_prefix(
            repo_root,
            storage_account,
            _CONTAINER,
            prefix,
            description="AML-to-OSMO proxy output",
        ),
    )
    asset_name = f"osmo-{job_name}-output-0"
    register_cleanup(
        request,
        handle,
        "archive_aml_data_asset",
        lambda: archive_aml_data_asset(repo_root, aml_workspace, asset_name),
    )
    job = AzureMLJob(
        name=job_name,
        workspace=aml_workspace,
        experiment_name=experiment_name,
        handle=handle,
    )
    register_cleanup(request, handle, "cancel_aml_job", lambda: cancel_aml_job(job, repo_root))
    job = _submit_proxy(
        repo_root,
        aml_workspace,
        job_name=job_name,
        experiment_name=experiment_name,
        output_url=output_url,
        handle=handle,
        compute=aml_compute_target.name,
    )

    wait_until_aml_started(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    wait_until_aml_completed(job, repo_root, timeout_minutes=30, poll_interval_seconds=30)
    logs = fetch_aml_job_logs(job, repo_root)
    if "Done. OSMO workflow" not in logs:
        raise AssertionError("AzureML proxy logs did not contain the OSMO completion marker")
    workflow_id, run_id = assert_aml_osmo_proxy_has_mlflow_tracking(job, aml_workspace)
    register_cleanup(
        request,
        job.handle,
        "delete_mlflow_run",
        lambda: delete_mlflow_run(aml_workspace, run_id),
    )

    workflow = OSMOWorkflow(
        workflow_id=workflow_id,
        workflow_name=workflow_id,
        experiment_name=experiment_name,
        correlation_id=job.name,
        handle=job.handle,
        is_terminal=True,
        terminal_status="COMPLETED",
    )
    execution_evidence = parse_provenance_marker(logs, "PROXY_EXECUTION_EVIDENCE=")
    assert execution_evidence["workflow_id"] == workflow_id
    assert execution_evidence["status"] == "COMPLETED"
    assert execution_evidence["attempts"] == ["initial"]
    assert execution_evidence["retry_classification"] == "none"
    attempts = execution_evidence["attempts"]
    assert isinstance(attempts, list)
    job.handle.attempts["osmo_workflow"] = [str(value) for value in attempts]
    job.handle.retry_classifications["osmo_workflow"] = str(execution_evidence["retry_classification"])
    job.handle.terminal_states["osmo_workflow"] = str(execution_evidence["status"])
    register_cleanup(
        request,
        job.handle,
        "cancel_osmo_workflow",
        lambda: cancel_osmo_workflow(workflow, repo_root),
    )
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

    expected_asset_path = f"abfss://{_CONTAINER}@{storage_account}.dfs.core.windows.net/{prefix}/"
    assert_aml_data_asset_exists(
        repo_root,
        aml_workspace,
        asset_name=asset_name,
        expected_path=expected_asset_path,
    )
    job.handle.resource_identifiers["azureml_data_asset"] = asset_name
    assert_e2e_handle_complete(
        job.handle,
        required_resources=("azureml_job", "osmo_workflow", "mlflow_run", "azureml_data_asset"),
        required_logs=("azureml_job",),
        required_cleanups=(
            "cancel_aml_job",
            "delete_mlflow_experiment",
            "delete_blob_prefix",
            "archive_aml_data_asset",
            "delete_mlflow_run",
            "cancel_osmo_workflow",
        ),
        required_executions=("azureml_job", "osmo_workflow"),
    )
    log_e2e("AML-to-OSMO proxy e2e test finished successfully")
