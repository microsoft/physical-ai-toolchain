"""
End-to-end test for the Azure ML GPU smoke job.

Runs ``training/smoke/scripts/submit-azureml-gpu-smoke.sh`` against the resolved workspace with a
unique job and model name. The script streams the job, downloads its checkpoints output, and exits
non-zero unless the job completed, its smoke summary passed, and at least one checkpoint exists. The
job's checks cover GPU access, a short training loop, MLflow metrics and artifacts, Azure Storage
uploads, and model registration. Finalizers cancel a still-running job and archive the test model.

Set ``E2E_AML_INSTANCE_TYPE`` to target a specific GPU instance type; the script default applies
otherwise.

```shell
E2E_ENVIRONMENT=<environment> uv run pytest -o addopts="" -vv -s -m e2e tests/e2e/test_e2e_aml_gpu_smoke.py
```
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLJob,
    AzureMLWorkspace,
    _submit_workspace_args,
    archive_all_model_versions,
    cancel_aml_job,
)
from tests.e2e._common import e2e_name, env_value, log_e2e

_SUBMIT_SCRIPT = "training/smoke/scripts/submit-azureml-gpu-smoke.sh"
_EXPERIMENT_NAME = "gpu-smoke-e2e"
_INSTANCE_TYPE_ENV = "E2E_AML_INSTANCE_TYPE"
_TIMEOUT_SECONDS = 90 * 60


def build_gpu_smoke_command(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    job_name: str,
    model_name: str,
    storage_account: str,
    instance_type: str | None,
) -> list[str]:
    """Build the submit command for one verified GPU smoke run."""
    command = [
        str(repo_root / _SUBMIT_SCRIPT),
        *_submit_workspace_args(aml_workspace),
        "--job-name",
        job_name,
        "--model-name",
        model_name,
        "--experiment-name",
        _EXPERIMENT_NAME,
        "--storage-account",
        storage_account,
        "--stream",
    ]
    if instance_type:
        command += ["--instance-type", instance_type]
    return command


def test_build_gpu_smoke_command_targets_the_workspace_and_verifies() -> None:
    workspace = AzureMLWorkspace(subscription_id="sub", resource_group="rg", workspace_name="mlw")

    command = build_gpu_smoke_command(
        Path("/repo"),
        workspace,
        job_name="gpu-smoke-e2e-1",
        model_name="gpu-smoke-e2e-model-1",
        storage_account="example-storage",
        instance_type=None,
    )

    assert command[0] == f"/repo/{_SUBMIT_SCRIPT}"
    assert command[1:7] == ["--subscription-id", "sub", "--resource-group", "rg", "--workspace-name", "mlw"]
    assert command[command.index("--job-name") + 1] == "gpu-smoke-e2e-1"
    assert command[command.index("--model-name") + 1] == "gpu-smoke-e2e-model-1"
    assert command[command.index("--storage-account") + 1] == "example-storage"
    assert "--stream" in command
    assert "--instance-type" not in command
    assert "--skip-register-model" not in command


def test_build_gpu_smoke_command_honors_an_instance_type() -> None:
    workspace = AzureMLWorkspace(subscription_id="sub", resource_group="rg", workspace_name="mlw")

    command = build_gpu_smoke_command(
        Path("/repo"),
        workspace,
        job_name="job",
        model_name="model",
        storage_account="st",
        instance_type="gpu-example-1x",
    )

    assert command[-2:] == ["--instance-type", "gpu-example-1x"]


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_gpu_smoke_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    job_name = e2e_name("gpu-smoke-e2e")
    model_name = e2e_name("gpu-smoke-e2e-model")
    job = AzureMLJob(name=job_name, workspace=aml_workspace, experiment_name=_EXPERIMENT_NAME)
    request.addfinalizer(lambda: archive_all_model_versions(repo_root, aml_workspace, model_name))
    request.addfinalizer(lambda: cancel_aml_job(job, repo_root))

    command = build_gpu_smoke_command(
        repo_root,
        aml_workspace,
        job_name=job_name,
        model_name=model_name,
        storage_account=storage_account,
        instance_type=env_value(_INSTANCE_TYPE_ENV),
    )
    log_e2e(f"Submitting and streaming AzureML GPU smoke job {job_name}")
    try:
        result = subprocess.run(command, cwd=repo_root, check=False, timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pytest.fail(f"AzureML GPU smoke job {job_name} did not finish within {_TIMEOUT_SECONDS // 60} minutes")

    assert result.returncode == 0, f"AzureML GPU smoke job {job_name} failed verification; see the output above"
    job.is_terminal = True
    job.terminal_status = "Completed"
    log_e2e("AzureML GPU smoke e2e test finished successfully")
