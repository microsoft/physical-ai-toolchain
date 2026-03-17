"""Download model from AzureML model registry."""

import os
from pathlib import Path

from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = MLClient(
    credential,
    os.environ["AZURE_SUBSCRIPTION_ID"],
    os.environ["AZURE_RESOURCE_GROUP"],
    os.environ["AZUREML_WORKSPACE_NAME"],
)

model_name = os.environ["AML_MODEL_NAME"]
model_version = os.environ["AML_MODEL_VERSION"]
download_dir = Path("/tmp/aml-model")
download_dir.mkdir(parents=True, exist_ok=True)

print(f"Downloading {model_name}:{model_version}...")
client.models.download(name=model_name, version=model_version, download_path=str(download_dir))

model_path = download_dir / model_name
if not model_path.exists():
    model_path = download_dir

for candidate in [model_path] + (list(model_path.iterdir()) if model_path.is_dir() else []):
    if candidate.is_dir() and (list(candidate.glob("*.safetensors")) or list(candidate.glob("*.bin"))):
        model_path = candidate
        break

with open("/tmp/aml_model_path.env", "w") as f:
    f.write(f"AML_MODEL_PATH={model_path}\n")

print(f"Model downloaded to: {model_path}")
for fp in sorted(model_path.iterdir()):
    print(f"  {fp.name} ({fp.stat().st_size / 1024 / 1024:.1f} MB)")
