---
description: 'Configure container image signing, attestation, and admission policy for a fork of this reference architecture'
argument-hint: "[mode={sigstore|notation|none}] [enableMirror=false] [premiumAcr=false]"
---

# Configure Container Build & Signing

## Inputs

* ${input:mode:sigstore}: (Optional, defaults to `sigstore`) Signing mode. `sigstore` uses keyless cosign + public Rekor. `notation` uses Notation v2 with Azure Key Vault. `none` disables signing (development forks only).
* ${input:enableMirror:false}: (Optional, defaults to false) Deploy the in-cluster Sigstore mirror (`tuf-mirror` + `rekor-replica`) for offline edge verification.
* ${input:premiumAcr:false}: (Optional, defaults to false) Provision Premium-tier ACR with content trust. Required for Notation mode in production.
* ${input:githubOrg}: (Required) GitHub organization or user that owns the fork. Used to scope OIDC subject claims.
* ${input:githubRepo}: (Required) Fork repository name. Used to build the workflow-ref regex for keyless verification.

## Context

This prompt configures a fresh fork to produce signed container images verified at admission. Operators must complete it once after forking and re-run it after migrating signing modes. It touches Terraform variables, GitHub Actions workflow refs, Kyverno ClusterPolicies, and (in Notation mode) Azure Key Vault.

## Requirements

1. **Validate prerequisites.**
   * Confirm `terraform`, `cosign`, `notation`, `vexctl`, `gh`, and `az` are on PATH (`require_tools` from `scripts/lib/common.sh`).
   * Confirm the user is logged into Azure (`az account show`) and GitHub (`gh auth status`).
   * Source `infrastructure/terraform/prerequisites/az-sub-init.sh` before any Terraform invocation.

2. **Select signing mode and persist Terraform variables.**
   * Update `infrastructure/terraform/terraform.tfvars` with `signing_mode = "${input:mode}"`.
   * When `mode = sigstore`: set `should_use_public_rekor = true` and `should_deploy_sigstore_mirror = ${input:enableMirror}`.
   * When `mode = notation`: set `should_enable_premium_acr = ${input:premiumAcr}` and prompt for AKV name + key name; populate the `notation_akv` block.
   * When `mode = none`: warn the user that admission verification will be disabled and require explicit confirmation.

3. **Provision identity and registries.**
   * Run `terraform plan` in `infrastructure/terraform/` and review the diff with the user.
   * Apply on confirmation. Capture outputs `github_oidc_issuer`, `arc_runner_identity`, and (Notation only) `notation_akv_key_uri`.

4. **Update workflow-ref regex.**
   * Compute the keyless subject regex `^https://github\.com/${input:githubOrg}/${input:githubRepo}/\.github/workflows/release-signing\.yml@refs/(heads|tags)/.+$`.
   * Patch `policies/kyverno/verify-images.yaml` and `fleet-deployment/gitops/clusters/base/admission/values.yaml` with the computed regex.
   * In Notation mode, also patch `trustedIdentities` to reference the AKV key URI.

5. **Distribute the trusted root.**
   * Sigstore mode: export the public-good Sigstore TUF root via `cosign initialize --mirror=https://tuf-repo-cdn.sigstore.dev`. When `enableMirror = true`, also generate `clusters/overlays/airgapped/trusted-root.json` from the in-cluster mirror.
   * Notation mode: export the AKV public certificate to `policies/kyverno/notation-trust-store/${input:githubRepo}.crt` and reference it from the ClusterPolicy.

6. **Select and apply Kyverno mode.**
   * Default to `enforce`. Offer `audit` for staged rollouts.
   * Apply policies with `kubectl apply -k fleet-deployment/gitops/clusters/overlays/admission/`.
   * Run `kyverno test policies/kyverno/` and report failures before continuing.

7. **Optionally deploy the Sigstore mirror.**
   * When `enableMirror = true`, run `infrastructure/setup/03-sigstore-mirror.sh` and verify `rekor-replica` and `tuf-mirror` pods are `Ready`.

8. **Validate the configured pipeline.**
   * Trigger `release-signing.yml` on a throwaway tag with `gh workflow run`.
   * Run `scripts/security/verify-image.sh --image <built-digest>` and confirm verification succeeds.
   * Run `scripts/security/check-admission-readiness.sh` against a representative cluster.

9. **Document the configuration.**
   * Append the chosen mode, key URIs, and rollout date to `docs/security/container-signing.md` under "Fork Configuration History".
   * Commit changes on a feature branch using `feat(security): ...` per `commit-message.instructions.md`.

## Validation

* `npm run lint:tf:validate` and `npm run test:tf` clean.
* `kyverno test policies/kyverno/` clean.
* `scripts/security/verify-image.sh` exits 0 against a freshly signed image.
* `npm run lint:md` and `npm run spell-check` clean for any updated docs.

> [!WARNING]
> Switching `signing_mode` between `sigstore` and `notation` requires re-signing all in-flight images. Plan a maintenance window and coordinate edge cluster cutover with the fleet operator.
