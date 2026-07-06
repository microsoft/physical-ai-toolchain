#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for scripts/security/discover-base-images.sh, the FROM-line
# parser used by container-scan.yml. It must emit only external, digest-pinned
# bases and exclude stage aliases, ARG/scratch bases, and duplicates. Tests run
# the script inside a throwaway git work tree (it enumerates tracked Dockerfiles
# via `git ls-files`).

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command git -ErrorAction SilentlyContinue)
}

BeforeAll {
    $script:DiscoverScript = (Resolve-Path (Join-Path $PSScriptRoot '../../security/discover-base-images.sh')).Path

    $script:DigestA = 'a' * 64
    $script:DigestB = 'b' * 64
    $script:DigestC = 'c' * 64
    $script:DigestD = 'd' * 64

    # Create a git work tree seeded with Dockerfiles and return the extracted refs.
    function Invoke-Discover {
        param([Parameter(Mandatory)][hashtable]$Files)

        $repo = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $repo -Force | Out-Null
        try {
            foreach ($relative in $Files.Keys) {
                $target = Join-Path $repo $relative
                New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
                Set-Content -Path $target -Value $Files[$relative] -Encoding utf8
            }
            & git -C $repo init -q
            & git -C $repo add -A
            $out = & bash -c "cd '$repo' && bash '$script:DiscoverScript'" 2>$null
            $script:LastDiscoverExit = $LASTEXITCODE
            @($out | Where-Object { $_ -ne '' })
        }
        finally {
            Remove-Item -Recurse -Force $repo -ErrorAction SilentlyContinue
        }
    }
}

Describe 'discover-base-images.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    Context 'FROM parsing across a representative Dockerfile' {
        BeforeAll {
            $dockerfile = @"
# syntax=docker/dockerfile:1
ARG BASE_IMAGE=python:3.12-slim
FROM python:3.12-slim@sha256:$script:DigestA AS base
FROM node:22@sha256:$script:DigestB AS builder
FROM builder
FROM `${BASE_IMAGE}
FROM scratch
FROM registry.example.com:5000/ns/app:1.0@sha256:$script:DigestC
from busybox@sha256:$script:DigestD
"@
            $script:Refs = Invoke-Discover -Files @{ 'Dockerfile' = $dockerfile }
        }

        It 'exits successfully' {
            $script:LastDiscoverExit | Should -Be 0
        }

        It 'extracts every digest-pinned external base' {
            $script:Refs | Should -Contain "python:3.12-slim@sha256:$script:DigestA"
            $script:Refs | Should -Contain "node:22@sha256:$script:DigestB"
            $script:Refs | Should -Contain "registry.example.com:5000/ns/app:1.0@sha256:$script:DigestC"
        }

        It 'matches FROM case-insensitively' {
            $script:Refs | Should -Contain "busybox@sha256:$script:DigestD"
        }

        It 'excludes stage aliases, ARG-interpolated bases, and scratch' {
            $script:Refs | Should -Not -Contain 'builder'
            $script:Refs | Should -Not -Contain 'scratch'
            ($script:Refs -join "`n") | Should -Not -Match '\$\{'
        }

        It 'returns exactly the four digest-pinned refs' {
            $script:Refs.Count | Should -Be 4
        }

        It 'emits a sorted, unique list' {
            $script:Refs | Should -Be (@($script:Refs) | Sort-Object -Unique)
        }
    }

    Context 'deduplication across multiple Dockerfiles' {
        It 'collapses the same digest-pinned base referenced in two files to one entry' {
            $line = "FROM python:3.12-slim@sha256:$script:DigestA`n"
            $refs = Invoke-Discover -Files @{
                'Dockerfile'         = $line
                'service.Dockerfile' = $line
            }
            @($refs | Where-Object { $_ -eq "python:3.12-slim@sha256:$script:DigestA" }).Count | Should -Be 1
        }
    }

    Context 'repository with no Dockerfiles' {
        It 'produces no output and exits successfully' {
            $refs = Invoke-Discover -Files @{ 'README.md' = '# no dockerfiles here' }
            $script:LastDiscoverExit | Should -Be 0
            $refs.Count | Should -Be 0
        }
    }
}
