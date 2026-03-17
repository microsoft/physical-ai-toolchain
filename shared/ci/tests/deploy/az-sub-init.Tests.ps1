#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# az-sub-init.Tests.ps1
#
# Purpose: Pester tests for Azure subscription initialization functions
# Author: Edge AI Team

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    $script:SavedArmSubId = $env:ARM_SUBSCRIPTION_ID

    function az { }
    Mock az {
        $global:LASTEXITCODE = 0
        return 'mock-subscription-id'
    }
    Mock Get-Command { [PSCustomObject]@{ Name = 'az' } } -ParameterFilter { $Name -eq 'az' }
    Mock Write-Host {}

    . $PSScriptRoot/../../../../infrastructure/terraform/prerequisites/az-sub-init.ps1
}

AfterAll {
    $env:ARM_SUBSCRIPTION_ID = $script:SavedArmSubId
    $global:LASTEXITCODE = 0
}

Describe 'Get-CurrentSubscriptionId' -Tag 'Unit' {
    Context 'When az account show succeeds' {
        BeforeAll {
            Mock az { return 'abc-123-def-456' }
        }

        It 'Returns the subscription ID' {
            Get-CurrentSubscriptionId | Should -Be 'abc-123-def-456'
        }
    }

    Context 'When az account show throws' {
        BeforeAll {
            Mock az { throw 'Not logged in' }
        }

        It 'Returns null' {
            Get-CurrentSubscriptionId | Should -BeNullOrEmpty
        }
    }
}

Describe 'Test-AzureToken' -Tag 'Unit' {
    Context 'When access token is valid' {
        BeforeAll {
            Mock az {
                $global:LASTEXITCODE = 0
                return 'valid-token'
            }
        }

        It 'Returns true' {
            Test-AzureToken | Should -BeTrue
        }
    }

    Context 'When access token is expired' {
        BeforeAll {
            Mock az { $global:LASTEXITCODE = 1 }
        }

        It 'Returns false' {
            Test-AzureToken | Should -BeFalse
        }
    }
}

Describe 'Test-CorrectTenant' -Tag 'Unit' {
    It 'Returns true when tenant is empty' {
        Test-CorrectTenant '' | Should -BeTrue
    }

    It 'Returns true when tenant is null' {
        Test-CorrectTenant $null | Should -BeTrue
    }

    Context 'When graph API returns a tenant domain' {
        BeforeAll {
            Mock az {
                $global:LASTEXITCODE = 0
                return 'contoso.onmicrosoft.com'
            }
        }

        It 'Returns true for matching tenant' {
            Test-CorrectTenant 'contoso.onmicrosoft.com' | Should -BeTrue
        }

        It 'Returns false for non-matching tenant' {
            Test-CorrectTenant 'other.onmicrosoft.com' | Should -BeFalse
        }
    }

    Context 'When graph API call fails' {
        BeforeAll {
            Mock az { $global:LASTEXITCODE = 1 }
        }

        It 'Returns false' {
            Test-CorrectTenant 'contoso.onmicrosoft.com' | Should -BeFalse
        }
    }
}

Describe 'Invoke-AzureLogin' -Tag 'Unit' {
    Context 'When login succeeds without tenant' {
        BeforeAll {
            Mock az { $global:LASTEXITCODE = 0 }
            Mock Write-Host {}
            Mock Write-Error {}
        }

        It 'Does not write error' {
            Invoke-AzureLogin ''
            Should -Invoke Write-Error -Times 0
        }
    }

    Context 'When login succeeds with tenant' {
        BeforeAll {
            Mock az { $global:LASTEXITCODE = 0 }
            Mock Write-Host {}
            Mock Write-Error {}
        }

        It 'Does not write error' {
            Invoke-AzureLogin 'contoso.onmicrosoft.com'
            Should -Invoke Write-Error -Times 0
        }
    }

    Context 'When login fails without tenant' {
        BeforeAll {
            Mock az { $global:LASTEXITCODE = 1 }
            Mock Write-Host {}
            Mock Write-Error {}
        }

        It 'Writes error' {
            Invoke-AzureLogin ''
            Should -Invoke Write-Error -Times 1
        }
    }

    Context 'When login fails with tenant' {
        BeforeAll {
            Mock az { $global:LASTEXITCODE = 1 }
            Mock Write-Host {}
            Mock Write-Error {}
        }

        It 'Writes error including tenant name' {
            Invoke-AzureLogin 'contoso.onmicrosoft.com'
            Should -Invoke Write-Error -Times 1 -ParameterFilter {
                $Message -match 'contoso'
            }
        }
    }
}
