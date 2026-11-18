"""Azure ML authentication, tracking, and storage connectivity smoke test."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib
import ipaddress
import json
import logging
import os
import socket
import ssl
import sys
import tempfile
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from training.rl.scripts import launch as launch_entrypoint
from training.utils import AzureConfigError, AzureMLContext, bootstrap_azure_ml
from training.utils.context import AzureStorageContext

_LOGGER = logging.getLogger("isaaclab.azure-smoke")
_DEFAULT_EXPERIMENT = "isaaclab-smoke-test"
_DEFAULT_RUN_NAME = "azure-connectivity-smoke-test"
_DEFAULT_METRIC = "connectivity"

_ARM_SCOPE = "https://management.azure.com/.default"
_STORAGE_SCOPE = "https://storage.azure.com/.default"

_IDENTITY_ENV_VARS = {
    "AZURE_CLIENT_ID": "client_id",
    "AZURE_TENANT_ID": "tenant_id",
    "AZURE_FEDERATED_TOKEN_FILE": "token_file",
}

_FORBIDDEN_STATIC_SECRET_VARS = (
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "AZURE_CLIENT_CERTIFICATE_PASSWORD",
    "AZURE_USERNAME",
    "AZURE_PASSWORD",
    "AZURE_STORAGE_KEY",
    "AZURE_STORAGE_CONNECTION_STRING",
    "AZURE_STORAGE_SAS_TOKEN",
)


def _detect_static_secret_vars() -> list[str]:
    """Return names (never values) of configured static secret/key/SAS env vars."""
    return [name for name in _FORBIDDEN_STATIC_SECRET_VARS if os.environ.get(name)]


def _decode_jwt_claims_unverified(token: str) -> dict[str, Any]:
    """Extract non-sensitive diagnostic claims from a JWT payload without logging raw token text."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    claims: dict[str, Any] = {}
    for key in ("tid", "oid", "appid", "azp", "idtyp"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            claims[key] = val
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        claims["expires_at"] = datetime.fromtimestamp(exp, tz=UTC).isoformat()
    return claims


def _check_identity_env_var(env_var: str, info_key: str, identity_info: dict[str, str]) -> None:
    value = os.environ.get(env_var)
    if value:
        identity_info[info_key] = value
        _LOGGER.info("Workload identity %s: %s", info_key, value)
        if info_key == "token_file" and not os.path.exists(value):
            _LOGGER.warning("%s set but file does not exist: %s", env_var, value)
    else:
        _LOGGER.warning("%s not set", env_var)


def _validate_workload_identity(*, require_workload_identity: bool = False) -> dict[str, str]:
    """Validate workload identity environment variables are present."""
    identity_info: dict[str, str] = {}
    for env_var, info_key in _IDENTITY_ENV_VARS.items():
        _check_identity_env_var(env_var, info_key, identity_info)

    if require_workload_identity:
        missing = [k for k in ("client_id", "tenant_id", "token_file") if k not in identity_info]
        token_file = identity_info.get("token_file", "")
        if missing:
            raise SystemExit(f"Workload identity required (--require-workload-identity), missing: {', '.join(missing)}")
        if not os.path.isfile(token_file) or os.path.getsize(token_file) == 0:
            raise SystemExit(f"Workload identity token file does not exist or is empty: {token_file}")
    return identity_info


def _test_credential_acquisition(
    *,
    verify_all_scopes: bool = False,
    token_diagnostics: dict[str, Any] | None = None,
) -> bool:
    """Test credential acquisition across required Azure scopes."""
    try:
        identity = importlib.import_module("azure.identity")
        credential_cls = identity.DefaultAzureCredential
        managed_client_id = os.environ.get("AZURE_CLIENT_ID")
        credential = (
            credential_cls(managed_identity_client_id=managed_client_id) if managed_client_id else credential_cls()
        )
        scopes = (_ARM_SCOPE, _STORAGE_SCOPE) if verify_all_scopes else (_ARM_SCOPE,)
        for scope in scopes:
            access_token = credential.get_token(scope)
            claims = _decode_jwt_claims_unverified(getattr(access_token, "token", ""))
            if token_diagnostics is not None and scope == _ARM_SCOPE and claims:
                token_diagnostics.update(claims)
            _LOGGER.info(
                "Successfully acquired Azure token for %s (oid=%s, expires_at=%s)",
                scope,
                claims.get("oid", "redacted-or-non-jwt"),
                claims.get("expires_at", "unknown"),
            )
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


def _test_storage_upload(storage: AzureStorageContext) -> dict[str, Any]:
    """Test uploading and SHA-256 reading back a checkpoint artifact in Azure Storage."""
    fd, checkpoint_path = tempfile.mkstemp(prefix="azure-smoke-", suffix=".chkpt")
    model_name = f"smoke-test-{uuid.uuid4().hex[:8]}"
    payload = b"azure storage smoke test payload"
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    blob_name = ""
    readback_verified = False

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)

        blob_name = storage.upload_checkpoint(
            local_path=checkpoint_path,
            model_name=model_name,
        )
        blob_service = getattr(storage, "blob_client", None)
        if blob_service is not None and hasattr(blob_service, "get_blob_client"):
            blob_client = blob_service.get_blob_client(container=storage.container_name, blob=blob_name)
            downloaded = blob_client.download_blob().readall()
            if isinstance(downloaded, (bytes, bytearray)):
                actual_sha256 = hashlib.sha256(downloaded).hexdigest()
                if actual_sha256 != expected_sha256:
                    raise RuntimeError(
                        f"Storage read-back SHA-256 mismatch for {blob_name}: "
                        f"expected {expected_sha256}, got {actual_sha256}"
                    )
                readback_verified = True
            with contextlib.suppress(Exception):
                blob_client.delete_blob()
        _LOGGER.info(
            "✓ Storage upload to container %s succeeded (blob %s, readback_verified=%s)",
            storage.container_name,
            blob_name,
            readback_verified,
        )
        return {
            "blob_name": blob_name,
            "sha256": expected_sha256,
            "readback_verified": readback_verified,
        }
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


def _extract_probe_host(uri: str) -> str | None:
    """Extract an FQDN from an HTTPS or azureml:// URI, skipping synthetic test URIs."""
    if not uri:
        return None
    normalized = uri
    if normalized.startswith("azureml://"):
        normalized = "https://" + normalized[len("azureml://") :]
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").strip().lower()
    if not host or "." not in host:
        return None
    return host


def _proxy_applies(host: str) -> bool:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if not proxy:
        return False
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    for raw in no_proxy.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token == "*" or host == token or host.endswith("." + token.lstrip(".")):
            return False
    return True


def _probe_single_endpoint(role: str, host: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    """Resolve DNS, classify Private Link vs public IP, and measure direct TLS 443 reachability."""
    via_proxy = _proxy_applies(host)
    record: dict[str, Any] = {
        "role": role,
        "host": host,
        "resolved_ips": [],
        "private_link_ip": False,
        "via_proxy": via_proxy,
        "tls_reachable": False,
        "tls_ms": None,
        "error": None,
    }
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = sorted({str(info[4][0]) for info in infos if info[4]})
        record["resolved_ips"] = ips
        record["private_link_ip"] = bool(ips) and all(ipaddress.ip_address(ip).is_private for ip in ips)
    except Exception as exc:
        record["error"] = f"dns_error: {exc}"
        if not via_proxy:
            return record

    if via_proxy:
        record["tls_reachable"] = True
        return record

    started = time.monotonic()
    ssl_ctx = ssl.create_default_context()
    try:
        with (
            socket.create_connection((host, 443), timeout=timeout_s) as raw_sock,
            ssl_ctx.wrap_socket(raw_sock, server_hostname=host),
        ):
            record["tls_reachable"] = True
            record["tls_ms"] = round((time.monotonic() - started) * 1000.0, 1)
            record["error"] = None
    except Exception as exc:
        record["error"] = f"tls_error: {exc}"
    return record


def _probe_network_truth_table(
    *,
    tracking_uri: str,
    dataset_blob_url: str | None = None,
    require_private_link: bool = False,
) -> list[dict[str, Any]]:
    """Build the ST-2 endpoint reachability and Private-Link classification table."""
    authority_host = _extract_probe_host(os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com"))
    targets: list[tuple[str, str, bool]] = []
    if authority_host:
        targets.append(("entra_authority", authority_host, False))
    targets.append(("arm_control_plane", "management.azure.com", False))

    mlflow_host = _extract_probe_host(tracking_uri)
    if mlflow_host:
        targets.append(("aml_mlflow_tracking", mlflow_host, True))

    storage_account = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "").strip()
    if storage_account:
        targets.append(("output_blob_storage", f"{storage_account}.blob.core.windows.net", True))

    if dataset_blob_url:
        ds_host = _extract_probe_host(dataset_blob_url)
        if ds_host and all(h != ds_host for _, h, _ in targets):
            targets.append(("dataset_blob_storage", ds_host, True))

    results: list[dict[str, Any]] = []
    failures: list[str] = []
    private_link_violations: list[str] = []
    for role, host, must_be_private in targets:
        res = _probe_single_endpoint(role, host)
        results.append(res)
        _LOGGER.info(
            "Network probe [%s] %s -> ips=%s private_link=%s tls_ok=%s (%sms)",
            role,
            host,
            res["resolved_ips"],
            res["private_link_ip"],
            res["tls_reachable"],
            res["tls_ms"],
        )
        if not res["tls_reachable"]:
            failures.append(f"{role} ({host}): {res['error']}")
        elif require_private_link and must_be_private and not res["private_link_ip"]:
            private_link_violations.append(f"{role} ({host} -> {res['resolved_ips']})")

    if failures:
        raise SystemExit(f"Network endpoint probe failed: {'; '.join(failures)}")
    if private_link_violations:
        raise SystemExit(
            f"Private Link required (--require-private-link), but public IPs resolved for: "
            f"{'; '.join(private_link_violations)}"
        )
    return results


def _parse_dataset_asset_ref(asset_ref: str) -> tuple[str, str]:
    """Parse azureml:<name>:<version> or full data asset URI into (name, version)."""
    cleaned = asset_ref.strip()
    if "@latest" in cleaned or cleaned.endswith(":latest"):
        raise ValueError(f"Dataset asset reference must pin an immutable version, got: {cleaned}")
    if cleaned.startswith("azureml://") and "/data/" in cleaned and "/versions/" in cleaned:
        tail = cleaned.split("/data/", 1)[1]
        name, _, version = tail.partition("/versions/")
        version = version.strip("/")
        if name and version:
            return name, version
    if cleaned.startswith("azureml:"):
        cleaned = cleaned[len("azureml:") :]
    parts = cleaned.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Expected azureml:<name>:<version>, got: {asset_ref}")
    return parts[0], parts[1]


def _probe_dataset_hydration_source(
    *,
    client: Any,
    dataset_asset: str | None,
    dataset_blob_url: str | None,
    forbid_static_secrets: bool,
) -> dict[str, Any]:
    """Read-only preflight confirming the worker can resolve and list the dataset source."""
    hydration_info: dict[str, Any] = {}
    resolved_blob_url = dataset_blob_url

    if dataset_asset:
        name, version = _parse_dataset_asset_ref(dataset_asset)
        asset = client.data.get(name=name, version=version)
        asset_path = str(getattr(asset, "path", "") or "")
        props = getattr(asset, "properties", None) or {}
        clean_path = (
            urlparse(asset_path)._replace(query="").geturl() if asset_path.startswith("https://") else asset_path
        )
        hydration_info["dataset_asset"] = {
            "name": name,
            "version": version,
            "path": clean_path,
            "manifest_sha256": props.get("manifest_sha256"),
        }
        _LOGGER.info("✓ Resolved AML Data Asset %s:%s", name, version)
        if not resolved_blob_url and asset_path.startswith("https://") and ".blob.core.windows.net/" in asset_path:
            resolved_blob_url = asset_path

    if resolved_blob_url:
        parsed = urlparse(resolved_blob_url)
        redacted = parsed._replace(query="").geturl()
        if parsed.query and forbid_static_secrets:
            raise SystemExit(f"SAS query string forbidden (--forbid-static-secrets) in dataset URL: {redacted}")
        parts = parsed.path.lstrip("/").split("/", 1)
        if parsed.scheme != "https" or not parsed.netloc or len(parts) < 2 or not parts[0] or not parts[1]:
            raise SystemExit(
                f"Dataset Blob URL must be https://<account>.blob.core.windows.net/<container>/<prefix>: {redacted}"
            )
        container, prefix = parts[0], parts[1].rstrip("/") + "/"
        endpoint = f"https://{parsed.netloc}"

        identity_mod = importlib.import_module("azure.identity")
        blob_mod = importlib.import_module("azure.storage.blob")
        cred_kwargs: dict[str, Any] = {}
        if os.environ.get("AZURE_CLIENT_ID"):
            cred_kwargs["managed_identity_client_id"] = os.environ["AZURE_CLIENT_ID"]
        if os.environ.get("AZURE_AUTHORITY_HOST"):
            cred_kwargs["authority"] = os.environ["AZURE_AUTHORITY_HOST"]
        cred = (
            identity_mod.DefaultAzureCredential(**cred_kwargs) if cred_kwargs else identity_mod.DefaultAzureCredential()
        )
        container_client = blob_mod.ContainerClient(endpoint, container, credential=cred)

        sampled: list[str] = []
        has_info_json = False
        has_published_marker = False
        has_checksums = False
        for idx, blob in enumerate(container_client.list_blobs(name_starts_with=prefix)):
            rel = blob.name[len(prefix) :].lstrip("/")
            if rel == "meta/info.json":
                has_info_json = True
            elif rel == ".published.json":
                has_published_marker = True
            elif rel == "checksums.sha256":
                has_checksums = True
            if idx < 25:
                sampled.append(rel)
            if has_info_json and has_published_marker and has_checksums and idx >= 25:
                break
        if not sampled:
            raise SystemExit(f"Dataset Blob prefix returned 0 blobs: {redacted}")
        hydration_info["dataset_blob"] = {
            "url": redacted,
            "sampled_blobs": len(sampled),
            "has_meta_info_json": has_info_json,
            "has_published_marker": has_published_marker,
            "has_checksums_sha256": has_checksums,
        }
        _LOGGER.info(
            "✓ Dataset Blob read access confirmed (%s, meta/info.json=%s, published=%s)",
            redacted,
            has_info_json,
            has_published_marker,
        )

    return hydration_info


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
    parser.add_argument(
        "--forbid-static-secrets",
        action="store_true",
        help="Fail if static secret, key, connection-string, or SAS env vars are set",
    )
    parser.add_argument(
        "--require-workload-identity",
        action="store_true",
        help="Require AZURE_CLIENT_ID, AZURE_TENANT_ID, and readable AZURE_FEDERATED_TOKEN_FILE",
    )
    parser.add_argument(
        "--verify-all-scopes",
        action="store_true",
        help="Verify token acquisition for both ARM and Azure Blob Storage scopes",
    )
    parser.add_argument(
        "--require-artifact-upload",
        action="store_true",
        help="Fail if uploading smoke-test-summary.json to the MLflow artifact store fails",
    )
    parser.add_argument(
        "--probe-network",
        action="store_true",
        help="Probe DNS, Private-Link IP classification, and TLS 443 reachability for target Azure endpoints",
    )
    parser.add_argument(
        "--require-private-link",
        action="store_true",
        help="Require AML tracking and Azure Storage endpoints to resolve to RFC1918 Private-Link IPs",
    )
    parser.add_argument(
        "--dataset-blob-url",
        default=None,
        help="Optional https://<account>.blob.core.windows.net/<container>/<prefix> read preflight",
    )
    parser.add_argument(
        "--dataset-asset",
        default=None,
        help="Optional pinned AML Data Asset (azureml:<name>:<version>) to verify resolution and Blob read access",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print sanitized JSON validation receipt to stdout",
    )
    return parser.parse_args(argv)


def _load_mlflow() -> Any:
    return importlib.import_module("mlflow")


def _start_run(
    context: AzureMLContext,
    args: argparse.Namespace,
    user_tags: dict[str, str],
    identity_info: dict[str, str],
    token_diagnostics: dict[str, Any] | None = None,
    extra_diagnostics: dict[str, Any] | None = None,
) -> str:
    mlflow_module = _load_mlflow()

    tags = {
        "entrypoint": "training/scripts/smoke_test_azure.py",
        "smoke_test": "azure-connectivity",
        "workspace_name": context.workspace_name,
    }
    tags.update(user_tags)

    token_file = identity_info.get("token_file", "")
    auth_summary: dict[str, Any] = {
        "method": "DefaultAzureCredential",
        "workload_identity_configured": bool(os.environ.get("AZURE_CLIENT_ID")),
        "client_id": identity_info.get("client_id", "not-set"),
        "tenant_id": identity_info.get("tenant_id", "not-set"),
        "federated_token_present": bool(token_file and os.path.exists(token_file)),
        "static_secret_vars_present": _detect_static_secret_vars(),
    }
    if token_diagnostics:
        auth_summary["token_claims"] = token_diagnostics

    summary: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "message": args.summary_message,
        "workspace": context.workspace_name,
        "tracking_uri": context.tracking_uri,
        "experiment_name": args.experiment_name,
        "authentication": auth_summary,
        "storage": {
            "configured": bool(context.storage),
            "container": (context.storage.container_name if context.storage else "not-set"),
        },
        "artifact_uploaded": False,
    }
    if extra_diagnostics:
        summary.update(extra_diagnostics)

    with mlflow_module.start_run(run_name=args.run_name) as run:
        mlflow_module.set_tags(tags)
        mlflow_module.log_param("workspace_name", context.workspace_name)
        mlflow_module.log_param(
            "storage_container",
            context.storage.container_name if context.storage else "not-configured",
        )
        mlflow_module.log_metric(args.metric_name, 1.0)
        try:
            mlflow_module.log_dict(summary, "smoke-test-summary.json")
            summary["artifact_uploaded"] = True
        except Exception as exc:
            if getattr(args, "require_artifact_upload", False):
                raise
            _LOGGER.warning("MLflow summary artifact upload failed (metrics succeeded): %s", exc)
        _LOGGER.info("Smoke test run created with ID %s", run.info.run_id)
        summary["run_id"] = run.info.run_id

    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2))
    return summary["run_id"]


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    args = _parse_args(argv)

    launch_entrypoint._ensure_dependencies()

    try:
        user_tags = _parse_tags(args.tag)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    static_secret_vars = _detect_static_secret_vars()
    if static_secret_vars:
        joined = ", ".join(static_secret_vars)
        if args.forbid_static_secrets:
            raise SystemExit(f"Forbidden static credentials present (--forbid-static-secrets): {joined}")
        _LOGGER.warning("Static credential environment variables detected: %s", joined)

    _LOGGER.info("Validating workload identity configuration...")
    identity_info = _validate_workload_identity(
        require_workload_identity=args.require_workload_identity,
    )

    _LOGGER.info("Testing credential acquisition...")
    token_diagnostics: dict[str, Any] = {}
    cred_kwargs: dict[str, Any] = {}
    if args.verify_all_scopes:
        cred_kwargs["verify_all_scopes"] = True
    if args.verify_all_scopes or args.json:
        cred_kwargs["token_diagnostics"] = token_diagnostics
    if not _test_credential_acquisition(**cred_kwargs):
        raise SystemExit("Failed to acquire credentials")

    try:
        context = bootstrap_azure_ml(
            experiment_name=args.experiment_name,
        )
    except AzureConfigError as exc:
        raise SystemExit(str(exc)) from exc

    extra_diagnostics: dict[str, Any] = {}
    if args.probe_network or args.require_private_link:
        _LOGGER.info("Probing endpoint DNS and Private-Link TLS routes...")
        extra_diagnostics["network_routes"] = _probe_network_truth_table(
            tracking_uri=context.tracking_uri,
            dataset_blob_url=args.dataset_blob_url,
            require_private_link=args.require_private_link,
        )

    _LOGGER.info("Testing workspace permissions...")
    _test_workspace_permissions(context.client, context.workspace_name)

    if args.dataset_asset or args.dataset_blob_url:
        _LOGGER.info("Validating dataset hydration read access...")
        try:
            extra_diagnostics["hydration_preflight"] = _probe_dataset_hydration_source(
                client=context.client,
                dataset_asset=args.dataset_asset,
                dataset_blob_url=args.dataset_blob_url,
                forbid_static_secrets=args.forbid_static_secrets,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    if context.storage:
        _LOGGER.info(
            "Testing storage upload to container %s...",
            context.storage.container_name,
        )
        storage_receipt = _test_storage_upload(context.storage)
        if storage_receipt:
            extra_diagnostics["storage_probe"] = storage_receipt
    else:
        _LOGGER.info("Skipping storage upload test (no storage context configured)")

    if extra_diagnostics:
        run_id = _start_run(context, args, user_tags, identity_info, token_diagnostics, extra_diagnostics)
    elif token_diagnostics:
        run_id = _start_run(context, args, user_tags, identity_info, token_diagnostics)
    else:
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
