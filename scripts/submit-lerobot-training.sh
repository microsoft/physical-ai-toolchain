source .env && osmo workflow submit workflows/osmo/lerobot-train.yaml \
  --set dataset_repo_id=alizaidi/so101-multi-pick-table-13_20260115_171614 \
        policy_type=act \
        output_dir=/workspace/outputs/train/act_so101_pick_table13 \
        job_name=act_so101_pick_table13 \
        policy_repo_id=alizaidi/act_so101_pick_table13 \
        mlflow_enable=true \
        wandb_enable=true \
        azure_subscription_id=$AZURE_SUBSCRIPTION_ID \
        azure_resource_group=$AZURE_RESOURCE_GROUP \
        azure_workspace_name=$AZUREML_WORKSPACE_NAME

source .env && osmo workflow submit ../workflows/osmo/lerobot-train.yaml \
  --set dataset_repo_id=alizaidi/leisaac-pick-orange \
        policy_type=act \
        output_dir=/workspace/outputs/train/act_so101_pick_oranges \
        job_name=act_so101_pick_oranges \
        policy_repo_id=alizaidi/act_so101_pick_oranges \
        mlflow_enable=true \
        wandb_enable=true \
        azure_subscription_id=$AZURE_SUBSCRIPTION_ID \
        azure_resource_group=$AZURE_RESOURCE_GROUP \
        azure_workspace_name=$AZUREML_WORKSPACE_NAME

source .env && osmo workflow submit ../workflows/osmo/lerobot-train.yaml \
  --set dataset_repo_id=LightwheelAI/leisaac-pick-orange \
        policy_type=act \
        output_dir=/workspace/outputs/train/act_so101_pick_oranges \
        job_name=act_so101_pick_oranges \
        policy_repo_id=alizaidi/act_so101_pick_oranges \
        lerobot_version=0.3.1 \
        mlflow_enable=true \
        wandb_enable=false \
        azure_subscription_id=$AZURE_SUBSCRIPTION_ID \
        azure_resource_group=$AZURE_RESOURCE_GROUP \
        azure_workspace_name=$AZUREML_WORKSPACE_NAME
