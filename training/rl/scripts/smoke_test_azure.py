"""Azure ML connectivity smoke test."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import logging
import os
import sys
import tempfile
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from training.scripts import launch as launch_entrypoint
from training.utils import AzureConfigError, AzureMLContext, bootstrap_azure_ml
from training.utils.context import AzureStorageContext

_LOGGER = logging.getLogger("isaaclab.azure-smoke")
_DEFAULT_EXPERIMENT = "isaaclab-smoke-test"
_DEFAULT_RUN_NAME = "azure-connectivity-smoke-test"
_DEFAULT_METRIC = "connectivity"


_IDENTITY_ENV_VARS = {
    "AZURE_CLIENT_ID": "client_id",
    "AZURE_TENANT_ID": "tenant_id",
    "AZURE_FEDERATED_TOKEN_FILE": "token_file",
}


def _check_identity_env_var(env_var: str, info_key: str, identity_info: dict[str, str]) -> None:
    value = os.environ.get(env_var)
    if value:
        identity_info[info_key] = value
        _LOGGER.info("Workload identity %s: %s", info_key, value)
        if info_key == "token_file" and not os.path.exists(value):
            _LOGGER.warning("%s set but file does not exist: %s", env_var, value)
    else:
        _LOGGER.warning("%s not set", env_var)


def _validate_workload_identity() -> dict[str, str]:
    """Validate workload identity environment variables are present."""
    identity_info: dict[str, str] = {}

    azure_client_id = os.environ.get("AZURE_CLIENT_ID")
    azure_tenant_id = os.environ.get("AZURE_TENANT_ID")
    azure_federated_token_file = os.environ.get("AZURE_FEDERATED_TOKEN_FILE")

    if azure_client_id:
        identity_info["client_id"] = azure_client_id
        _LOGGER.info("Workload identity client_id: %s", azure_client_id)
    else:
        _LOGGER.warning("AZURE_CLIENT_ID not set")

    if azure_tenant_id:
        identity_info["tenant_id"] = azure_tenant_id
        _LOGGER.info("Workload identity tenant_id: %s", azure_tenant_id)
    else:
        _LOGGER.warning("AZURE_TENANT_ID not set")

    if azure_federated_token_file:
        identity_info["token_file"] = azure_federated_token_file
        if os.path.exists(azure_federated_token_file):
            _LOGGER.info("Federated token file exists: %s", azure_federated_token_file)
        else:
            _LOGGER.warning(
                "AZURE_FEDERATED_TOKEN_FILE is set but file does not exist: %s",
                azure_federated_token_file,
            )
    else:
        _LOGGER.warning("AZURE_FEDERATED_TOKEN_FILE not set")

    for env_var, info_key in _IDENTITY_ENV_VARS.items():
        _check_identity_env_var(env_var, info_key, identity_info)
    return identity_info


def _test_credential_acquisition() -> bool:
    """Test credential acquisition and log which method succeeded."""
    try:
        identity = importlib.import_module("azure.identity")
        credential_cls = identity.DefaultAzureCredential
        credential = credential_cls()
        credential.get_token("https://management.azure.com/.default")
        _LOGGER.info("Successfully acquired Azure credential")
        return True
    except Exception as exc:
        _LOGGER.error("Credential acquisition failed: %s", exc)
        return False


def _test_workspace_permissions(client: Any, workspace_name: str) -> None:
    """Test that the identity has proper permissions."""
    try:
        client.workspaces.get(workspace_name)
        _LOGGER.info("✓ Workspace read access confirmed")
        experiments = client.jobs.list(max_results=1)
        list(experiments)
        _LOGGER.info("✓ Experiment listing access confirmed")
    except Exception as exc:
        _LOGGER.warning("Permission test failed: %s", exc)
        raise


def _test_storage_upload(storage: AzureStorageContext) -> None:
    """Test uploading a checkpoint artifact to Azure Storage."""
    fd, checkpoint_path = tempfile.mkstemp(prefix="azure-smoke-", suffix=".chkpt")
    model_name = f"smoke-test-{uuid.uuid4().hex[:8]}"

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(b"azure storage smoke test payload")

        blob_name = storage.upload_checkpoint(
            local_path=checkpoint_path,
            model_name=model_name,
        )
        _LOGGER.info(
            "✓ Storage upload to container %s succeeded (blob %s)",
            storage.container_name,
            blob_name,
        )
    except Exception as exc:
        _LOGGER.warning(
            "Storage upload validation failed for container %s: %s",
            storage.container_name,
            exc,
        )
        raise
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(checkpoint_path)


def _parse_single_tag(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise ValueError(f"Tag '{raw}' must use KEY=VALUE format")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError("Tag key cannot be empty")
    return key, value.strip()


def _parse_tags(values: Sequence[str]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for raw in values:
        key, value = _parse_single_tag(raw)
        tags[key] = value
    return tags


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AzureML connectivity smoke test")
    parser.add_argument(
        "--experiment-name",
        default=_DEFAULT_EXPERIMENT,
        help="AzureML experiment name used for the smoke test run",
    )
    parser.add_argument(
        "--run-name",
        default=_DEFAULT_RUN_NAME,
        help="MLflow run name recorded for the smoke test",
    )
    parser.add_argument(
        "--metric-name",
        default=_DEFAULT_METRIC,
        help="Metric name to record for a successful connectivity run",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra MLflow tags for the smoke test run",
    )
    parser.add_argument(
        "--summary-message",
        default="Azure connectivity smoke test completed successfully.",
        help="Message stored in the summary artifact",
    )
    return parser.parse_args(argv)


def _load_mlflow() -> Any:
    return importlib.import_module("mlflow")


def _start_run(
    context: AzureMLContext,
    args: argparse.Namespace,
    user_tags: dict[str, str],
    identity_info: dict[str, str],
) -> str:
    mlflow_module = _load_mlflow()

    tags = {
        "entrypoint": "training/scripts/smoke_test_azure.py",
        "smoke_test": "azure-connectivity",
        "workspace_name": context.workspace_name,
    }
    tags.update(user_tags)

    summary = {
        "timestamp": datetime.now(UTC).isoformat(),
        "message": args.summary_message,
        "workspace": context.workspace_name,
        "tracking_uri": context.tracking_uri,
        "experiment_name": args.experiment_name,
        "authentication": {
            "method": "DefaultAzureCredential",
            "workload_identity_configured": bool(os.environ.get("AZURE_CLIENT_ID")),
            "client_id": identity_info.get("client_id", "not-set"),
            "tenant_id": identity_info.get("tenant_id", "not-set"),
            "federated_token_present": bool(identity_info.get("token_file")),
        },
        "storage": {
            "configured": bool(context.storage),
            "container": (context.storage.container_name if context.storage else "not-set"),
        },
    }

    with mlflow_module.start_run(run_name=args.run_name) as run:
        mlflow_module.set_tags(tags)
        mlflow_module.log_param("workspace_name", context.workspace_name)
        mlflow_module.log_param(
            "storage_container",
            context.storage.container_name if context.storage else "not-configured",
        )
        mlflow_module.log_metric(args.metric_name, 1.0)
        mlflow_module.log_dict(summary, "smoke-test-summary.json")
        _LOGGER.info("Smoke test run created with ID %s", run.info.run_id)
        return run.info.run_id


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    args = _parse_args(argv)

    launch_entrypoint._ensure_dependencies()

    try:
        user_tags = _parse_tags(args.tag)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    _LOGGER.info("Validating workload identity configuration...")
    identity_info = _validate_workload_identity()

    _LOGGER.info("Testing credential acquisition...")
    if not _test_credential_acquisition():
        raise SystemExit("Failed to acquire credentials")

    try:
        context = bootstrap_azure_ml(
            experiment_name=args.experiment_name,
        )
    except AzureConfigError as exc:
        raise SystemExit(str(exc)) from exc

    _LOGGER.info("Testing workspace permissions...")
    _test_workspace_permissions(context.client, context.workspace_name)

    if context.storage:
        _LOGGER.info(
            "Testing storage upload to container %s...",
            context.storage.container_name,
        )
        _test_storage_upload(context.storage)
    else:
        _LOGGER.info("Skipping storage upload test (no storage context configured)")

    run_id = _start_run(context, args, user_tags, identity_info)
    _LOGGER.info(
        "Azure connectivity validation succeeded for workspace %s",
        context.workspace_name,
    )
    _LOGGER.debug("Run tracking URI %s", context.tracking_uri)
    _LOGGER.debug("Recorded run ID %s", run_id)
    _LOGGER.info("Azure connectivity validated for workspace %s (run: %s)", context.workspace_name, run_id)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:
        _LOGGER.exception("Azure connectivity smoke test failed")
        raise SystemExit(1) from exc
