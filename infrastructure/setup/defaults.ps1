# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Default configuration for 002-setup deployment scripts.
.DESCRIPTION
    Override via command-line parameters or environment variables.
    Dot-source this file to load defaults into the current scope.
#>

# Helm Chart Versions
$Script:GPU_OPERATOR_VERSION = $env:GPU_OPERATOR_VERSION ?? 'v24.9.1'
$Script:KAI_SCHEDULER_VERSION = $env:KAI_SCHEDULER_VERSION ?? 'v0.5.5'
$Script:OSMO_CHART_VERSION = $env:OSMO_CHART_VERSION ?? '1.0.0'
$Script:OSMO_IMAGE_VERSION = $env:OSMO_IMAGE_VERSION ?? '6.0.0'

# Kubernetes Namespaces
$Script:NS_OSMO = $env:NS_OSMO ?? 'osmo'
$Script:NS_OSMO_CONTROL_PLANE = $env:NS_OSMO_CONTROL_PLANE ?? 'osmo-control-plane'
$Script:NS_OSMO_OPERATOR = $env:NS_OSMO_OPERATOR ?? 'osmo-operator'
$Script:NS_OSMO_WORKFLOWS = $env:NS_OSMO_WORKFLOWS ?? 'osmo-workflows'
$Script:NS_AZUREML = $env:NS_AZUREML ?? 'azureml'
$Script:NS_GPU_OPERATOR = $env:NS_GPU_OPERATOR ?? 'gpu-operator'
$Script:NS_KAI_SCHEDULER = $env:NS_KAI_SCHEDULER ?? 'kai-scheduler'

# Secret Names
$Script:SECRET_MEK = $env:SECRET_MEK ?? 'mek-config'
$Script:SECRET_POSTGRES = $env:SECRET_POSTGRES ?? 'db-secret'
$Script:SECRET_REDIS = $env:SECRET_REDIS ?? 'redis-secret'

# Timeouts
$Script:TIMEOUT_DEPLOY = $env:TIMEOUT_DEPLOY ?? '600s'
$Script:TIMEOUT_WAIT = $env:TIMEOUT_WAIT ?? '300'

# Helm Repositories
$Script:HELM_REPO_NVIDIA = $env:HELM_REPO_NVIDIA ?? 'https://helm.ngc.nvidia.com/nvidia'
$Script:HELM_REPO_GPU_OPERATOR = $env:HELM_REPO_GPU_OPERATOR ?? 'https://helm.ngc.nvidia.com/nvidia'
$Script:HELM_REPO_KAI = $env:HELM_REPO_KAI ?? 'https://nvidia.github.io/k8s-device-scheduler/'
$Script:HELM_REPO_OSMO = $env:HELM_REPO_OSMO ?? 'https://helm.ngc.nvidia.com/nvidia/osmo'

# Default Terraform Directory (relative to 002-setup)
$Script:DEFAULT_TF_DIR = $env:DEFAULT_TF_DIR ?? '../001-iac'

# AzureML Extension Configuration
$Script:AZUREML_EXTENSION_NAME = $env:AZUREML_EXTENSION_NAME ?? 'aml-extension'
$Script:AZUREML_EXTENSION_VERSION = $env:AZUREML_EXTENSION_VERSION ?? '1.3.1'
$Script:AZUREML_RELEASE_NS = $env:AZUREML_RELEASE_NS ?? 'azureml'
$Script:AZUREML_RELAY_NS = $env:AZUREML_RELAY_NS ?? 'azureml-hybrid-relay'

# OSMO Component Names
$Script:OSMO_SERVICE_CHART = $env:OSMO_SERVICE_CHART ?? 'osmo-service'
$Script:OSMO_ROUTER_CHART = $env:OSMO_ROUTER_CHART ?? 'osmo-router'
$Script:OSMO_WEBUI_CHART = $env:OSMO_WEBUI_CHART ?? 'osmo-web-ui'
$Script:OSMO_BACKEND_CHART = $env:OSMO_BACKEND_CHART ?? 'backend-operator'

# Workload Identity Defaults
$Script:WORKFLOW_SERVICE_ACCOUNT = $env:WORKFLOW_SERVICE_ACCOUNT ?? 'osmo-workflow'
$Script:WI_FEDERATED_SUBJECT = $env:WI_FEDERATED_SUBJECT ?? 'system:serviceaccount'

# GPU Instance Type
$Script:GPU_INSTANCE_TYPE = $env:GPU_INSTANCE_TYPE ?? 'Standard_NV36ads_A10_v5'

# Dataset Configuration Defaults
$Script:DATASET_CONTAINER_NAME = $env:DATASET_CONTAINER_NAME ?? 'datasets'
$Script:DATASET_BUCKET_NAME = $env:DATASET_BUCKET_NAME ?? 'training'
