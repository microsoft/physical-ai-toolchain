"""Register model artifacts to Azure ML model registry."""

import os
import sys
from pathlib import Path

from azure.ai.ml import MLClient
from azure.ai.ml.constants import AssetTypes
from azure.ai.ml.entities import Model
from azure.identity import DefaultAzureCredential

output_dir = Path(os.environ["OUTPUT_DIR"])
model_name = os.environ["REGISTER_MODEL"]
policy_type = os.environ.get("POLICY_TYPE", "act")
job_name = os.environ.get("JOB_NAME", "lerobot-eval")

artifacts_dir = output_dir / "model_artifacts"
if not artifacts_dir.exists():
    print("No model artifacts found, skipping registration")
    sys.exit(0)

credential = DefaultAzureCredential()
client = MLClient(
    credential,
    os.environ["AZURE_SUBSCRIPTION_ID"],
    os.environ["AZURE_RESOURCE_GROUP"],
    os.environ["AZUREML_WORKSPACE_NAME"],
)

model = Model(
    path=str(artifacts_dir),
    name=model_name,
    description=f"LeRobot {policy_type} policy evaluated in job: {job_name}",
    type=AssetTypes.CUSTOM_MODEL,
    tags={
        "framework": "lerobot",
        "policy_type": policy_type,
        "job_name": job_name,
        "source": "azureml-lerobot-inference",
    },
)

registered = client.models.create_or_update(model)
print(f"Model registered: {registered.name} (version: {registered.version})")
