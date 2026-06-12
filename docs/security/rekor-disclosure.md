# Rekor Public Transparency Log Disclosure

When the default signing mode (`signing_mode = "sigstore"`) is in effect with `should_use_public_rekor = true`, every published container image signature, attestation, and SBOM is recorded in the **public Sigstore Rekor transparency log** at `https://rekor.sigstore.dev`. This page documents what becomes public, why the project defaults to public Rekor, and how to opt out.

## What Becomes Public

Each `cosign sign`, `cosign attest`, and `cosign attest-blob` invocation publishes an immutable Rekor entry that contains:

| Field                      | Public Value                                                                           | Notes                                                            |
|----------------------------|----------------------------------------------------------------------------------------|------------------------------------------------------------------|
| OIDC issuer                | `https://token.actions.githubusercontent.com`                                          | Identifies GitHub Actions OIDC as the keyless trust root.        |
| Certificate identity       | `https://github.com/<owner>/<repo>/.github/workflows/container-publish.yml@refs/<ref>` | Subject Alternative Name; reveals workflow path, branch, or tag. |
| Image digest               | `sha256:...`                                                                           | Content-addressable digest of the signed artifact.               |
| Signature                  | base64 ECDSA over the digest                                                           | Verifiable offline against the Fulcio root.                      |
| Signed timestamp           | RFC3161                                                                                | Provided by Rekor at entry time.                                 |
| Attestation predicate type | SPDX, SLSA, CycloneDX, or OpenVEX URI                                                  | The predicate body itself is included for `cosign attest`.       |

Publication of the workflow ref means the **branch or tag name** of every signed build is visible to anyone querying the log. Predicate bodies (SBOM, provenance, VEX) are also visible in full.

## Why the Project Defaults to Public Rekor

Public Rekor delivers three properties that a private log cannot match without significant operator burden:

* **Independent verification**: Any consumer can re-derive the signing identity chain without trusting a project-operated server.
* **Transparency by default**: Supply-chain consumers and security researchers can audit every published artifact without coordination.
* **Zero operational cost**: No private Rekor, no TUF root rotation, no separate witness infrastructure.

The architectural decision is recorded in [Container Signing — Public Rekor as Default](../adrs/container-signing-public-rekor.md).

## Operator Consent Surface

Operators acknowledge public-log publication explicitly before any signed workload runs:

* `infrastructure/setup/01-deploy-robotics-charts.sh` prints a first-deploy banner when `should_use_public_rekor = true` and aborts unless `--accept-public-rekor` is passed (or the operator answers `y` interactively).
* `data-management/setup/deploy-dataviewer.sh` requires the same flag when verification falls back to the public Rekor instance.
* `scripts/security/verify-image.sh` aborts with `Public Rekor consent not granted; aborting.` when sigstore mode is active without `--offline` + `--trusted-root` and without `--accept-public-rekor`.

Consent is per-invocation. No state is persisted on disk; operators reaffirm on every run so a new operator inheriting the cluster cannot publish silently.

## Opt-Out Path

To prevent any project signing operation from writing to the public Rekor instance, use one of the following configurations:

### Option 1 — Notation Mode

Set `signing_mode = "notation"` in `infrastructure/terraform/terraform.tfvars`. Notation/AKV signs against an Azure Key Vault HSM key and never touches Rekor. This is the recommended opt-out for tenants with a contractual or regulatory bar on public log publication. See [Container Image Signing](container-signing.md#notation-mode) for the full Notation surface.

### Option 2 — Private Sigstore Mirror

Set `signing_mode = "sigstore"`, `should_use_public_rekor = false`, and `should_deploy_sigstore_mirror = true`. The `sigstore-mirror` Terraform module provisions a Storage-Account static-website TUF mirror, and signing operations target a self-hosted Fulcio + Rekor pair.

Operators must run their own Fulcio CA and Rekor instance — this repository ships only the TUF mirror, not the signing-server stack. Out of scope for the reference architecture; see [WI-08](../../.copilot-tracking/plans/logs/2026-04-25/container-signing-log.md) in the planning log.

### Option 3 — Disable Signing

Set `signing_mode = "none"` to disable signing entirely. Kyverno admission policies will fall back to `Audit` mode and unsigned images will deploy. This is appropriate only for isolated development clusters.

## Audit and Removal

* Rekor entries are **append-only**. Once published, a signature cannot be removed.
* Workflow refs containing sensitive branch names should not be signed; rename the branch before triggering the publish workflow.
* If a signing key or workload identity is suspected to be compromised, follow the rotation runbook in [docs/runbooks/notation-key-rotation.md](../runbooks/notation-key-rotation.md) (Notation) or rotate the GitHub OIDC trust binding via `infrastructure/terraform/modules/github-oidc/` (Sigstore).

## References

* [Container Image Signing](container-signing.md) — full architecture and operator surface.
* [ADR: Container Signing — Public Rekor as Default](../adrs/container-signing-public-rekor.md) — decision record.
* [Notation Key Rotation Runbook](../runbooks/notation-key-rotation.md) — quarterly rotation cadence.
* [Sigstore Rekor Documentation](https://docs.sigstore.dev/logging/overview/) — upstream transparency-log specification.
