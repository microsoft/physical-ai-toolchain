"""Register the latest LeRobot checkpoint to Azure ML model registry.

Reads OUTPUT_DIR, REGISTER_CHECKPOINT, POLICY_TYPE, AZURE_SUBSCRIPTION_ID,
AZURE_RESOURCE_GROUP, and AZUREML_WORKSPACE_NAME from the environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from azure.ai.ml import MLClient
from azure.ai.ml.constants import AssetTypes
from azure.ai.ml.entities import Model
from azure.identity import DefaultAzureCredential


def main() -> int:
    output_dir = Path(os.environ["OUTPUT_DIR"])
    checkpoint_dirs = sorted(
        output_dir.glob("checkpoints/*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not checkpoint_dirs:
        pretrained = output_dir / "pretrained_model"
        checkpoint_path = pretrained if pretrained.exists() else None
    else:
        pretrained = checkpoint_dirs[0] / "pretrained_model"
        checkpoint_path = pretrained if pretrained.exists() else checkpoint_dirs[0]

    if not checkpoint_path:
        print("No checkpoints found")
        return 0

    policy_type = os.environ.get("POLICY_TYPE", "act")
    credential = DefaultAzureCredential()
    client = MLClient(
        credential,
        os.environ["AZURE_SUBSCRIPTION_ID"],
        os.environ["AZURE_RESOURCE_GROUP"],
        os.environ["AZUREML_WORKSPACE_NAME"],
    )
    model = Model(
        path=str(checkpoint_path),
        name=os.environ["REGISTER_CHECKPOINT"],
        description=f"LeRobot {policy_type} policy",
        type=AssetTypes.CUSTOM_MODEL,
        tags={"framework": "lerobot", "policy_type": policy_type, "source": "azureml-job"},
    )
    registered = client.models.create_or_update(model)
    print(f"Model registered: {registered.name} v{registered.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
