# ADR: Container Signing — Public Rekor as Default

**Status**: Accepted
**Date**: 2026-04-25
**Deciders**: Reference architecture maintainers
**Related**: [Container Image Signing](../security/container-signing.md), [Rekor Public Disclosure](../security/rekor-disclosure.md)

## Context

This reference architecture publishes container images for the dataviewer, LeRobot evaluation, and downstream workload images consumed by Kyverno-gated AKS clusters. Each image needs a verifiable signature plus four attestations (SPDX SBOM, SLSA provenance, CycloneDX SBOM, OpenVEX vulnerability statement). Two signing implementations are supported:

* **Sigstore cosign keyless** — backed by GitHub Actions OIDC and the public Sigstore Fulcio CA + Rekor transparency log.
* **Notation v1 + Azure Key Vault HSM** — backed by an AKV HSM key and a Notation trust policy bundle distributed to clusters.

Cosign keyless requires a transparency log to prove that a short-lived Fulcio certificate existed at signing time. Two log topologies are available:

1. The **public Rekor instance** at `https://rekor.sigstore.dev`, operated by the Open Source Security Foundation.
2. A **private Rekor instance** operated alongside a private Fulcio CA and TUF mirror.

A decision is required for which Rekor the project's `signing_mode = "sigstore"` default targets.

## Decision

**The default Sigstore signing mode publishes to the public Rekor instance.** Operators opt out by selecting `signing_mode = "notation"`, by setting `should_use_public_rekor = false` with a self-hosted Sigstore stack, or by setting `signing_mode = "none"` to disable signing entirely.

Operator consent is required at every invocation:

* Deploy scripts (`infrastructure/setup/01-deploy-*.sh`, `data-management/setup/deploy-dataviewer.sh`) require `--accept-public-rekor` (or interactive `y` confirmation) before any signed workload reaches the cluster.
* `scripts/security/verify-image.sh` aborts with an explicit consent error when sigstore mode runs without offline trust roots and without the consent flag.
* Consent is per-invocation; no state persists.

## Rationale

| Criterion                 | Public Rekor                                                                 | Private Rekor                                               | Decision Driver                                                 |
|---------------------------|------------------------------------------------------------------------------|-------------------------------------------------------------|-----------------------------------------------------------------|
| Independent verifiability | Any consumer can verify against the public log without project cooperation.  | Consumers must trust a project-operated witness.            | Public wins for an open reference architecture.                 |
| Operational burden        | Zero — log is operated by upstream Sigstore.                                 | High — TUF root rotation, witness availability, monitoring. | Public wins for a community-maintained sample.                  |
| Transparency posture      | Every signed artifact and predicate body is visible to security researchers. | Visibility limited to operators with log access.            | Aligns with project's open-source supply-chain security goals.  |
| Tenant disclosure surface | Publishes workflow ref (branch or tag), image digest, predicate bodies.      | None.                                                       | Mitigated by explicit operator consent + Notation opt-out path. |
| Compliance alignment      | Maps to SLSA L3 transparency requirements out of the box.                    | Requires custom attestation of log integrity.               | Public satisfies SLSA review without bespoke evidence.          |

## Consequences

### Positive

* Consumers of published images can re-verify signatures end-to-end with stock `cosign verify` and no project-specific configuration.
* The reference architecture remains operable by tenants with no Sigstore infrastructure of their own.
* Audit and SLSA L3 evidence collection requires no additional witness or log-integrity proof.

### Negative

* Workflow refs (branch and tag names) of every signed build become public. Sensitive branch names must be renamed before triggering the publish workflow.
* Rekor entries are append-only; a published signature cannot be retracted.
* Tenants with regulatory bars on public-log publication must opt into Notation mode or self-host the full Sigstore stack.

### Mitigations

* Per-invocation consent surface in deploy scripts and `verify-image.sh` — see [Rekor Public Disclosure](../security/rekor-disclosure.md).
* Notation mode is a first-class, fully tested alternative — not a degraded path.
* `should_use_public_rekor = false` is supported by the Terraform schema for tenants who deploy a private Sigstore stack outside this repository.

## Alternatives Considered

### A. Default to Notation

Rejected. Notation requires Premium ACR + AKV HSM provisioning before the first build and adds a quarterly key-rotation operational burden (see [Notation Key Rotation Runbook](../runbooks/notation-key-rotation.md)). Forking tenants exploring the architecture should not need an HSM to publish the first signed image.

### B. Default to Sigstore with Required Self-Hosted Mirror

Rejected. Self-hosting Fulcio + Rekor + TUF mirror takes the architecture out of "reference" scope. Tenants would face an undocumented multi-week setup before a first signature could be produced.

### C. Default to Disabled (`signing_mode = "none"`)

Rejected. The project's stated goal is signed-by-default supply chain. Shipping unsigned images by default contradicts the Kyverno admission posture and the README claim of cosign-verified releases.

## References

* [Sigstore Rekor Specification](https://docs.sigstore.dev/logging/overview/)
* [SLSA L3 Provenance Requirements](https://slsa.dev/spec/v1.0/levels)
* [Container Image Signing](../security/container-signing.md)
* [Rekor Public Disclosure](../security/rekor-disclosure.md)
* [Notation Key Rotation Runbook](../runbooks/notation-key-rotation.md)
