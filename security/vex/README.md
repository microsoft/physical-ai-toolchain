# OpenVEX Statements

This directory contains [OpenVEX](https://openvex.dev/) (Vulnerability Exploitability eXchange) statements that the build pipeline attaches as Sigstore/Notation attestations to dataviewer container images. VEX lets us declare, for each known CVE surfaced by `trivy image`, whether our images are actually affected — suppressing false positives without hiding genuine risk.

## 📋 Authoring Workflow

1. **Identify** a CVE flagged by `scripts/security/scan-image-vulns.sh` against a dataviewer image that is **not** exploitable in our usage.
2. **Author or update** an OpenVEX document under this directory. One file per logical product; group statements by base image when practical.
3. **Validate** the JSON schema:

   ```bash
   vexctl validate security/vex/*.openvex.json
   ```

   When `vexctl` is unavailable, use any JSON-schema validator against the [OpenVEX v0.2.0 schema](https://github.com/openvex/spec/blob/main/openvex_json_schema.json).
4. **Commit** the document on a branch and open a PR. Reviewers verify the justification matches the code path under analysis.
5. **Sign** the published VEX document. The `release-signing.yml` workflow attaches each `*.openvex.json` to the corresponding container image as an OpenVEX attestation predicate using `cosign attest --predicate` (Sigstore mode) or `notation sign --plugin azure-kv` with a `vnd.openvex` artifact reference (Notation mode). Unsigned VEX documents are not consumed by the verification pipeline.

## 🧾 Required Fields

Every OpenVEX statement must populate:

| Field                             | Purpose                                                                                                                      |
|-----------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `@context`                        | Always `https://openvex.dev/ns/v0.2.0`.                                                                                      |
| `@id`                             | Stable URI for the document; bump on every revision.                                                                         |
| `author`                          | Microsoft physical-AI-toolchain maintainers (or fork operator).                                                              |
| `timestamp`                       | RFC 3339 UTC timestamp.                                                                                                      |
| `version`                         | Monotonically increasing integer.                                                                                            |
| `statements[].vulnerability.name` | Canonical CVE ID (e.g., `CVE-2024-12345`).                                                                                   |
| `statements[].products[].@id`     | Image reference, ideally `pkg:oci/...` purl with digest.                                                                     |
| `statements[].status`             | One of `not_affected`, `affected`, `fixed`, `under_investigation`.                                                           |
| `statements[].justification`      | Required when `status = not_affected`. Use a controlled OpenVEX justification (e.g., `vulnerable_code_not_in_execute_path`). |
| `statements[].impact_statement`   | Plain-English rationale visible to auditors.                                                                                 |

## 🔍 Justification Reference

Use the standard OpenVEX justifications:

* `component_not_present`
* `vulnerable_code_not_present`
* `vulnerable_code_not_in_execute_path`
* `vulnerable_code_cannot_be_controlled_by_adversary`
* `inline_mitigations_already_exist`

## 🛡️ Signing Requirements

* VEX documents in `main` MUST be signed before publication. The signing job runs in `release-signing.yml` and emits a Sigstore bundle (or Notation signature manifest) per document.
* Edge clusters consume VEX via `verify-image.sh --policy-file`, which feeds attached predicates into Kyverno's `verifyImages` rule. Unsigned predicates are rejected.
* Rotate the document `version` and `@id` on every revision so consumers can detect updates.

> [!NOTE]
> VEX is **descriptive**, not authoritative. A signed VEX statement records our analysis at a point in time; reviewers should re-evaluate when the image, base layer, or surrounding code path changes.
