"""Hydrate and verify a LeRobot dataset or model folder from Azure Blob / Azure ML Data Asset.

Supports three hydration modes for external GPU, OSMO, and Azure VM workers:
  1. AZUREML_DATASET_ASSET (azureml:<name>:<version> or full ARM/azureml URI):
     resolves the Data Asset via Azure ML ARM REST (2024-04-01), binds its
     manifest_sha256 property, downloads from Blob via DefaultAzureCredential,
     and verifies release integrity.
  2. BLOB_URL (https://<account>.blob.core.windows.net/<container>/<prefix>):
     downloads directly from Azure Blob Storage and verifies integrity.
  3. --verify-only: verifies an already-mounted local / shared NFS dataset folder
     (meta/info.json + optional checksums.sha256 / .published.json / manifest_sha256)
     without downloading.

Writes a JSON hydration receipt when HYDRATION_RECEIPT_PATH or --receipt-out is set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

_ARM_API_VERSION = "2024-04-01"
_ARM_SCOPE = "https://management.azure.com/.default"
_HASH_CHUNK_BYTES = 1024 * 1024


def _redact(url: str) -> str:
    return urlparse(url)._replace(query="").geturl()


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_credential() -> Any:
    from azure.identity import DefaultAzureCredential

    kwargs: dict[str, Any] = {}
    if os.environ.get("AZURE_CLIENT_ID"):
        kwargs["managed_identity_client_id"] = os.environ["AZURE_CLIENT_ID"]
    if os.environ.get("AZURE_AUTHORITY_HOST"):
        kwargs["authority"] = os.environ["AZURE_AUTHORITY_HOST"]
    return DefaultAzureCredential(**kwargs) if kwargs else DefaultAzureCredential()


def _arm_get_json(url: str, credential: Any) -> dict[str, Any]:
    token = credential.get_token(_ARM_SCOPE).token
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected ARM response shape from {url}")
    return payload


def _parse_asset_ref(
    asset_ref: str,
    *,
    subscription_id: str | None = None,
    resource_group: str | None = None,
    workspace_name: str | None = None,
) -> tuple[str, str, str, str, str]:
    """Resolve an AML Data Asset reference into (sub, rg, ws, name, version)."""
    cleaned = asset_ref.strip()
    if "@latest" in cleaned or cleaned.endswith(":latest"):
        raise ValueError(f"AZUREML_DATASET_ASSET must pin an explicit version (not @latest): {cleaned}")

    if "/subscriptions/" in cleaned and "/data/" in cleaned and "/versions/" in cleaned:
        after_sub = cleaned.split("/subscriptions/", 1)[1]
        sub, _, rest = after_sub.partition("/resourceGroups/")
        if not rest:
            sub, _, rest = after_sub.partition("/resourcegroups/")
        rg, _, rest = rest.partition("/providers/Microsoft.MachineLearningServices/workspaces/")
        ws, _, rest = rest.partition("/data/")
        name, _, ver = rest.partition("/versions/")
        ver = ver.strip("/")
        if all((sub, rg, ws, name, ver)):
            return sub, rg, ws, name, ver

    if cleaned.startswith("azureml:"):
        cleaned = cleaned[len("azureml:") :]
    parts = cleaned.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Expected azureml:<name>:<version> or full data asset URI, got: {asset_ref}")

    sub = subscription_id or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    rg = resource_group or os.environ.get("AZURE_RESOURCE_GROUP") or os.environ.get("AZUREML_RESOURCE_GROUP", "")
    ws = workspace_name or os.environ.get("AZUREML_WORKSPACE_NAME", "")
    if not all((sub, rg, ws)):
        raise ValueError(
            "Short AZUREML_DATASET_ASSET (azureml:<name>:<version>) requires "
            "AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AZUREML_WORKSPACE_NAME."
        )
    return sub, rg, ws, parts[0], parts[1]


def resolve_aml_data_asset_to_blob_url(
    asset_ref: str,
    *,
    credential: Any | None = None,
) -> dict[str, str]:
    """Resolve an AML Data Asset via ARM 2024-04-01 into its HTTPS Blob URL and lineage properties."""
    sub, rg, ws, name, ver = _parse_asset_ref(asset_ref)
    cred = credential or _build_credential()
    base_ws = (
        f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.MachineLearningServices/workspaces/{ws}"
    )
    asset_doc = _arm_get_json(f"{base_ws}/data/{name}/versions/{ver}?api-version={_ARM_API_VERSION}", cred)
    props = asset_doc.get("properties") or {}
    data_uri = str(props.get("dataUri") or "")
    custom_props = props.get("properties") or {}
    tags = props.get("tags") or {}

    blob_url = ""
    if data_uri.startswith("https://") and ".blob.core.windows.net/" in data_uri:
        blob_url = data_uri
    elif data_uri.startswith("azureml://") and "/datastores/" in data_uri and "/paths/" in data_uri:
        after_ds = data_uri.split("/datastores/", 1)[1]
        ds_name, _, rel_path = after_ds.partition("/paths/")
        ds_doc = _arm_get_json(f"{base_ws}/datastores/{ds_name}?api-version={_ARM_API_VERSION}", cred)
        ds_props = ds_doc.get("properties") or {}
        acct = ds_props.get("accountName")
        cont = ds_props.get("containerName")
        endpoint_suffix = ds_props.get("endpoint") or "core.windows.net"
        if not acct or not cont:
            raise RuntimeError(f"Datastore '{ds_name}' did not expose accountName/containerName")
        blob_url = f"https://{acct}.blob.{endpoint_suffix}/{cont}/{rel_path.strip('/')}"
    else:
        raise RuntimeError(f"Unsupported Data Asset dataUri format for '{name}:{ver}': {_redact(data_uri)}")

    manifest_sha = str(custom_props.get("manifest_sha256") or tags.get("manifest_sha256") or "")
    release_id = str(custom_props.get("release_id") or custom_props.get("release_name") or ver)
    return {
        "blob_url": blob_url,
        "asset_name": name,
        "asset_version": ver,
        "release_id": release_id,
        "manifest_sha256": manifest_sha,
    }


def safe_local_path(dataset_root: pathlib.Path, blob_name: str, rel: str) -> pathlib.Path | None:
    if rel.startswith("/") or "\\" in rel:
        print(f"  WARN: skipping unsafe blob path: {blob_name}")
        return None
    candidate = (dataset_root / rel).resolve()
    try:
        candidate.relative_to(dataset_root)
    except ValueError:
        print(f"  WARN: skipping blob that escapes dataset root: {blob_name}")
        return None
    return candidate


def _download_prefix(client: Any, prefix: str, dataset_root: pathlib.Path) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    for blob in client.list_blobs(name_starts_with=prefix):
        rel = blob.name[len(prefix) :].lstrip("/")
        if not rel or ".cache/" in rel or rel.endswith(".lock"):
            continue
        local = safe_local_path(dataset_root, blob.name, rel)
        if local is None:
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        with local.open("wb") as fh:
            for chunk in client.download_blob(blob).chunks():
                fh.write(chunk)
                total_bytes += len(chunk)
        count += 1
        if count <= 20:
            print(f"  {blob.name} -> {local}")
    if count > 20:
        print(f"  ... and {count - 20} more files")
    return count, total_bytes


def download_from_blob_url(
    url: str,
    dataset_root: pathlib.Path,
    *,
    forbid_static_secrets: bool = False,
) -> tuple[int, int]:
    from azure.core.exceptions import HttpResponseError
    from azure.storage.blob import ContainerClient

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit(f"Invalid BLOB_URL: {_redact(url)}")

    path_parts = parsed.path.lstrip("/").split("/", 1)
    if len(path_parts) < 2 or not path_parts[0] or not path_parts[1]:
        raise SystemExit(f"BLOB_URL must include account/container/prefix, got: {_redact(url)}")

    container = path_parts[0]
    prefix = path_parts[1].rstrip("/") + "/"
    endpoint = f"https://{parsed.netloc}"
    sas_token = parsed.query if parsed.query else None

    if forbid_static_secrets and sas_token:
        raise SystemExit(f"SAS token query string is forbidden (FORBID_STATIC_SECRETS=true): {_redact(url)}")

    dataset_root = dataset_root.resolve()
    dataset_root.mkdir(parents=True, exist_ok=True)

    if sas_token:
        sas_client = ContainerClient(endpoint, container, credential=sas_token)
        count, total_bytes = _download_prefix(sas_client, prefix, dataset_root)
        print(f"Downloaded {count} files to {dataset_root}/ using SAS token")
        return count, total_bytes

    cred_client = None
    try:
        cred = _build_credential()
        cred_client = ContainerClient(endpoint, container, credential=cred)
    except Exception as err:
        if forbid_static_secrets:
            raise SystemExit(f"DefaultAzureCredential initialization failed: {err}") from err
        print(f"DefaultAzureCredential unavailable ({err}); will try anonymous access")

    if cred_client is not None:
        try:
            count, total_bytes = _download_prefix(cred_client, prefix, dataset_root)
            print(f"Downloaded {count} files to {dataset_root}/")
            return count, total_bytes
        except HttpResponseError as err:
            if forbid_static_secrets or getattr(err, "error_code", None) not in {
                "AuthorizationFailure",
                "AuthenticationFailed",
            }:
                raise
            print("Credentialed blob access failed; retrying anonymous access")

    anon_client = ContainerClient(endpoint, container)
    count, total_bytes = _download_prefix(anon_client, prefix, dataset_root)
    print(f"Downloaded {count} files to {dataset_root}/")
    return count, total_bytes


def verify_hydrated_dataset(
    dataset_root: pathlib.Path,
    *,
    expected_manifest_sha256: str = "",
    require_lerobot_schema: bool = True,
) -> dict[str, Any]:
    """Verify LeRobot meta/info.json and optional sealed Dataviewer release checksums (ST-4)."""
    root = dataset_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"Dataset directory does not exist: {root}")

    info: dict[str, Any] = {}
    info_path = root / "meta" / "info.json"
    if require_lerobot_schema:
        if not info_path.is_file():
            raise SystemExit(f"Missing required LeRobot schema file: {info_path}")
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"Malformed JSON in {info_path}: {exc}") from exc

    manifest_candidates = (
        root / "metadata" / "release-manifest.json",
        root / "release" / "manifest.json",
    )
    manifest_file = next((p for p in manifest_candidates if p.is_file()), None)
    actual_manifest_sha = _sha256_file(manifest_file) if manifest_file else ""

    if expected_manifest_sha256:
        expected_norm = expected_manifest_sha256.strip().lower()
        if not manifest_file:
            raise SystemExit(
                f"Expected release manifest SHA-256 ({expected_norm[:12]}...), "
                f"but no release manifest found under {root}"
            )
        if actual_manifest_sha != expected_norm:
            raise SystemExit(
                f"Dataset manifest SHA-256 mismatch at {manifest_file}: "
                f"expected {expected_norm}, got {actual_manifest_sha}"
            )

    checksums_verified = 0
    checksums_path = root / "checksums.sha256"
    if checksums_path.is_file():
        for raw_line in checksums_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise SystemExit(f"Malformed line in {checksums_path}: {raw_line!r}")
            expected_hex = parts[0].strip().lower()
            rel_path = parts[1].lstrip("*").strip()
            target = safe_local_path(root, rel_path, rel_path)
            if target is None or not target.is_file():
                raise SystemExit(f"File listed in checksums.sha256 missing on disk: {rel_path}")
            actual_hex = _sha256_file(target)
            if actual_hex != expected_hex:
                raise SystemExit(f"Corrupt dataset file {rel_path}: expected {expected_hex}, got {actual_hex}")
            checksums_verified += 1

    all_files = [p for p in root.rglob("*") if p.is_file()]
    disk_bytes = sum(p.stat().st_size for p in all_files)
    return {
        "verified": True,
        "file_count": len(all_files),
        "total_bytes": disk_bytes,
        "checksums_verified": checksums_verified,
        "published_marker_present": (root / ".published.json").is_file(),
        "manifest_sha256": actual_manifest_sha,
        "robot_type": info.get("robot_type"),
        "total_episodes": info.get("total_episodes"),
        "total_frames": info.get("total_frames"),
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hydrate and verify PAI dataset or model from Azure Blob / AML")
    parser.add_argument("--blob-url", default=os.environ.get("BLOB_URL", ""), help="Source Azure Blob folder URL")
    parser.add_argument(
        "--dataset-asset",
        default=os.environ.get("AZUREML_DATASET_ASSET", ""),
        help="Pinned Azure ML Data Asset (azureml:<name>:<version>)",
    )
    parser.add_argument(
        "--dataset-path",
        default=os.environ.get("DATASET_PATH", "/data/dataset"),
        help="Destination local or mounted NFS directory",
    )
    parser.add_argument(
        "--expected-manifest-sha256",
        default=os.environ.get("DATASET_MANIFEST_SHA256", ""),
        help="Expected SHA-256 of release manifest",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip download and verify an already-staged local or NFS dataset directory",
    )
    parser.add_argument(
        "--skip-schema-check",
        action="store_true",
        help="Skip LeRobot meta/info.json check (use when hydrating raw model weights)",
    )
    parser.add_argument(
        "--forbid-static-secrets",
        action="store_true",
        default=os.environ.get("FORBID_STATIC_SECRETS", "false").lower() == "true",
        help="Reject SAS tokens and disable anonymous fallback",
    )
    parser.add_argument(
        "--receipt-out",
        default=os.environ.get("HYDRATION_RECEIPT_PATH", ""),
        help="Optional path to write hydration-receipt.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_root = pathlib.Path(args.dataset_path).resolve()
    expected_manifest_sha = args.expected_manifest_sha256
    source_kind = "local_mount"
    dataset_version = os.environ.get("DATASET_VERSION", "")
    asset_meta: dict[str, str] = {}

    started = time.monotonic()
    downloaded_files = 0
    downloaded_bytes = 0

    if not args.verify_only:
        blob_url = args.blob_url
        if args.dataset_asset:
            source_kind = "aml_data_asset"
            asset_meta = resolve_aml_data_asset_to_blob_url(args.dataset_asset)
            blob_url = asset_meta["blob_url"]
            dataset_version = dataset_version or asset_meta["release_id"]
            expected_manifest_sha = expected_manifest_sha or asset_meta["manifest_sha256"]
            print(
                f"Resolved AML Data Asset {asset_meta['asset_name']}:{asset_meta['asset_version']} "
                f"-> {_redact(blob_url)}"
            )
        elif blob_url:
            source_kind = "blob_url"
        else:
            raise SystemExit("Specify BLOB_URL, AZUREML_DATASET_ASSET, or --verify-only")

        downloaded_files, downloaded_bytes = download_from_blob_url(
            blob_url,
            dataset_root,
            forbid_static_secrets=args.forbid_static_secrets,
        )
        if downloaded_files == 0:
            raise SystemExit(f"0 files downloaded from {_redact(blob_url)}")

    elapsed_s = max(time.monotonic() - started, 1e-6)
    verification = verify_hydrated_dataset(
        dataset_root,
        expected_manifest_sha256=expected_manifest_sha,
        require_lerobot_schema=not args.skip_schema_check,
    )
    effective_bytes = downloaded_bytes if downloaded_bytes > 0 else int(verification["total_bytes"])
    throughput_mb_s = round((effective_bytes / (1024 * 1024)) / elapsed_s, 2)

    receipt: dict[str, Any] = {
        "schema": "pai-hydration-receipt/v1",
        "timestamp": datetime.now(UTC).isoformat(),
        "source_kind": source_kind,
        "dataset_path": str(dataset_root),
        "dataset_asset": args.dataset_asset or None,
        "dataset_version": dataset_version or None,
        "downloaded_files": downloaded_files,
        "elapsed_s": round(elapsed_s, 3),
        "throughput_mb_s": throughput_mb_s,
        **verification,
    }
    if args.receipt_out:
        out_path = pathlib.Path(args.receipt_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote hydration receipt to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
