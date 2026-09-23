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
        [bool](Get-Command git -ErrorAction SilentlyContinue) -and
        [bool](Get-Command jq -ErrorAction SilentlyContinue)
}

BeforeAll {
    $script:DiscoverScript = (Resolve-Path (Join-Path $PSScriptRoot '../../security/discover-base-images.sh')).Path

    $script:DigestA = 'a' * 64
    $script:DigestB = 'b' * 64
    $script:DigestC = 'c' * 64
    $script:DigestD = 'd' * 64

    $script:SlugScript = (Resolve-Path (Join-Path $PSScriptRoot '../../security/image-slug.sh')).Path
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:ScanWorkflow = Get-Content (Join-Path $script:RepoRoot '.github/workflows/container-scan.yml') -Raw
    $script:PrWorkflow = Get-Content (Join-Path $script:RepoRoot '.github/workflows/pr-validation.yml') -Raw

    function Get-Lane {
        param([string]$Id, [string]$Path, [int]$From = 0)
        @{ id = $Id; sources = @(@{ path = $Path; from = $From }) }
    }

    # Create a tracked repository fixture and return the requested discovery view.
    function Invoke-Discover {
        param(
            [Parameter(Mandatory)][hashtable]$Files,
            [object]$Lanes,
            [switch]$Matrix,
            [switch]$OmitMap
        )

        $repo = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $repo -Force | Out-Null
        try {
            if ($Matrix -and -not $OmitMap -and
                -not $Files.ContainsKey('scripts/security/container-scan-lanes.json')) {
                $Files = $Files.Clone()
                $laneArray = @()
                if ($null -ne $Lanes) { $laneArray = @($Lanes) }
                $Files['scripts/security/container-scan-lanes.json'] = @{ lanes = $laneArray } |
                    ConvertTo-Json -Depth 10 -Compress
            }
            foreach ($relative in $Files.Keys) {
                $target = Join-Path $repo $relative
                New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
                Set-Content -Path $target -Value $Files[$relative] -Encoding utf8
            }
            & git -C $repo init -q
            & git -C $repo add -A
            $mode = if ($Matrix) { '--matrix' } else { '' }
            $out = & bash -c "cd '$repo' && bash '$script:DiscoverScript' $mode" 2>$null
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

        It 'accepts case-insensitive digest tokens and hexadecimal characters' {
            $refs = Invoke-Discover -Files @{
                Dockerfile = "FROM registry.example.com/APP:1@SHA256:$($script:DigestA.ToUpper())"
            }
            $script:LastDiscoverExit | Should -Be 0
            $refs | Should -Contain "registry.example.com/APP:1@SHA256:$($script:DigestA.ToUpper())"
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

    Context 'checked-in source lanes' {
        It 'resolves all eight stable categories and exact concrete refs' {
            $matrix = & bash $script:DiscoverScript --matrix | ConvertFrom-Json
            $LASTEXITCODE | Should -Be 0
            @($matrix).Count | Should -Be 8
            @($matrix.lane | Sort-Object -Unique).Count | Should -Be 8
            @($matrix.category | Sort-Object -Unique).Count | Should -Be 8
            foreach ($row in $matrix) {
                @($row.PSObject.Properties.Name | Sort-Object) | Should -Be @('category', 'image', 'lane')
                $row.category | Should -Be "trivy-image-$($row.lane)"
            }
            $refs = & bash $script:DiscoverScript
            @($matrix.image | Sort-Object -Unique) | Should -Be @($refs)
        }
    }

    Context 'source-bound matrix behavior' {
        It 'retains a category across arbitrary tag and digest changes while artifact slugs change' {
            $lane = Get-Lane 'runtime' 'Dockerfile'
            $before = "FROM python:3.12@sha256:$script:DigestA"
            $after = "FROM python:latest@sha256:$script:DigestB"
            $a = Invoke-Discover -Matrix -Files @{ Dockerfile = $before } -Lanes @($lane) | ConvertFrom-Json
            $b = Invoke-Discover -Matrix -Files @{ Dockerfile = $after } -Lanes @($lane) | ConvertFrom-Json
            $a.category | Should -Be $b.category
            $a.image | Should -Not -Be $b.image
            (& bash $script:SlugScript $a.image) | Should -Not -Be (& bash $script:SlugScript $b.image)
        }

        It 'retains lane identity across <Name>' -ForEach @(
            @{ Name = 'digest update'; OldTag = '1'; NewTag = '1'; OldDigest = 'a'; NewDigest = 'b' }
            @{ Name = 'patch update'; OldTag = '1.0.1'; NewTag = '1.0.2'; OldDigest = 'a'; NewDigest = 'b' }
            @{ Name = 'tag update'; OldTag = 'stable'; NewTag = 'next'; OldDigest = 'a'; NewDigest = 'a' }
        ) {
            $oldRef = "registry.example.com:5000/team/app:${OldTag}@sha256:" + ($OldDigest * 64)
            $newRef = "registry.example.com:5000/team/app:${NewTag}@sha256:" + ($NewDigest * 64)
            $oldRow = Invoke-Discover -Matrix -Files @{ Dockerfile = "FROM $oldRef" } `
                -Lanes @((Get-Lane 'stable-role' 'Dockerfile')) | ConvertFrom-Json
            $newRow = Invoke-Discover -Matrix -Files @{ Dockerfile = "FROM $newRef" } `
                -Lanes @((Get-Lane 'stable-role' 'Dockerfile')) | ConvertFrom-Json
            $oldRow.category | Should -Be 'trivy-image-stable-role'
            $newRow.category | Should -Be $oldRow.category
            $oldRow.image | Should -Be $oldRef
            $newRow.image | Should -Be $newRef
            (& bash $script:SlugScript $oldRef) | Should -Not -Be (& bash $script:SlugScript $newRef)
        }

        It 'retains independent categories for concurrent aliases and versions' {
            $lanes = @(
                (Get-Lane 'short' 'a/Dockerfile')
                (Get-Lane 'qualified' 'b/Containerfile')
                (Get-Lane 'older' 'c/Dockerfile')
            )
            $matrix = Invoke-Discover -Matrix -Files @{
                'a/Dockerfile' = "FROM python:3.12@sha256:$script:DigestA"
                'b/Containerfile' = "FROM docker.io/library/python:3.12@sha256:$script:DigestA"
                'c/Dockerfile' = "FROM python:3.11@sha256:$script:DigestB"
            } -Lanes $lanes | ConvertFrom-Json
            @($matrix.category | Sort-Object -Unique).Count | Should -Be 3
            @($matrix.image | Sort-Object -Unique).Count | Should -Be 3
        }

        It 'resolves shared sources once and sorts output by lane, not map order' {
            $lanes = @(
                (Get-Lane 'zeta' 'z/Dockerfile')
                @{ id = 'alpha'; sources = @(
                    @{ path = 'a/Dockerfile'; from = 0 },
                    @{ path = 'b/Containerfile'; from = 0 }
                ) }
            )
            $matrix = Invoke-Discover -Matrix -Files @{
                'a/Dockerfile' = "FROM python:3.12@sha256:$script:DigestA"
                'b/Containerfile' = "from python:3.12@sha256:$script:DigestA"
                'z/Dockerfile' = "FROM node:22@sha256:$script:DigestB"
            } -Lanes $lanes | ConvertFrom-Json
            @($matrix.lane) | Should -Be @('alpha', 'zeta')
        }

        It 'returns an empty JSON array for an empty map and no eligible sources' {
            $result = Invoke-Discover -Matrix -Files @{ 'README.md' = 'none' } -Lanes @()
            $script:LastDiscoverExit | Should -Be 0
            $result | Should -Be '[]'
        }

        It 'rejects a missing map without emitting a partial matrix' {
            $result = Invoke-Discover -Matrix -OmitMap -Files @{
                Dockerfile = "FROM python:3.12@sha256:$script:DigestA"
            }
            $script:LastDiscoverExit | Should -Not -Be 0
            @($result).Count | Should -Be 0
        }

        It 'rejects invalid source bindings and map shapes' -ForEach @(
            @{ Name = 'duplicate ID'; Lanes = @(
                @{ id = 'same'; sources = @(@{ path = 'Dockerfile'; from = 0 }) },
                @{ id = 'same'; sources = @(@{ path = 'Dockerfile'; from = 0 }) }
            ) }
            @{ Name = 'invalid ID'; Lanes = @(@{ id = 'Bad_ID'; sources = @(@{ path = 'Dockerfile'; from = 0 }) }) }
            @{ Name = 'empty lane'; Lanes = @(@{ id = 'empty'; sources = @() }) }
            @{ Name = 'traversal'; Lanes = @(@{ id = 'traversal'; sources = @(@{ path = '../Dockerfile'; from = 0 }) }) }
            @{ Name = 'stale slot'; Lanes = @(@{ id = 'stale'; sources = @(@{ path = 'Dockerfile'; from = 1 }) }) }
            @{ Name = 'unknown field'; Lanes = @(@{ id = 'unknown'; sources = @(@{ path = 'Dockerfile'; from = 0 }); extra = 1 }) }
        ) {
            $result = Invoke-Discover -Matrix -Files @{
                Dockerfile = "FROM python:3.12@sha256:$script:DigestA"
            } -Lanes $Lanes
            $script:LastDiscoverExit | Should -Not -Be 0
            @($result).Count | Should -Be 0
        }

        It 'rejects unbound eligible sources and divergent shared-source refs' {
            $files = @{
                'a/Dockerfile' = "FROM python:3.12@sha256:$script:DigestA"
                'b/Dockerfile' = "FROM python:3.13@sha256:$script:DigestB"
            }
            $result = Invoke-Discover -Matrix -Files $files -Lanes @((Get-Lane 'only' 'a/Dockerfile'))
            $script:LastDiscoverExit | Should -Not -Be 0
            @($result).Count | Should -Be 0
            $result = Invoke-Discover -Matrix -Files $files -Lanes @(
                @{ id = 'both'; sources = @(
                    @{ path = 'a/Dockerfile'; from = 0 },
                    @{ path = 'b/Dockerfile'; from = 0 }
                ) }
            )
            $script:LastDiscoverExit | Should -Not -Be 0
            @($result).Count | Should -Be 0
        }

        It 'rejects duplicate concrete refs across separate lanes' {
            $result = Invoke-Discover -Matrix -Files @{
                'a/Dockerfile' = "FROM python:3.12@sha256:$script:DigestA"
                'b/Dockerfile' = "FROM python:3.12@sha256:$script:DigestA"
            } -Lanes @((Get-Lane 'first' 'a/Dockerfile'), (Get-Lane 'second' 'b/Dockerfile'))
            $script:LastDiscoverExit | Should -Not -Be 0
            @($result).Count | Should -Be 0
        }

        It 'rejects duplicate, invalid, and ineligible source slots' -ForEach @(
            @{ Name = 'duplicate source'; Files = @{
                Dockerfile = 'FROM python:1@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            }; Lanes = @(@{ id = 'same'; sources = @(
                @{ path = 'Dockerfile'; from = 0 }, @{ path = 'Dockerfile'; from = 0 }
            ) }) }
            @{ Name = 'untracked path'; Files = @{ Dockerfile = 'FROM scratch' };
                Lanes = @(@{ id = 'gone'; sources = @(@{ path = 'missing/Dockerfile'; from = 0 }) }) }
            @{ Name = 'tracked non-source path'; Files = @{ 'README.md' = 'FROM scratch' };
                Lanes = @(@{ id = 'readme'; sources = @(@{ path = 'README.md'; from = 0 }) }) }
            @{ Name = 'ineligible templated stage'; Files = @{ Dockerfile = 'FROM ${BASE}' };
                Lanes = @(@{ id = 'template'; sources = @(@{ path = 'Dockerfile'; from = 0 }) }) }
            @{ Name = 'non-integer ordinal'; Files = @{ Dockerfile = 'FROM scratch' };
                Lanes = @(@{ id = 'ordinal'; sources = @(@{ path = 'Dockerfile'; from = 0.5 }) }) }
            @{ Name = 'unknown source field'; Files = @{ Dockerfile = 'FROM scratch' };
                Lanes = @(@{ id = 'extra'; sources = @(@{ path = 'Dockerfile'; from = 0; note = 'no' }) }) }
        ) {
            $result = Invoke-Discover -Matrix -Files $Files -Lanes $Lanes
            $script:LastDiscoverExit | Should -Not -Be 0
            @($result).Count | Should -Be 0
        }

        It 'rejects malformed JSON map without partial output' {
            $result = Invoke-Discover -Matrix -Files @{
                Dockerfile = "FROM python:3.12@sha256:$script:DigestA"
                'scripts/security/container-scan-lanes.json' = '{"lanes": ['
            }
            $script:LastDiscoverExit | Should -Not -Be 0
            @($result).Count | Should -Be 0
        }

        It 'preserves registry ports and paths containing spaces' {
            $ref = "registry.example.com:5000/ns/app:1.0@sha256:$script:DigestC"
            $result = Invoke-Discover -Matrix -Files @{ 'images with spaces/Dockerfile' = "FROM $ref" } `
                -Lanes @((Get-Lane 'port' 'images with spaces/Dockerfile')) | ConvertFrom-Json
            $script:LastDiscoverExit | Should -Be 0
            $result.image | Should -Be $ref
        }

        It 'binds tracked suffix-style Dockerfile and Containerfile sources' {
            $dockerRef = "python:3.12@sha256:$script:DigestA"
            $containerRef = "node:22@sha256:$script:DigestB"
            $matrix = Invoke-Discover -Matrix -Files @{
                'images/base.Dockerfile' = "FROM $dockerRef"
                'images/base.Containerfile' = "FROM $containerRef"
            } -Lanes @(
                (Get-Lane 'docker-role' 'images/base.Dockerfile')
                (Get-Lane 'container-role' 'images/base.Containerfile')
            ) | ConvertFrom-Json
            $script:LastDiscoverExit | Should -Be 0
            @($matrix.lane) | Should -Be @('container-role', 'docker-role')
            $matrix[0].image | Should -Be $containerRef
            $matrix[0].category | Should -Be 'trivy-image-container-role'
            $matrix[1].image | Should -Be $dockerRef
            $matrix[1].category | Should -Be 'trivy-image-docker-role'
        }

        It 'keeps distinct categories for host variants with colliding slug prefixes' {
            $refDot = "foo.bar:1@sha256:$script:DigestA"
            $refDash = "foo-bar:1@sha256:$script:DigestA"
            $matrix = Invoke-Discover -Matrix -Files @{
                'first/Dockerfile' = "FROM $refDot"
                'second/Containerfile' = "FROM $refDash"
            } -Lanes @((Get-Lane 'first' 'first/Dockerfile'), (Get-Lane 'second' 'second/Containerfile')) |
                ConvertFrom-Json
            @($matrix.category | Sort-Object -Unique).Count | Should -Be 2
            (& bash $script:SlugScript $refDot) | Should -Not -Be (& bash $script:SlugScript $refDash)
        }

        It 'keeps surviving categories stable when another lane changes' {
            $lanes = @((Get-Lane 'first' 'a/Dockerfile'), (Get-Lane 'second' 'b/Containerfile'))
            $files = @{
                'a/Dockerfile' = "FROM foo:1@sha256:$script:DigestA"
                'b/Containerfile' = "FROM bar:1@sha256:$script:DigestB"
            }
            $before = Invoke-Discover -Matrix -Files $files -Lanes $lanes | ConvertFrom-Json
            $files['b/Containerfile'] = "FROM bar:2@sha256:$script:DigestC"
            $after = Invoke-Discover -Matrix -Files $files -Lanes @($lanes[1], $lanes[0]) | ConvertFrom-Json
            @($before.category) | Should -Be @($after.category)
            $before[0].image | Should -Be $after[0].image
            $before[1].image | Should -Not -Be $after[1].image
        }
    }

    Context 'workflow bindings' {
        It 'uses the lane matrix for uploads and the concrete slug only for filenames' {
            $script:ScanWorkflow | Should -Match 'discover-base-images\.sh --matrix'
            $script:ScanWorkflow | Should -Match 'include: \$\{\{ fromJSON\(needs\.discover\.outputs\.images\) \}\}'
            $script:ScanWorkflow | Should -Match 'count=\$\(jq .length.'
            $script:ScanWorkflow | Should -Match 'category: \$\{\{ matrix\.category \}\}'
            $script:ScanWorkflow | Should -Match 'image-ref: \$\{\{ matrix\.image \}\}'
            @([regex]::Matches($script:ScanWorkflow, 'trivy-\$\{\{ steps\.slug\.outputs\.slug \}\}\.sarif')).Count |
                Should -Be 2
        }

        It 'executes the actual PR path filter for scan and Pester triggers' {
            $matchLine = [regex]::Match($script:PrWorkflow, '(?m)^\s*match\(\) \{[^\r\n]+').Value.Trim()
            $scanLine = [regex]::Match($script:PrWorkflow, '(?m)^\s*scan_match=[^\r\n]+').Value.Trim()
            $pesterLine = [regex]::Match($script:PrWorkflow, '(?m)^\s*pester_match=[^\r\n]+').Value.Trim()
            $matchLine | Should -Not -BeNullOrEmpty
            $scanLine | Should -Not -BeNullOrEmpty
            $pesterLine | Should -Not -BeNullOrEmpty
            $filter = @($matchLine, $scanLine, $pesterLine,
                'printf "%s,%s" "$(match "$scan_match")" "$pester_match"') -join "`n"
            foreach ($path in @(
                'gpu-offload/controller/Containerfile',
                '.devcontainer/Dockerfile',
                'scripts/security/container-scan-lanes.json',
                'scripts/security/discover-base-images.sh',
                'scripts/security/image-slug.sh',
                '.github/workflows/container-scan.yml',
                '.github/workflows/pr-validation.yml'
            )) {
                $env:FILES = $path
                try { (& bash -c $filter) | Should -Be 'true,true' }
                finally { Remove-Item Env:FILES }
            }
            $env:FILES = 'docs/README.md'
            try { (& bash -c $filter) | Should -Be 'false,false' }
            finally { Remove-Item Env:FILES }
            $env:FILES = 'scripts/tests/security/image-slug.Tests.ps1'
            try { (& bash -c $filter) | Should -Be 'false,true' }
            finally { Remove-Item Env:FILES }
        }
    }
}
