"""Download and verify a LeRobot dataset from Azure Blob Storage."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

_VERIFIER_ROOT = Path(__file__).resolve().parents[3] / "training" / "il" / "scripts" / "lerobot"
if str(_VERIFIER_ROOT) not in sys.path:
    sys.path.insert(0, str(_VERIFIER_ROOT))

from release_verifier import verify_release  # noqa: E402

_DOWNLOAD_MAX_CONCURRENCY = 4


def download_dataset() -> Path:
    """Stream one Blob prefix into staging and atomically publish it locally."""
    account = os.environ["BLOB_STORAGE_ACCOUNT"]
    container = os.environ.get("BLOB_STORAGE_CONTAINER", "datasets")
    prefix = os.environ["BLOB_PREFIX"].strip("/")
    data_root = Path(os.environ.get("DATA_ROOT", "/workspace/data"))
    local_root = data_root / prefix.replace("/", "_")
    if local_root.exists():
        raise FileExistsError(f"Dataset already exists at {local_root}; refusing to overwrite")
    staged = local_root.with_name(f".{local_root.name}.new")
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)

    credential = DefaultAzureCredential()
    url = f"https://{account}.blob.core.windows.net/{container}"
    client = ContainerClient.from_container_url(url, credential=credential)
    prefix_with_separator = f"{prefix}/"
    downloaded = 0
    try:
        for blob in client.list_blobs(name_starts_with=prefix_with_separator):
            relative_name = blob.name[len(prefix_with_separator) :]
            relative_path = Path(relative_name)
            if not relative_name or relative_path.is_absolute() or ".." in relative_path.parts:
                if relative_name:
                    raise ValueError(f"Unsafe blob path: {blob.name}")
                continue
            local_path = staged / relative_path
            if not local_path.resolve().is_relative_to(staged.resolve()):
                raise ValueError(f"Blob path escapes staging: {blob.name}")
            local_path.parent.mkdir(parents=True, exist_ok=True)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f"{local_path.name}.", suffix=".tmp", dir=local_path.parent
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(file_descriptor, "wb") as handle:
                    stream = client.download_blob(
                        blob.name,
                        max_concurrency=_DOWNLOAD_MAX_CONCURRENCY,
                        validate_content=True,
                    )
                    written = stream.readinto(handle)
                if blob.size is not None and written != blob.size:
                    raise RuntimeError(f"Short read for {blob.name}: wrote {written} bytes, expected {blob.size}")
                os.replace(temporary_path, local_path)
            finally:
                temporary_path.unlink(missing_ok=True)
            downloaded += 1

        if os.environ.get("DATASET_TRUST", "unverified").strip().lower() == "verified":
            verify_release(staged, expected_target_format=("lerobot", "3.0"))
        staged.rename(local_root)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise

    config_path = Path(os.environ.get("DATASET_CONFIG_PATH", "/tmp/dataset_path.env"))
    config_path.write_text(f"DATASET_DIR={local_root}\n", encoding="utf-8")
    print(f"Downloaded {downloaded} files to {local_root}")
    return local_root


if __name__ == "__main__":
    download_dataset()
