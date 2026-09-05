---
description: 'OpenVEX authoring standards for base-image vulnerability triage'
applyTo: 'security/vex/**,scripts/security/generate-vex.sh,fleet-deployment/setup/attest-image.sh,fleet-deployment/setup/defaults.conf,fleet-deployment/setup/README.md'
---

# VEX Standards

Follow these rules when authoring or reviewing OpenVEX documents under `security/vex/` or changing their generator. These documents control whether container base-image vulnerability findings are suppressed, retained, or marked as remediated. Dependency suppressions remain governed by `osv-scanner.toml`.

This guidance is adapted from the [hve-core VEX standards](https://github.com/microsoft/hve-core/blob/e6f414dabf65d67d59763ce776fa2212bd70b028/.github/instructions/security/vex-standards.instructions.md).

## Human Accountability

OpenVEX changes under `security/vex/` require review from the security owner declared in `.github/CODEOWNERS`. An AI agent may draft a disposition and its supporting evidence, but the human reviewer who approves and merges the change is the accountable author of record.

The Sigstore identity attached during publication is the trust anchor for the published document. Keep the document `author` and `tooling` fields accurate so consumers can identify both the issuing organization and the process used to produce the statements.

## Source Licensing

Write original impact, remediation, and status-note prose. Reference advisory identifiers and URLs rather than copying advisory text.

| Source | Licensing guidance |
|--------|--------------------|
| NVD | US government public-domain data may be used for CVSS vectors and CWE classifications. |
| GitHub Advisory Database | Records are CC-BY-4.0. Link to advisory URLs and identifiers; do not copy prose without satisfying attribution requirements. |
| OSV.dev | Licensing follows the upstream record. Check record provenance before paraphrasing; when unclear, write original prose and cite the record URL. |

## Status Determination

Use only the four OpenVEX statuses:

| Status                | Use                                                                                                                                                     |
|-----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| `under_investigation` | Reachability or the ability to exploit the vulnerability is not yet known. This is the safe default for generated findings that have not been reviewed. |
| `not_affected`        | Analysis demonstrates that the vulnerability cannot affect the identified product.                                                                      |
| `affected`            | Analysis demonstrates that the identified product is vulnerable. Include an `action_statement` describing remediation or mitigation.                    |
| `fixed`               | The identified product version or digest contains the verified remediation.                                                                             |

Never infer a terminal status from a scanner result alone. A scanner finding establishes that investigation is required; an absent finding does not establish `not_affected` or `fixed`.

Do not change `under_investigation` to another status without product-specific analysis. Record the evidence and reasoning in `status_notes`. Vendor disputes, severity reassessments, and unavailable exploit reports do not establish that exploitation is impossible; retain `under_investigation` until the required evidence exists.

Do not mark a product `fixed` because an upstream fix exists. Verify that the exact product version or digest named by the statement contains the fix.

## Not-Affected Justifications

Every `not_affected` statement must include exactly one of these machine-readable `justification` values:

| Justification                                       | Required finding                                                                                             |
|-----------------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| `component_not_present`                             | The vulnerable component is absent from the identified product.                                              |
| `vulnerable_code_not_present`                       | The component is present, but the vulnerable code is excluded or patched out.                                |
| `vulnerable_code_not_in_execute_path`               | The vulnerable code is present but cannot execute in the product.                                            |
| `vulnerable_code_cannot_be_controlled_by_adversary` | The vulnerable code executes, but an adversary cannot control the inputs or state required for exploitation. |
| `inline_mitigations_already_exist`                  | Built-in controls that cannot be bypassed prevent all known exploit paths.                                   |

Choose the narrowest justification supported by evidence. Document the product-specific evidence in `status_notes`; do not repeat advisory prose or use a generic claim such as "not reachable."

## Product Identity

Identify the exact scanned artifact in every statement. Use a digest-pinned OCI package URL when the artifact has a registry digest. Do not apply analysis from one image tag, version, architecture, or digest to another without verifying that the relevant component and execution path are identical.

Keep one statement per vulnerability and product pair. Update that statement when its status changes rather than adding a contradictory statement.

## Document Mutation Contract

For every change to document content, including any statement:

- Increment the integer `version` by one.
- Set `timestamp` to the current UTC issuance time and update `last_updated` when present.
- Replace `@id` with a unique IRI for the new revision. Include the version or another collision-resistant revision value; a date alone is insufficient when multiple revisions can be issued in one day.
- Set `tooling` to describe how the document was generated, reviewed, and published.
- Preserve the original effective timestamp of every unchanged statement. Before changing the document timestamp, add an explicit statement `timestamp` where an unchanged statement previously inherited the old document timestamp.

`scripts/security/generate-vex.sh` preserves existing statements and their effective timestamps, then appends newly scanned vulnerabilities for the current digest as `under_investigation`. Do not replace that merge behavior with a wholesale rewrite. Findings absent from a later scan remain until product-specific analysis establishes their status.

The official OpenVEX v0.2.0 schema requires at least one statement. When a scan finds no vulnerabilities and no prior document exists, generation succeeds without creating a document. Do not create an empty foundation document.

Reject a change when it fails the official OpenVEX v0.2.0 schema, when `version`, `timestamp`, `@id`, or `tooling` is stale, when an unchanged statement loses its original effective timestamp, or when a status lacks the required analysis.
