from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._aml import AzureMLWorkspace
from tests.e2e._common import run_command

AML_COMPUTE_NAME_MAX_LENGTH = 16
TFVARS_FALLBACK_OUTPUT_KEYS = ("resource_group", "azureml_workspace", "aks_cluster")


@dataclass(frozen=True)
class TerraformOutputs:
    values: dict[str, Any]

    def try_key_value(self, key: str) -> str | None:
        value = self.values.get(key)
        if not isinstance(value, dict):
            return None

        resolved = value.get("value")
        if isinstance(resolved, str):
            return resolved
        if isinstance(resolved, dict):
            name = resolved.get("name")
            if isinstance(name, str):
                return name
        return None


def _json_object_from_output(output: str) -> dict[str, Any]:
    stripped = output.strip()
    if not stripped:
        return {}

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return {}

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}

    if isinstance(payload, dict):
        return payload
    return {}


def _has_resolved_output(outputs: TerraformOutputs, key: str) -> bool:
    return bool(outputs.try_key_value(key))


def _tfvars_outputs(terraform_dir: Path) -> TerraformOutputs:
    tfvars_path = terraform_dir / "terraform.tfvars"
    if shutil.which("terraform") is None or not tfvars_path.is_file():
        return TerraformOutputs({})

    init_result = run_command(
        ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
        cwd=terraform_dir,
    )
    if init_result.returncode != 0:
        return TerraformOutputs({})

    console_result = run_command(
        ["terraform", "console", f"-var-file={tfvars_path}"],
        cwd=terraform_dir,
        input_text=(
            "jsonencode({environment=var.environment,instance=var.instance,"
            "resource_group_name=var.resource_group_name,resource_prefix=var.resource_prefix})"
        ),
    )
    if console_result.returncode != 0:
        return TerraformOutputs({})

    metadata = _json_object_from_output(console_result.stdout)
    environment = metadata.get("environment")
    resource_prefix = metadata.get("resource_prefix")
    instance = metadata.get("instance")
    if not all(isinstance(value, str) and value for value in (environment, resource_prefix, instance)):
        return TerraformOutputs({})

    suffix = f"{resource_prefix}-{environment}-{instance}"
    resource_group_name = metadata.get("resource_group_name")
    if not isinstance(resource_group_name, str) or not resource_group_name:
        resource_group_name = f"rg-{suffix}"

    return TerraformOutputs(
        {
            "resource_group": {"value": {"name": resource_group_name}},
            "azureml_workspace": {"value": {"name": f"mlw-{suffix}"}},
            "aks_cluster": {"value": {"name": f"aks-{suffix}"}},
        }
    )


@cache
def _terraform_outputs(repo_root: Path) -> TerraformOutputs:
    terraform_dir = repo_root / "infrastructure/terraform"
    if not terraform_dir.is_dir():
        return TerraformOutputs({})

    if shutil.which("terraform") is None:
        return TerraformOutputs({})

    result = run_command(["terraform", "output", "-json"], cwd=terraform_dir)
    output_payload = {}
    if result.returncode == 0 and result.stdout.strip():
        output_payload = _json_object_from_output(result.stdout)

    resolved_outputs = TerraformOutputs(output_payload)
    if all(_has_resolved_output(resolved_outputs, key) for key in TFVARS_FALLBACK_OUTPUT_KEYS):
        return resolved_outputs

    fallback_outputs = _tfvars_outputs(terraform_dir)
    if not fallback_outputs.values:
        return resolved_outputs

    if not resolved_outputs.values:
        return fallback_outputs

    merged_outputs = dict(resolved_outputs.values)
    for key, value in fallback_outputs.values.items():
        if not _has_resolved_output(resolved_outputs, key):
            merged_outputs[key] = value
    return TerraformOutputs(merged_outputs)


def _compute_target_name(outputs: TerraformOutputs) -> str:
    compute_name = os.environ.get("AZUREML_COMPUTE", "")
    if compute_name:
        return compute_name

    aks_cluster_name = outputs.try_key_value("aks_cluster")
    if not aks_cluster_name:
        return ""

    compute_name = aks_cluster_name.replace("aks-", "k8s-", 1)
    if len(compute_name) > AML_COMPUTE_NAME_MAX_LENGTH:
        compute_name = compute_name[:AML_COMPUTE_NAME_MAX_LENGTH].rstrip("-")
    return compute_name


@pytest.fixture(scope="session")
def repo_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    train_script = root / "training/rl/scripts/train.sh"
    if not train_script.is_file():
        pytest.skip(f"RL train script not found: {train_script}")
    return root


def _subscription_id_from_az_cli() -> str:
    if shutil.which("az") is None:
        pytest.skip("Azure CLI is not installed")

    result = run_command(["az", "account", "show", "-o", "json"], cwd=Path.cwd())
    if result.returncode != 0:
        pytest.skip("Azure CLI is not logged in or account context is unavailable")

    payload = _json_object_from_output(result.stdout)
    if not payload:
        pytest.skip("Azure CLI account output was not a valid JSON object")

    subscription_id = payload.get("id")
    if not isinstance(subscription_id, str) or not subscription_id:
        pytest.skip("Azure CLI account output did not include a subscription ID")
    return subscription_id


@pytest.fixture(scope="session")
def aml_workspace(repo_root: Path) -> AzureMLWorkspace:
    """
    Resolves the AML workspace to use for tests, skipping if it cannot be determined or accessed.
    The workspace is determined by env vars, Terraform outputs, or terraform.tfvars, and is validated for accessibility
    via the Azure CLI.
    """
    tf_outputs = _terraform_outputs(repo_root)

    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID") or _subscription_id_from_az_cli()
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP") or tf_outputs.try_key_value("resource_group")
    workspace_name = os.environ.get("AZUREML_WORKSPACE_NAME") or tf_outputs.try_key_value("azureml_workspace")

    if not resource_group:
        pytest.skip("Azure resource group is not configured in env vars, Terraform outputs, or terraform.tfvars")
    if not workspace_name:
        pytest.skip("AzureML workspace is not configured in env vars, Terraform outputs, or terraform.tfvars")

    result = run_command(
        [
            "az",
            "ml",
            "workspace",
            "show",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--name",
            workspace_name,
            "-o",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        pytest.skip("AzureML workspace is unreachable with the current Azure CLI context")

    return AzureMLWorkspace(
        subscription_id=subscription_id,
        resource_group=resource_group,
        workspace_name=workspace_name,
    )


@pytest.fixture(scope="session")
def aml_compute_target(repo_root: Path, aml_workspace: AzureMLWorkspace) -> None:
    """
    Ensures AML compute target is available, skipping tests if not.
    The compute target name is determined by the AZUREML_COMPUTE env var or Terraform outputs.
    """
    tf_outputs = _terraform_outputs(repo_root)
    compute_name = _compute_target_name(tf_outputs)
    if not compute_name:
        pytest.skip("AzureML compute target is not configured in env vars, Terraform outputs, or terraform.tfvars")

    result = run_command(
        [
            "az",
            "ml",
            "compute",
            "show",
            "--subscription",
            aml_workspace.subscription_id,
            "--resource-group",
            aml_workspace.resource_group,
            "--workspace-name",
            aml_workspace.workspace_name,
            "--name",
            compute_name,
            "-o",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        pytest.skip(f"AzureML compute target is unavailable: {compute_name}")

    payload = _json_object_from_output(result.stdout)
    if not payload:
        pytest.skip(f"AzureML compute target response was not a valid JSON object: {compute_name}")

    provisioning_state = payload.get("provisioning_state")
    if isinstance(provisioning_state, str) and provisioning_state.lower() != "succeeded":
        pytest.skip(f"AzureML compute target is not ready: {compute_name} ({provisioning_state})")


@pytest.fixture(scope="session")
def ensure_osmo_cli_available(repo_root: Path) -> None:
    """Ensures OSMO CLI is available and authenticated, skipping tests if not."""
    if shutil.which("osmo") is None:
        pytest.skip("OSMO CLI is not installed")

    result = run_command(
        ["osmo", "workflow", "list", "--count", "1", "--format-type", "json"],
        cwd=repo_root,
    )
    if result.returncode != 0:
        pytest.skip("OSMO CLI is unavailable or not authenticated")


@pytest.fixture(scope="session")
def ensure_gpu_nodes_available(repo_root: Path) -> None:
    """Ensures that GPU nodes are available in the Kubernetes cluster, skipping tests if not."""
    if shutil.which("kubectl") is None:
        pytest.skip("kubectl is not installed")

    result = run_command(["kubectl", "get", "nodes", "--no-headers"], cwd=repo_root)
    if result.returncode != 0:
        pytest.skip("kubectl context is unavailable")

    output = result.stdout.strip()
    if not output:
        pytest.skip("kubectl returned no cluster nodes")

    result = run_command(
        ["kubectl", "get", "nodes", "-l", "nvidia.com/gpu.present=true", "-o", "json"],
        cwd=repo_root,
    )
    if result.returncode != 0:
        pytest.skip("Unable to query GPU nodes from the Kubernetes cluster")

    payload = _json_object_from_output(result.stdout)
    if not payload:
        pytest.skip("kubectl GPU node output was not a valid JSON object")

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        pytest.skip("No GPU nodes are available in the Kubernetes cluster")

    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        name = metadata.get("name")
        if isinstance(name, str) and name:
            return

    pytest.skip("No GPU node names were returned from kubectl")
