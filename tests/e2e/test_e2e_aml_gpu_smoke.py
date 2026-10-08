"""
End-to-end test for the Azure ML GPU smoke job.

Runs ``training/smoke/scripts/submit-azureml-gpu-smoke.sh`` against the resolved workspace with a
unique job and model name. The script streams the job, downloads its checkpoints output, and exits
non-zero unless the job completed, its smoke summary passed, and at least one checkpoint exists. The
job's checks cover GPU access, a short training loop, MLflow metrics and artifacts, Azure Storage
uploads, and model registration. Finalizers cancel a still-running job and archive the test model.

The job requests the instance type in ``E2E_AML_INSTANCE_TYPE_GPU_SMOKE`` or
``E2E_AML_INSTANCE_TYPE``, read from the repository-root ``.env.local`` before the environment;
the script default applies otherwise.

```shell
E2E_ENVIRONMENT=<environment> uv run pytest -o addopts="" -vv -s -m e2e tests/e2e/test_e2e_aml_gpu_smoke.py
```
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.e2e._aml import (
    GPU_SMOKE_SCRIPT,
    AzureMLCompute,
    AzureMLJob,
    AzureMLWorkspace,
    archive_all_model_versions,
    cancel_aml_job,
    require_gpu_instance_type,
    submit_target_args,
)
from tests.e2e._common import e2e_name, log_e2e

_EXPERIMENT_NAME = "gpu-smoke-e2e"
_TIMEOUT_SECONDS = 90 * 60


def build_gpu_smoke_command(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    job_name: str,
    model_name: str,
    storage_account: str,
    instance_type: str | None,
    compute: str,
) -> list[str]:
    """Build the submit command for one verified GPU smoke run."""
    command = [
        str(repo_root / GPU_SMOKE_SCRIPT),
        *submit_target_args(aml_workspace, compute),
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
        compute="k8s-example",
    )

    assert command[0] == f"/repo/{GPU_SMOKE_SCRIPT}"
    assert command[1:9] == [
        "--subscription-id",
        "sub",
        "--resource-group",
        "rg",
        "--workspace-name",
        "mlw",
        "--compute",
        "k8s-example",
    ]
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
        compute="k8s-example",
    )

    assert command[-2:] == ["--instance-type", "gpu-example-1x"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="requires bash")
def test_the_compute_flag_wins_over_local_env(tmp_path: Path, repo_root: Path) -> None:
    for relative in (GPU_SMOKE_SCRIPT, "scripts/lib/common.sh", "scripts/lib/terraform-outputs.sh"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, tmp_path / relative)
    (tmp_path / ".env.local").write_text(
        "AZURE_SUBSCRIPTION_ID=sub\n"
        "AZURE_RESOURCE_GROUP=rg\n"
        "AZUREML_WORKSPACE_NAME=mlw\n"
        "AZURE_STORAGE_ACCOUNT_NAME=\n"
        "AZUREML_COMPUTE=k8s-from-file\n",
        encoding="utf-8",
    )
    env = {**os.environ, "AZUREML_COMPUTE": "k8s-from-bundle"}

    def preview(*extra: str) -> str:
        result = subprocess.run(
            ["bash", str(tmp_path / GPU_SMOKE_SCRIPT), "--config-preview", *extra],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    # The scripts load .env.local over exported variables, so the validated compute must travel as a flag.
    assert "k8s-from-file" in preview()
    flagged = preview("--compute", "k8s-validated")
    assert "k8s-validated" in flagged
    assert "k8s-from-file" not in flagged


@pytest.mark.e2e
def test_aml_gpu_smoke_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    aml_compute_target: AzureMLCompute,
    repo_root: Path,
    storage_account: str,
) -> None:
    instance_type = require_gpu_instance_type(
        aml_compute_target, repo_root, category="gpu-smoke", scripts=(GPU_SMOKE_SCRIPT,)
    )
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
        instance_type=instance_type,
        compute=aml_compute_target.name,
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
