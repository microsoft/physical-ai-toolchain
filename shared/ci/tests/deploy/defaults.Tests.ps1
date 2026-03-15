#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# defaults.Tests.ps1
#
# Purpose: Pester tests for deploy default configuration values
# Author: Edge AI Team

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

Describe 'Default configuration values' -Tag 'Unit' {
    BeforeAll {
        $script:EnvVarsToTest = @(
            'GPU_OPERATOR_VERSION', 'KAI_SCHEDULER_VERSION', 'OSMO_CHART_VERSION',
            'OSMO_IMAGE_VERSION', 'NS_OSMO', 'NS_GPU_OPERATOR',
            'SECRET_MEK', 'TIMEOUT_DEPLOY', 'TIMEOUT_WAIT',
            'HELM_REPO_NVIDIA', 'DEFAULT_TF_DIR',
            'AZUREML_EXTENSION_NAME', 'OSMO_SERVICE_CHART',
            'WORKFLOW_SERVICE_ACCOUNT', 'GPU_INSTANCE_TYPE',
            'DATASET_CONTAINER_NAME'
        )
        $script:SavedVars = @{}
        foreach ($name in $script:EnvVarsToTest) {
            $script:SavedVars[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            Remove-Item "env:$name" -ErrorAction SilentlyContinue
        }

        . $PSScriptRoot/../../../../infrastructure/setup/defaults.ps1
    }

    AfterAll {
        foreach ($entry in $script:SavedVars.GetEnumerator()) {
            if ($null -ne $entry.Value) {
                Set-Item "env:$($entry.Key)" $entry.Value
            }
            else {
                Remove-Item "env:$($entry.Key)" -ErrorAction SilentlyContinue
            }
        }
    }

    It 'Sets GPU_OPERATOR_VERSION default' {
        $Script:GPU_OPERATOR_VERSION | Should -Be 'v24.9.1'
    }

    It 'Sets KAI_SCHEDULER_VERSION default' {
        $Script:KAI_SCHEDULER_VERSION | Should -Be 'v0.5.5'
    }

    It 'Sets OSMO_CHART_VERSION default' {
        $Script:OSMO_CHART_VERSION | Should -Be '1.0.0'
    }

    It 'Sets OSMO_IMAGE_VERSION default' {
        $Script:OSMO_IMAGE_VERSION | Should -Be '6.0.0'
    }

    It 'Sets NS_OSMO default' {
        $Script:NS_OSMO | Should -Be 'osmo'
    }

    It 'Sets NS_GPU_OPERATOR default' {
        $Script:NS_GPU_OPERATOR | Should -Be 'gpu-operator'
    }

    It 'Sets SECRET_MEK default' {
        $Script:SECRET_MEK | Should -Be 'mek-config'
    }

    It 'Sets TIMEOUT_DEPLOY default' {
        $Script:TIMEOUT_DEPLOY | Should -Be '600s'
    }

    It 'Sets TIMEOUT_WAIT default' {
        $Script:TIMEOUT_WAIT | Should -Be '300'
    }

    It 'Sets HELM_REPO_NVIDIA default' {
        $Script:HELM_REPO_NVIDIA | Should -Be 'https://helm.ngc.nvidia.com/nvidia'
    }

    It 'Sets DEFAULT_TF_DIR default' {
        $Script:DEFAULT_TF_DIR | Should -Be '../001-iac'
    }

    It 'Sets AZUREML_EXTENSION_NAME default' {
        $Script:AZUREML_EXTENSION_NAME | Should -Be 'aml-extension'
    }

    It 'Sets OSMO_SERVICE_CHART default' {
        $Script:OSMO_SERVICE_CHART | Should -Be 'osmo-service'
    }

    It 'Sets WORKFLOW_SERVICE_ACCOUNT default' {
        $Script:WORKFLOW_SERVICE_ACCOUNT | Should -Be 'osmo-workflow'
    }

    It 'Sets GPU_INSTANCE_TYPE default' {
        $Script:GPU_INSTANCE_TYPE | Should -Be 'Standard_NV36ads_A10_v5'
    }

    It 'Sets DATASET_CONTAINER_NAME default' {
        $Script:DATASET_CONTAINER_NAME | Should -Be 'datasets'
    }
}

Describe 'Environment variable overrides' -Tag 'Unit' {
    BeforeAll {
        $script:OverrideVars = @{
            GPU_OPERATOR_VERSION = $env:GPU_OPERATOR_VERSION
            NS_OSMO              = $env:NS_OSMO
            TIMEOUT_DEPLOY       = $env:TIMEOUT_DEPLOY
            GPU_INSTANCE_TYPE    = $env:GPU_INSTANCE_TYPE
        }

        $env:GPU_OPERATOR_VERSION = 'v99.0.0'
        $env:NS_OSMO = 'custom-namespace'
        $env:TIMEOUT_DEPLOY = '999s'
        $env:GPU_INSTANCE_TYPE = 'Standard_Custom_VM'

        . $PSScriptRoot/../../../../infrastructure/setup/defaults.ps1
    }

    AfterAll {
        foreach ($entry in $script:OverrideVars.GetEnumerator()) {
            if ($null -ne $entry.Value) {
                Set-Item "env:$($entry.Key)" $entry.Value
            }
            else {
                Remove-Item "env:$($entry.Key)" -ErrorAction SilentlyContinue
            }
        }
    }

    It 'Overrides GPU_OPERATOR_VERSION from env' {
        $Script:GPU_OPERATOR_VERSION | Should -Be 'v99.0.0'
    }

    It 'Overrides NS_OSMO from env' {
        $Script:NS_OSMO | Should -Be 'custom-namespace'
    }

    It 'Overrides TIMEOUT_DEPLOY from env' {
        $Script:TIMEOUT_DEPLOY | Should -Be '999s'
    }

    It 'Overrides GPU_INSTANCE_TYPE from env' {
        $Script:GPU_INSTANCE_TYPE | Should -Be 'Standard_Custom_VM'
    }

    It 'Preserves non-overridden defaults' {
        $Script:KAI_SCHEDULER_VERSION | Should -Be 'v0.5.5'
    }
}
