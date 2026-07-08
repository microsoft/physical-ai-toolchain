"""Download a LeRobot dataset from Azure Blob Storage."""

from __future__ import annotations

import os
import pathlib
from urllib.parse import urlparse

from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient


def _redact(u: str) -> str:
    # Drop the query string so a SAS signature never reaches the logs.
    return urlparse(u)._replace(query="").geturl()


url = os.environ["BLOB_URL"]
parsed = urlparse(url)
if parsed.scheme != "https" or not parsed.netloc:
    raise SystemExit(f"Invalid BLOB_URL: {_redact(url)}")

path_parts = parsed.path.lstrip("/").split("/", 1)
if len(path_parts) < 2 or not path_parts[0] or not path_parts[1]:
    raise SystemExit(f"BLOB_URL must include account/container/prefix, got: {_redact(url)}")

container = path_parts[0]
prefix = path_parts[1].rstrip("/")
endpoint = f"https://{parsed.netloc}"
sas_token = parsed.query if parsed.query else None

DATASET_ROOT = pathlib.Path(os.environ.get("DATASET_PATH", "/data/dataset")).resolve()
DATASET_ROOT.mkdir(parents=True, exist_ok=True)


def safe_local_path(blob_name: str, rel: str) -> pathlib.Path | None:
    # Reject absolute paths and any segment that would escape the root.
    if rel.startswith("/") or "\\" in rel:
        print(f"  WARN: skipping unsafe blob path: {blob_name}")
        return None
    candidate = (DATASET_ROOT / rel).resolve()
    try:
        candidate.relative_to(DATASET_ROOT)
    except ValueError:
        print(f"  WARN: skipping blob that escapes dataset root: {blob_name}")
        return None
    return candidate


def download_with_client(client: ContainerClient) -> int:
    count = 0
    for blob in client.list_blobs(name_starts_with=prefix):
        rel = blob.name[len(prefix) :].lstrip("/")
        if not rel:
            continue
        local = safe_local_path(blob.name, rel)
        if local is None:
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(local, "wb") as f:
            f.write(client.download_blob(blob).readall())
        count += 1
        if count <= 20:
            print(f"  {blob.name} -> {local}")
    if count > 20:
        print(f"  ... and {count - 20} more files")
    return count


if sas_token:
    sas_client = ContainerClient(endpoint, container, credential=sas_token)
    count = download_with_client(sas_client)
    print(f"Downloaded {count} files to {DATASET_ROOT}/ using SAS token")
    raise SystemExit(0)

cred_client = None
try:
    cred = DefaultAzureCredential()
    cred_client = ContainerClient(endpoint, container, credential=cred)
except Exception as err:
    # No managed identity / usable credential in this container; fall through to
    # the anonymous-access path below rather than aborting the download.
    print(f"DefaultAzureCredential unavailable ({err}); will try anonymous access")

if cred_client is not None:
    try:
        count = download_with_client(cred_client)
        print(f"Downloaded {count} files to {DATASET_ROOT}/")
        raise SystemExit(0)
    except HttpResponseError as err:
        if getattr(err, "error_code", None) not in {"AuthorizationFailure", "AuthenticationFailed"}:
            raise
        print("Credentialed blob access failed; retrying anonymous access")

anon_client = ContainerClient(endpoint, container)
count = download_with_client(anon_client)
print(f"Downloaded {count} files to {DATASET_ROOT}/")
