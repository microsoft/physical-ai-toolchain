"""Download dataset from Azure Blob Storage."""

import os
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

account = os.environ["BLOB_STORAGE_ACCOUNT"]
container = os.environ.get("BLOB_STORAGE_CONTAINER", "datasets")
prefix = os.environ["BLOB_PREFIX"]
data_root = Path(os.environ.get("DATA_ROOT", "/workspace/data"))
local_root = data_root / prefix.replace("/", "_")
local_root.mkdir(parents=True, exist_ok=True)

credential = DefaultAzureCredential()
url = f"https://{account}.blob.core.windows.net/{container}"
client = ContainerClient.from_container_url(url, credential=credential)

blobs = list(client.list_blobs(name_starts_with=prefix))
print(f"Found {len(blobs)} blobs under {prefix}")

for blob in blobs:
    rel = blob.name[len(prefix) :].lstrip("/")
    if not rel:
        continue
    local_path = local_root / rel
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(client.download_blob(blob.name).readall())

config_path = Path(os.environ.get("DATASET_CONFIG_PATH", "/tmp/dataset_path.env"))
with config_path.open("w") as f:
    f.write(f"DATASET_DIR={local_root}\n")

print(f"Dataset downloaded to: {local_root}")
