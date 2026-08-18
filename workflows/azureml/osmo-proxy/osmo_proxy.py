"""osmo_proxy.py — AML command job: submit OSMO workflow, poll, log MLflow metrics.

Integrates AML (lineage/artifact tracking) with OSMO (native cluster orchestration):
  1. Submit workflow YAML to OSMO REST API (POST /api/pool/{pool}/workflow)
  2. Poll OSMO status (GET /api/workflow/{id}) until COMPLETED or FAILED
  3. Log metrics to MLflow:
     Always: task_count, failed_tasks, task_completed, success_rate, duration,
             per-group breakdown, task duration stats, first_error tag.
     Optional: spec-driven osmo.workflow.* extraction via OSMO_METRICS_SPEC.
  4. Register OSMO output blob paths as AML data assets (uri_folder)

  --metrics-only mode: skip OSMO submission/polling; only run spec-driven
  metric extraction against pre-existing output URLs (OSMO_OUTPUT_URLS).

Environment variables:
  OSMO_GATEWAY_URL      OSMO in-cluster URL (default: see _DEFAULT_GATEWAY_URL)
  OSMO_POOL             OSMO pool name (default: default)
  OSMO_AUTH_MODE        dev → x-osmo-user header; token → Authorization Bearer
                        Default: dev
  OSMO_USERNAME         Username for dev mode (default: admin)
  OSMO_TOKEN            Bearer token value for token auth mode.
                        Inject from the osmo-default-admin K8s secret:
                          kubectl get secret osmo-default-admin -n osmo-control-plane \\
                            -o jsonpath='{.data.password}' | base64 -d
                        Note: the K8s secret key is 'password', not 'token'.
  WORKFLOW_YAML         Path to the OSMO workflow YAML file (default: smoke-test)
  POLL_INTERVAL_SECS    Status polling interval in seconds (default: 30)
  OSMO_SET_VARIABLES    JSON array of {"name": key, "value": val} variable overrides.
                        Example: '[{"name":"dataset","value":"vda-demo"}]'
  OSMO_METRICS_SPEC     Path to or inline YAML of a workflow metrics extraction spec.
  AML_SUBSCRIPTION_ID   Azure subscription for data asset registration (optional)
  AML_RESOURCE_GROUP    Resource group for data asset registration (optional)
  AML_WORKSPACE_NAME    AML workspace name for data asset registration (optional)
  AZURE_CLIENT_ID       MSI client ID for blob access (Tier 2 metrics extraction).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import requests
import yaml

_LOGGER = logging.getLogger(__name__)

_DEFAULT_GATEWAY_URL = "http://osmo-gateway.osmo-control-plane.svc.cluster.local"
_DEFAULT_WORKFLOW_YAML = "workflows/osmo/smoke-test-proxy-e2e.yaml"
_DEFAULT_POLL_INTERVAL_SECS = 30

_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "FAILED_SUBMISSION",
        "FAILED_SERVER_ERROR",
        "FAILED_EXEC_TIMEOUT",
        "FAILED_QUEUE_TIMEOUT",
        "FAILED_CANCELED",
        "FAILED_BACKEND_ERROR",
        "FAILED_IMAGE_PULL",
        "FAILED_EVICTED",
        "FAILED_START_ERROR",
        "FAILED_START_TIMEOUT",
        "FAILED_PREEMPTED",
    }
)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _build_auth_headers() -> dict[str, str]:
    """Return HTTP headers for OSMO API requests based on OSMO_AUTH_MODE."""
    auth_mode = os.environ.get("OSMO_AUTH_MODE", "dev").lower()
    if auth_mode == "token":
        token = os.environ.get("OSMO_TOKEN", "").strip()
        if not token:
            raise ValueError("OSMO_TOKEN must be set (non-empty) when OSMO_AUTH_MODE=token")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    # dev mode — no bearer token required; gateway accepts x-osmo-user directly
    username = os.environ.get("OSMO_USERNAME", "admin")
    return {
        "x-osmo-user": username,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# OSMO API helpers
# ---------------------------------------------------------------------------


def _version_check(gateway_url: str, headers: dict[str, str]) -> None:
    """Verify the OSMO gateway is reachable before submitting."""
    url = f"{gateway_url}/api/version"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        ver = resp.json()
        _LOGGER.info(
            "OSMO gateway reachable. Version: %s.%s.%s",
            ver.get("major"),
            ver.get("minor"),
            ver.get("revision"),
        )
    except Exception as exc:
        raise RuntimeError(f"Cannot reach OSMO gateway at {gateway_url}: {exc}") from exc


def _submit_workflow(
    gateway_url: str,
    pool: str,
    workflow_yaml: str,
    headers: dict[str, str],
    set_variables: list[str] | None = None,
) -> str:
    """Submit a workflow YAML to OSMO. Returns the assigned workflow ID.

    set_variables: KEY=VALUE strings for the OSMO TemplateSpec payload.
    Wire format: {"file": "<yaml text>", "set_variables": ["KEY=VALUE", ...]}
    """
    url = f"{gateway_url}/api/pool/{pool}/workflow"
    sv = set_variables or []
    payload: dict[str, Any] = {
        "file": workflow_yaml,
        "set_variables": sv,
    }
    if sv:
        _LOGGER.info("Passing %d set_variables: %s", len(sv), ", ".join(sv))
    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"OSMO submit failed [{resp.status_code}]: {resp.text}")
    data = resp.json()
    workflow_id = data.get("name")
    if not workflow_id:
        raise RuntimeError(f"OSMO submit response missing 'name' field: {data}")
    _LOGGER.info("Submitted OSMO workflow: %s", workflow_id)
    return workflow_id


def _poll_until_done(
    gateway_url: str,
    workflow_id: str,
    headers: dict[str, str],
    poll_interval: int,
) -> dict[str, Any]:
    """Poll GET /api/workflow/{id} until a terminal status is reached.

    Returns the final WorkflowQueryResponse dict.
    """
    url = f"{gateway_url}/api/workflow/{workflow_id}"
    while True:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            _LOGGER.warning("Poll request failed (%s) — will retry", exc)
            time.sleep(poll_interval)
            continue

        if resp.status_code != 200:
            _LOGGER.warning(
                "Poll returned HTTP %s — will retry: %s",
                resp.status_code,
                resp.text[:200],
            )
            time.sleep(poll_interval)
            continue

        data = resp.json()
        status = data.get("status", "UNKNOWN")
        _LOGGER.info("Workflow %s → %s", workflow_id, status)

        if status in _TERMINAL_STATUSES:
            return data
        time.sleep(poll_interval)


def _fetch_tail_logs(
    gateway_url: str,
    workflow_id: str,
    headers: dict[str, str],
    last_n_lines: int = 50,
) -> str:
    """Fetch the last N log lines for the workflow (best-effort)."""
    url = f"{gateway_url}/api/workflow/{workflow_id}/logs"
    try:
        resp = requests.get(
            url,
            params={"last_n_lines": last_n_lines},
            headers={k: v for k, v in headers.items() if k != "Content-Type"},
            timeout=30,
            stream=True,
        )
        if resp.status_code == 200:
            return resp.text
    except Exception as exc:
        _LOGGER.debug("Log fetch failed (non-fatal): %s", exc)
    return ""


# ---------------------------------------------------------------------------
# OSMO_SET_VARIABLES parsing
# ---------------------------------------------------------------------------


def _parse_set_variables(raw: str) -> list[str]:
    """Parse OSMO_SET_VARIABLES JSON array to KEY=VALUE strings.

    Converts [{"name": "k", "value": "v"}, ...] to ["k=v", ...].
    This conversion is required before passing to _submit_workflow;
    missing it causes OSMO API 422 errors.
    """
    if not raw.strip():
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        _LOGGER.warning("Failed to parse OSMO_SET_VARIABLES JSON (ignored): %s", exc)
        return []
    if not isinstance(items, list):
        _LOGGER.warning("OSMO_SET_VARIABLES must be a JSON array — ignored")
        return []
    result: list[str] = []
    for item in items:
        if isinstance(item, dict) and "name" in item and "value" in item:
            result.append(f"{item['name']}={item['value']}")
        else:
            _LOGGER.warning("Invalid OSMO_SET_VARIABLES entry (skipped): %r", item)
    return result


# ---------------------------------------------------------------------------
# Workflow YAML parsing
# ---------------------------------------------------------------------------


def _extract_output_urls(workflow_yaml_str: str) -> list[str]:
    """Extract output URL paths from a workflow YAML spec.

    Supports both flat tasks and nested groups format.
    Returns a list of URL strings; empty list if none defined.
    """
    try:
        spec = yaml.safe_load(workflow_yaml_str)
        workflow = spec.get("workflow", {})

        def _urls_from_tasks(task_list: list[dict[str, Any]]) -> list[str]:
            urls: list[str] = []
            for task in task_list or []:
                for output in task.get("outputs", []):
                    if isinstance(output, dict) and "url" in output:
                        urls.append(output["url"])
            return urls

        urls: list[str] = []
        urls.extend(_urls_from_tasks(workflow.get("tasks", [])))
        for group in workflow.get("groups", []):
            urls.extend(_urls_from_tasks(group.get("tasks", [])))
        return urls
    except Exception as exc:
        _LOGGER.warning("Could not parse output URLs from workflow YAML: %s", exc)
        return []


# ---------------------------------------------------------------------------
# MSI / blob helpers (used by Tier 2 metrics extraction)
# ---------------------------------------------------------------------------


def _get_msi_token(resource: str, client_id: str = "") -> str:
    """Get an Azure AD token from the IMDS MSI endpoint."""
    params = f"api-version=2018-02-01&resource={resource}"
    if client_id:
        params += f"&client_id={client_id}"
    resp = requests.get(
        f"http://169.254.169.254/metadata/identity/oauth2/token?{params}",
        headers={"Metadata": "true"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _fetch_blob_json(azure_url: str, client_id: str = "") -> dict[str, Any] | None:
    """Fetch a JSON file from an azure:// blob URL using MSI auth.

    Returns the parsed dict, or None if the blob does not exist or fetch fails.
    azure_url format: azure://<account>/<container>/<path>
    """
    if not azure_url.startswith("azure://"):
        return None
    rest = azure_url[len("azure://") :]
    parts = rest.split("/", 2)
    if len(parts) < 3:
        return None
    account, container, blob = parts[0], parts[1], parts[2]
    try:
        token = _get_msi_token("https://storage.azure.com/", client_id)
        url = f"https://{account}.blob.core.windows.net/{container}/{blob}"
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "x-ms-version": "2020-04-08",
            },
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return json.loads(resp.content)
    except Exception as exc:
        _LOGGER.debug("Blob JSON fetch failed for %s (non-fatal): %s", azure_url, exc)
        return None


def _list_blobs(
    account: str,
    container: str,
    prefix: str,
    client_id: str = "",
) -> list[str]:
    """List all blob names under prefix in the given container.

    Handles Azure Blob REST API pagination automatically.
    Returns relative blob names (full path within container).
    """
    try:
        token = _get_msi_token("https://storage.azure.com/", client_id)
    except Exception as exc:
        _LOGGER.debug("MSI token for blob list failed (non-fatal): %s", exc)
        return []

    results: list[str] = []
    marker = ""
    base = f"https://{account}.blob.core.windows.net/{container}"
    while True:
        params = f"restype=container&comp=list&prefix={prefix}"
        if marker:
            params += f"&marker={marker}"
        try:
            resp = requests.get(
                f"{base}?{params}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-ms-version": "2020-04-08",
                },
                timeout=30,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for blob_elem in root.iter("Name"):
                results.append(blob_elem.text or "")
            next_marker = root.findtext("NextMarker") or ""
            if not next_marker:
                break
            marker = next_marker
        except Exception as exc:
            _LOGGER.debug("Blob list failed (non-fatal): %s", exc)
            break
    return results


# ---------------------------------------------------------------------------
# Spec-driven Tier 2 metrics extraction
# ---------------------------------------------------------------------------


def _apply_transform(value: Any, transform: str) -> float | None:
    """Apply a named transform to a value extracted from a blob JSON file.

    Supported transforms:
      (none / "")   — value must already be numeric (int or float)
      len           — length of a list, dict, or string
      bool          — True → 1.0, False → 0.0
      not_bool      — True → 0.0, False → 1.0
      eq:<literal>  — 1.0 if str(value) == literal, else 0.0
      ne:<literal>  — 1.0 if str(value) != literal, else 0.0
    """
    if not transform:
        if isinstance(value, bool):
            return None  # bool is a subclass of int; require explicit bool transform
        if isinstance(value, (int, float)):
            return float(value)
        return None
    if transform == "len":
        if isinstance(value, (list, dict, str)):
            return float(len(value))
        return None
    if transform == "bool":
        return 1.0 if value else 0.0
    if transform == "not_bool":
        return 0.0 if value else 1.0
    if transform.startswith("eq:"):
        return 1.0 if str(value) == transform[3:] else 0.0
    if transform.startswith("ne:"):
        return 1.0 if str(value) != transform[3:] else 0.0
    _LOGGER.warning("Unknown transform %r — skipping", transform)
    return None


def _resolve_json_path(data: Any, path: str) -> Any:
    """Walk a dot-notation path through nested dicts/lists."""
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit():
            idx = int(part)
            data = data[idx] if idx < len(data) else None
        else:
            return None
        if data is None:
            return None
    return data


def _extract_tier2_metrics(
    spec: dict[str, Any],
    output_urls: list[str],
    client_id: str = "",
) -> dict[str, float]:
    """Evaluate a metrics extraction spec against OSMO output blob URLs.

    Spec format (YAML):
      metrics:
        - name: videos_completed
          source: "*/sidecars/pipeline_status.json"
          extract: status
          transform: "eq:completed"
          aggregate: sum

        - name: images_generated
          source: "results/**/*.png"
          aggregate: count

    Returns a dict of {metric_name: float}.
    """

    def _parse_azure_url(url: str) -> tuple[str | None, str | None, str]:
        rest = url[len("azure://") :]
        parts = rest.split("/", 2)
        if len(parts) < 2:
            return None, None, ""
        return parts[0], parts[1], parts[2] if len(parts) > 2 else ""

    results: dict[str, list[float]] = {}

    for entry in spec.get("metrics", []):
        name = entry.get("name", "").strip()
        source = entry.get("source", "").strip()
        extract = entry.get("extract", "").strip()
        transform = entry.get("transform", "").strip()
        aggregate = entry.get("aggregate", "sum").strip().lower()
        if not name or not source:
            _LOGGER.warning("Metrics spec entry missing name or source — skipped: %r", entry)
            continue

        count_only = aggregate == "count" and not extract
        values: list[float] = []

        for out_url in output_urls:
            if not out_url.startswith("azure://"):
                continue
            account, container, base_prefix = _parse_azure_url(out_url)
            if not account:
                continue
            base_prefix = base_prefix.rstrip("/")
            all_blobs = _list_blobs(account, container, base_prefix + "/", client_id)

            matched: list[str] = []
            for blob in all_blobs:
                rel = blob[len(base_prefix) :].lstrip("/")
                source_simple = source.replace("**/", "").replace("**", "*")
                if fnmatch.fnmatch(rel, source) or fnmatch.fnmatch(rel, source_simple):
                    matched.append(blob)

            if count_only:
                values.append(float(len(matched)))
                continue

            for blob_path in matched:
                blob_url = f"azure://{account}/{container}/{blob_path}"
                data = _fetch_blob_json(blob_url, client_id)
                if data is None:
                    continue
                raw = _resolve_json_path(data, extract) if extract else data
                value = _apply_transform(raw, transform)
                if value is not None:
                    values.append(value)

        if not values:
            continue

        if aggregate in ("sum", "count"):
            results[name] = [sum(values)]
        elif aggregate == "mean":
            results[name] = [sum(values) / len(values)]
        elif aggregate == "max":
            results[name] = [max(values)]
        elif aggregate == "min":
            results[name] = [min(values)]
        else:
            _LOGGER.warning("Unknown aggregate %r for metric %r — using sum", aggregate, name)
            results[name] = [sum(values)]

    return {k: v[0] for k, v in results.items()}


# ---------------------------------------------------------------------------
# MLflow integration
# ---------------------------------------------------------------------------


def _setup_mlflow() -> None:
    """Import and configure MLflow tracking.

    Imports azureml.mlflow to register the azureml:// tracking store plugin.
    Without this import, metrics are silently dropped when MLFLOW_TRACKING_URI
    uses the azureml:// scheme. Starts a run if one is not already active.
    """
    try:
        import mlflow

        try:
            import azureml.mlflow  # noqa: F401
        except ImportError:
            _LOGGER.debug("azureml.mlflow not installed — azureml:// tracking store unavailable")

        if mlflow.active_run() is None:
            mlflow.start_run()
    except ImportError:
        _LOGGER.info("mlflow not installed — MLflow tracking disabled")


def _log_to_mlflow(
    workflow_id: str,
    status: str,
    query: dict[str, Any],
    output_urls: list[str] | None = None,
    azure_client_id: str = "",
    metrics_spec: dict[str, Any] | None = None,
) -> None:
    """Log OSMO workflow Tier 1 and Tier 2 metrics to MLflow.

    Tier 1 (always logged):
      Tags: osmo.workflow_id, osmo.status, osmo.pool, osmo.first_error
      Metrics: osmo.task_count, osmo.task_completed, osmo.failed_tasks,
               osmo.task_success_rate, osmo.duration_seconds,
               osmo.task_duration_mean_s, osmo.task_duration_max_s,
               osmo.task_duration_p95_s,
               osmo.group.<name>.task_count, osmo.group.<name>.completed,
               osmo.group.<name>.failed

    Tier 2 (optional, requires OSMO_METRICS_SPEC and AZURE_CLIENT_ID):
      osmo.workflow.<name> metrics from spec-driven blob extraction.
    """
    try:
        import mlflow

        groups = query.get("groups", [])
        all_tasks = [t for g in groups for t in g.get("tasks", [])]
        task_count = len(all_tasks)
        completed_tasks = sum(1 for t in all_tasks if str(t.get("status", "")) == "COMPLETED")
        failed_tasks = sum(1 for t in all_tasks if str(t.get("status", "")).startswith("FAILED"))
        success_rate = completed_tasks / task_count if task_count else 0.0
        duration = query.get("duration")

        # --- Tier 1 tags ---
        mlflow.set_tag("osmo.workflow_id", workflow_id)
        mlflow.set_tag("osmo.status", status)
        mlflow.set_tag("osmo.pool", query.get("pool", ""))

        first_failed = next(
            (t for t in all_tasks if str(t.get("status", "")).startswith("FAILED")),
            None,
        )
        if first_failed:
            error_msg = first_failed.get("error") or first_failed.get("message") or first_failed.get("status", "")
            mlflow.set_tag("osmo.first_error", str(error_msg)[:500])

        # --- Tier 1 metrics ---
        mlflow.log_metric("osmo.task_count", task_count)
        mlflow.log_metric("osmo.task_completed", completed_tasks)
        mlflow.log_metric("osmo.failed_tasks", failed_tasks)
        mlflow.log_metric("osmo.task_success_rate", success_rate)
        if duration is not None:
            mlflow.log_metric("osmo.duration_seconds", float(duration))

        # --- Per-group breakdown ---
        for i, group in enumerate(groups):
            g_tasks = group.get("tasks", [])
            g_name = group.get("name") or str(i)
            g_completed = sum(1 for t in g_tasks if str(t.get("status", "")) == "COMPLETED")
            g_failed = sum(1 for t in g_tasks if str(t.get("status", "")).startswith("FAILED"))
            mlflow.log_metric(f"osmo.group.{g_name}.task_count", len(g_tasks))
            mlflow.log_metric(f"osmo.group.{g_name}.completed", g_completed)
            mlflow.log_metric(f"osmo.group.{g_name}.failed", g_failed)

        # --- Task duration statistics ---
        # OSMO WorkflowQueryResponse does not include a pre-computed per-task
        # duration field. Derive elapsed seconds from ISO-8601 timestamps instead,
        # trying common OSMO field name variants in priority order.
        if all_tasks:
            _LOGGER.debug(
                "OSMO task object keys (first task): %s",
                sorted(all_tasks[0].keys()),
            )

        def _parse_ts(s: str) -> datetime:
            """Parse an ISO 8601 timestamp string, with or without timezone info."""
            s = str(s).replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                return datetime.strptime(str(s).rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC)

        def _task_duration_s(task: dict[str, Any]) -> float | None:
            """Return task elapsed seconds or None if timing data unavailable."""
            if task.get("duration") is not None:
                return float(task["duration"])
            for start_key, end_key in (
                ("startedAt", "finishedAt"),
                ("startTime", "endTime"),
                ("start_time", "end_time"),
                ("started_at", "finished_at"),
            ):
                start_raw = task.get(start_key)
                end_raw = task.get(end_key)
                if start_raw and end_raw:
                    try:
                        delta = _parse_ts(str(end_raw)) - _parse_ts(str(start_raw))
                        return max(0.0, delta.total_seconds())
                    except Exception:
                        continue
            return None

        durations = [d for d in (_task_duration_s(t) for t in all_tasks) if d is not None]
        if durations:
            mlflow.log_metric("osmo.task_duration_mean_s", sum(durations) / len(durations))
            mlflow.log_metric("osmo.task_duration_max_s", max(durations))
            sorted_d = sorted(durations)
            p95_idx = max(0, int(len(sorted_d) * 0.95) - 1)
            mlflow.log_metric("osmo.task_duration_p95_s", sorted_d[p95_idx])

        # --- Tier 2 spec-driven workflow metrics ---
        if metrics_spec and output_urls and azure_client_id:
            workflow_metrics = _extract_tier2_metrics(metrics_spec, output_urls, azure_client_id)
            for key, value in workflow_metrics.items():
                mlflow.log_metric(f"osmo.workflow.{key}", value)
            if workflow_metrics:
                _LOGGER.info(
                    "Logged %d spec-driven workflow metric(s): %s",
                    len(workflow_metrics),
                    ", ".join(workflow_metrics),
                )
        elif metrics_spec and not azure_client_id:
            _LOGGER.debug("AZURE_CLIENT_ID not set — skipping spec-driven metrics extraction")

        duration_str = f"{duration:.1f}" if duration is not None else "n/a"
        _LOGGER.info(
            "Logged MLflow metrics — workflow: %s, status: %s, "
            "tasks: %d (completed: %d failed: %d rate: %.2f), groups: %d, duration: %ss",
            workflow_id,
            status,
            task_count,
            completed_tasks,
            failed_tasks,
            success_rate,
            len(groups),
            duration_str,
        )
    except ImportError:
        _LOGGER.info("mlflow not installed — skipping MLflow logging")
    except Exception as exc:
        tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "<not set>")
        _LOGGER.warning(
            "MLflow logging failed (non-fatal): %s  [MLFLOW_TRACKING_URI=%s — ensure azureml-mlflow is in conda deps]",
            exc,
            tracking_uri,
        )


# ---------------------------------------------------------------------------
# AML data asset registration
# ---------------------------------------------------------------------------


def _to_aml_url(url: str) -> str:
    """Convert an OSMO output azure:// URL to an AML-compatible abfss:// URI.

    AML data assets accept abfss, http, https, wasb, wasbs, adl, azureml.
    OSMO uses azure://account/container/path — convert to abfss for AML.

    Examples:
      azure://myacct/mycontainer/path/ → abfss://mycontainer@myacct.dfs.core.windows.net/path/
      https://... → unchanged
      abfss://... → unchanged
    """
    if url.startswith("azure://"):
        rest = url[len("azure://") :]
        parts = rest.split("/", 2)
        if len(parts) >= 2:
            account = parts[0]
            container = parts[1]
            path = parts[2] if len(parts) > 2 else ""
            return f"abfss://{container}@{account}.dfs.core.windows.net/{path}"
    return url


def _register_aml_data_assets(
    output_urls: list[str],
    workflow_id: str,
) -> None:
    """Register OSMO output blob paths as AML data assets (uri_folder).

    Requires AML_SUBSCRIPTION_ID, AML_RESOURCE_GROUP, AML_WORKSPACE_NAME
    to be set, and azure-ai-ml + azure-identity to be installed.
    """
    subscription = os.environ.get("AML_SUBSCRIPTION_ID", "")
    resource_group = os.environ.get("AML_RESOURCE_GROUP", "")
    workspace = os.environ.get("AML_WORKSPACE_NAME", "")

    if not all([subscription, resource_group, workspace]):
        _LOGGER.info(
            "AML_SUBSCRIPTION_ID / AML_RESOURCE_GROUP / AML_WORKSPACE_NAME "
            "not all set — skipping data asset registration"
        )
        return

    try:
        from azure.ai.ml import MLClient
        from azure.ai.ml.constants import AssetTypes
        from azure.ai.ml.entities import Data
        from azure.identity import DefaultAzureCredential
    except ImportError:
        _LOGGER.info("azure-ai-ml not installed — skipping data asset registration")
        return

    try:
        client = MLClient(
            DefaultAzureCredential(),
            subscription_id=subscription,
            resource_group_name=resource_group,
            workspace_name=workspace,
        )
        for idx, url in enumerate(output_urls):
            asset_name = f"osmo-{workflow_id}-output-{idx}"
            aml_url = _to_aml_url(url)
            data_asset = Data(
                name=asset_name,
                path=aml_url,
                type=AssetTypes.URI_FOLDER,
                description=f"OSMO workflow {workflow_id} output {idx} (source: {url})",
            )
            created = client.data.create_or_update(data_asset)
            _LOGGER.info(
                "Registered AML data asset: %s v%s → %s",
                created.name,
                created.version,
                aml_url,
            )
    except Exception as exc:
        _LOGGER.warning("AML data asset registration failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="AML → OSMO proxy: submit OSMO workflow, poll, log to MLflow")
    parser.add_argument(
        "--workflow-yaml",
        default=os.environ.get("WORKFLOW_YAML", _DEFAULT_WORKFLOW_YAML),
        help="Path to the OSMO workflow YAML file (required unless --metrics-only)",
    )
    parser.add_argument(
        "--metrics-only",
        action="store_true",
        default=False,
        help=(
            "Skip OSMO submission and polling. Only run spec-driven metric "
            "extraction against pre-existing output URLs (OSMO_OUTPUT_URLS or "
            "--output-urls)."
        ),
    )
    parser.add_argument(
        "--output-urls",
        default=os.environ.get("OSMO_OUTPUT_URLS", ""),
        help=(
            "Comma-separated azure:// output URLs for metric extraction "
            "or to override URLs parsed from the workflow YAML."
        ),
    )
    parser.add_argument(
        "--pool",
        default=os.environ.get("OSMO_POOL", "default"),
        help="OSMO pool name (default: default)",
    )
    parser.add_argument(
        "--gateway-url",
        default=os.environ.get("OSMO_GATEWAY_URL", _DEFAULT_GATEWAY_URL),
        help="OSMO gateway base URL",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.environ.get("POLL_INTERVAL_SECS", str(_DEFAULT_POLL_INTERVAL_SECS))),
        help="Seconds between status polls (default: 30)",
    )
    parser.add_argument(
        "--set",
        metavar="KEY=VALUE",
        action="append",
        dest="set_variables",
        default=[],
        help=("Set a workflow template variable (may repeat). Format: --set dataset=vda-demo --set run_id=run-001."),
    )
    args = parser.parse_args()

    if args.metrics_only and not args.output_urls:
        parser.error("--metrics-only requires --output-urls or OSMO_OUTPUT_URLS env var")
    if not args.metrics_only and not args.workflow_yaml:
        parser.error("--workflow-yaml or WORKFLOW_YAML environment variable is required")

    # --- Load metrics spec (shared by normal and metrics-only mode) ---
    metrics_spec: dict[str, Any] | None = None
    metrics_spec_raw = os.environ.get("OSMO_METRICS_SPEC", "").strip()
    if metrics_spec_raw:
        try:
            if "\n" in metrics_spec_raw:
                metrics_spec = yaml.safe_load(metrics_spec_raw)
                _LOGGER.info(
                    "Loaded inline metrics spec (%d metric(s))",
                    len((metrics_spec or {}).get("metrics", [])),
                )
            else:
                with open(metrics_spec_raw, encoding="utf-8") as sf:
                    metrics_spec = yaml.safe_load(sf)
                _LOGGER.info(
                    "Loaded metrics spec from %s (%d metric(s))",
                    metrics_spec_raw,
                    len((metrics_spec or {}).get("metrics", [])),
                )
        except Exception as exc:
            _LOGGER.warning("Failed to load OSMO_METRICS_SPEC (ignored): %s", exc)

    # --- metrics-only mode: skip OSMO submission, extract spec metrics ---
    if args.metrics_only:
        output_urls = [u.strip() for u in args.output_urls.split(",") if u.strip()]
        azure_client_id = os.environ.get("AZURE_CLIENT_ID", "")
        if not metrics_spec:
            _LOGGER.warning("--metrics-only set but OSMO_METRICS_SPEC is empty — nothing to log")
            sys.exit(0)
        try:
            import mlflow as _mlflow

            workflow_metrics = _extract_tier2_metrics(metrics_spec, output_urls, azure_client_id)
            for key, value in workflow_metrics.items():
                _mlflow.log_metric(f"osmo.workflow.{key}", value)
            _LOGGER.info(
                "metrics-only: logged %d metric(s): %s",
                len(workflow_metrics),
                ", ".join(workflow_metrics) or "(none)",
            )
        except Exception as exc:
            _LOGGER.warning("metrics-only logging failed (non-fatal): %s", exc)
        sys.exit(0)

    # --- Read workflow YAML ---
    with open(args.workflow_yaml, encoding="utf-8") as fh:
        workflow_yaml = fh.read()

    # --- Build set_variables from env var and --set flags ---
    # OSMO_SET_VARIABLES (JSON array of {name, value} dicts) is parsed to KEY=VALUE strings.
    # CLI --set KEY=VALUE flags are merged in, with priority over env var entries.
    set_vars: list[str] = _parse_set_variables(os.environ.get("OSMO_SET_VARIABLES", ""))
    if set_vars:
        _LOGGER.info("Loaded %d set_variables from OSMO_SET_VARIABLES", len(set_vars))

    if args.set_variables:
        cli_additions: list[str] = []
        for kv in args.set_variables:
            if "=" not in kv:
                _LOGGER.warning("--set %r ignored: expected KEY=VALUE format", kv)
                continue
            k = kv.split("=", 1)[0]
            set_vars = [e for e in set_vars if not e.startswith(k + "=")]
            cli_additions.append(kv)
        set_vars.extend(cli_additions)
        _LOGGER.info(
            "Applied %d --set override(s): %s",
            len(cli_additions),
            ", ".join(cli_additions),
        )

    output_urls = _extract_output_urls(workflow_yaml)

    if args.output_urls:
        output_urls = [u.strip() for u in args.output_urls.split(",") if u.strip()]
        _LOGGER.info("Using %d output URL(s) from --output-urls / OSMO_OUTPUT_URLS", len(output_urls))
    elif output_urls:
        _LOGGER.info("Detected %d output URL(s) in workflow spec", len(output_urls))

    headers = _build_auth_headers()

    # 1. Connectivity check
    _version_check(args.gateway_url, headers)

    # 2. Submit
    workflow_id = _submit_workflow(args.gateway_url, args.pool, workflow_yaml, headers, set_vars or None)

    # 3. Poll until done
    _LOGGER.info("Polling every %ds until workflow finishes...", args.poll_interval)
    query = _poll_until_done(args.gateway_url, workflow_id, headers, args.poll_interval)
    status = query.get("status", "UNKNOWN")
    _LOGGER.info("Workflow %s finished: %s", workflow_id, status)

    # Show tail logs (best-effort, helpful for AML run output)
    tail = _fetch_tail_logs(args.gateway_url, workflow_id, headers)
    if tail:
        _LOGGER.info("--- OSMO workflow logs (last 50 lines) ---\n%s", tail)

    # 4. Set up MLflow and log metrics
    _setup_mlflow()
    _log_to_mlflow(
        workflow_id,
        status,
        query,
        output_urls=output_urls or None,
        azure_client_id=os.environ.get("AZURE_CLIENT_ID", ""),
        metrics_spec=metrics_spec,
    )

    # 5. Register AML data assets for any declared outputs
    if output_urls:
        _register_aml_data_assets(output_urls, workflow_id)

    # 6. Exit with appropriate code
    if status != "COMPLETED":
        _LOGGER.error("OSMO workflow %s did not complete successfully: %s", workflow_id, status)
        sys.exit(1)

    _LOGGER.info("Done. OSMO workflow %s COMPLETED.", workflow_id)


if __name__ == "__main__":
    main()
