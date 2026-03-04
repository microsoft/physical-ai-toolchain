"""Bootstrap Azure ML MLflow tracking."""

import os
import sys

import mlflow
from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = MLClient(
    credential,
    os.environ["AZURE_SUBSCRIPTION_ID"],
    os.environ["AZURE_RESOURCE_GROUP"],
    os.environ["AZUREML_WORKSPACE_NAME"],
)

workspace = client.workspaces.get(os.environ["AZUREML_WORKSPACE_NAME"])
tracking_uri = workspace.mlflow_tracking_uri

if not tracking_uri:
    print("ERROR: Azure ML workspace does not expose MLflow tracking URI")
    sys.exit(1)

mlflow.set_tracking_uri(tracking_uri)

experiment_name = os.environ.get("EXPERIMENT_NAME", "")
if not experiment_name or experiment_name == "none":
    experiment_name = f"lerobot-{os.environ.get('POLICY_TYPE', 'act')}-inference"
mlflow.set_experiment(experiment_name)

with open("/tmp/mlflow_config.env", "w") as f:
    f.write(f"MLFLOW_TRACKING_URI={tracking_uri}\n")
    f.write(f"MLFLOW_EXPERIMENT_NAME={experiment_name}\n")

print(f"MLflow configured: {experiment_name}")
