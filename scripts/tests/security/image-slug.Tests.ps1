#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for scripts/security/image-slug.sh, the disambiguation-slug
# helper used by container-scan.yml (SARIF names/categories). The slug must be
# deterministic and collision-free across image refs whose tags collapse to the
# same alphanumeric form.

BeforeDiscovery {
    $script:BashPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    $script:SlugScript = Join-Path $PSScriptRoot '../../security/image-slug.sh'

    function Get-Slug {
        param([Parameter(Mandatory)][string]$Ref)
        $out = & bash $script:SlugScript $Ref 2>$null
        $script:LastSlugExit = $LASTEXITCODE
        ($out | Select-Object -First 1)
    }

    # Independent reference implementation of the ref hash (SHA-256 of the full
    # ref, first 12 hex chars) so the golden assertion catches a regression that
    # hashed only the tag.
    function Get-RefHash {
        param([Parameter(Mandatory)][string]$Ref)
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Ref)
        $hash = [System.Security.Cryptography.SHA256]::HashData($bytes)
        (([System.BitConverter]::ToString($hash) -replace '-').ToLower()).Substring(0, 12)
    }
}

Describe 'image-slug.sh' -Tag 'Unit' -Skip:(-not $script:BashPresent) {
    Context 'slug shape' {
        It 'collapses registry/repo/tag punctuation to single hyphens and appends a 12-hex hash' {
            $ref = 'python:3.12@sha256:' + ('a' * 64)
            Get-Slug -Ref $ref | Should -Match '^python-3-12-[0-9a-f]{12}$'
        }

        It 'preserves registry host and port in the slug' {
            $ref = 'registry.example.com:5000/ns/app:1.0@sha256:' + ('c' * 64)
            Get-Slug -Ref $ref | Should -Match '^registry-example-com-5000-ns-app-1-0-[0-9a-f]{12}$'
        }

        It 'trims leading, trailing, and repeated hyphens from the tag portion' {
            $ref = '_weird_:1@sha256:' + ('e' * 64)
            $slug = Get-Slug -Ref $ref
            $slug | Should -Match '^weird-1-[0-9a-f]{12}$'
            $slug | Should -Not -Match '^-'
            $slug | Should -Not -Match '--'
        }
    }

    Context 'hash provenance' {
        It 'derives the suffix from a SHA-256 of the full ref, digest included' {
            $ref = 'python:3.12@sha256:' + ('a' * 64)
            Get-Slug -Ref $ref | Should -Be ('python-3-12-' + (Get-RefHash -Ref $ref))
        }
    }

    Context 'determinism and collision resistance' {
        It 'returns an identical slug for identical input' {
            $ref = 'node:22@sha256:' + ('b' * 64)
            (Get-Slug -Ref $ref) | Should -Be (Get-Slug -Ref $ref)
        }

        It 'disambiguates two refs whose tags collapse to the same alphanumeric slug' {
            $digest = '@sha256:' + ('a' * 64)
            $slugDot = Get-Slug -Ref ('foo.bar:1' + $digest)
            $slugDash = Get-Slug -Ref ('foo-bar:1' + $digest)
            $slugDot | Should -Match '^foo-bar-1-[0-9a-f]{12}$'
            $slugDash | Should -Match '^foo-bar-1-[0-9a-f]{12}$'
            $slugDot | Should -Not -Be $slugDash
        }

        It 'changes the suffix when only the digest differs' {
            $slugA = Get-Slug -Ref ('img:1@sha256:' + ('a' * 64))
            $slugB = Get-Slug -Ref ('img:1@sha256:' + ('b' * 64))
            $slugA | Should -Not -Be $slugB
        }
    }

    Context 'argument handling' {
        It 'exits non-zero when no ref is supplied' {
            & bash $script:SlugScript 2>$null | Out-Null
            $LASTEXITCODE | Should -Not -Be 0
        }
    }
}
