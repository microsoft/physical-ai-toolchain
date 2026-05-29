# Fleet Deployment Setup

Build, sign, and attest inference container images for the robot fleet.

## 📋 Prerequisites

| Tool          | Purpose                                      | Required for                  |
|---------------|----------------------------------------------|-------------------------------|
| `az` CLI      | Azure auth, `az ml`, `az acr login`          | All steps                     |
| `docker` (with `buildx`) | Local image build + push to ACR   | Build                         |
| `jq`          | Terraform output parsing, buildx metadata    | Build                         |
| `cosign` ≥2.2 | Image signing + attestation                  | `sigstore` mode               |
| `syft`        | SBOM generation                              | Attest (unless `--skip-sbom`) |
| `notation` ≥1.1 + `notation-azure-kv` ≥1.1 | AKV-backed image signing | `notation` mode |
| `oras` ≥1.2   | SBOM upload as OCI referrer                  | `notation` mode               |

Run `az login` against every Entra tenant you need (AML tenant, ACR tenant,
AKV tenant). Cross-tenant flows require a session in each.

Builds run on the local Docker daemon and push layers directly to ACR — the
script no longer routes through ACR Tasks, so the ~100 MB source-upload cap
that blocks large baked-in models does not apply.

## 📂 Files

| File                       | Purpose                                                         |
|----------------------------|-----------------------------------------------------------------|
| `build-aml-model-image.sh` | Download AML model, `docker buildx build --push`, sign image, self-verify |
| `attest-image.sh`          | Attach SBOM + OpenVEX attestations to an already-built image    |
| `Dockerfile.inference`     | Base image + `COPY model/` + `act_inference_node` entrypoint    |
| `defaults.conf`            | Centralized defaults consumed by both scripts                   |

## 🚀 Quick Start

```bash
# 1. Build and sign (one pass, no rebuild needed for attestations)
fleet-deployment/setup/build-aml-model-image.sh \
  --model-name lerobot-act-pickplace

# Build prints the digest-pinned reference, e.g.
#   Image (digest): acrfleetprod001.azurecr.io/lerobot-act-pickplace@sha256:abc...

# 2. Attach SBOM + OpenVEX attestations
fleet-deployment/setup/attest-image.sh \
  --image acrfleetprod001.azurecr.io/lerobot-act-pickplace@sha256:abc...
```

The build script prints the exact `attest-image.sh` invocation to run next.

## 🔁 Workflow

```text
                 ┌──────────────────────┐
                 │ build-aml-model-image│  build + push + cosign sign
                 │       .sh            │  + verify-image.sh self-check
                 └──────────┬───────────┘
                            │  emits digest-pinned image ref
                            ▼
                 ┌──────────────────────┐
                 │  attest-image.sh     │  cosign attest spdxjson (SBOM)
                 │  (sigstore mode)     │  + cosign attest openvex (VEX)
                 └──────────────────────┘
```

Build and attest are decoupled on purpose:

- The build pipeline never blocks on VEX triage.
- Security can refresh the VEX (re-scan, re-triage, re-attest) without rebuilding.
- The same digest can carry multiple attestations over time; verifiers fetch all.

## 🔐 Signing Modes (`--verify-mode`)

| Mode       | Signature                       | SBOM (via `attest-image.sh`)        | VEX (via `attest-image.sh`) |
|------------|---------------------------------|-------------------------------------|-----------------------------|
| `sigstore` | `cosign sign` (keyless OIDC)    | `cosign attest --type spdxjson`     | `cosign attest --type openvex` |
| `notation` | `notation sign` (AKV-backed)    | `oras attach` as OCI referrer       | _not supported_             |
| `none`     | _no signature_                  | _attest refuses `--mode none`_      | _n/a_                       |

Use `none` only for local development. Kyverno-enforcing clusters will reject
unsigned images.

## 📄 OpenVEX Workflow

[`security/vex/inference-base.openvex.json`](../../security/vex/inference-base.openvex.json)
is the committed VEX document for the pinned base image. Every CVE statement
must carry one of:

| `status`              | When to use                                                          |
|-----------------------|----------------------------------------------------------------------|
| `not_affected`        | CVE present in a package we ship, but our usage path is not reachable. Add `justification`. |
| `affected`            | Exploitable. Add `action_statement` (e.g. "upgrade to 2.4").         |
| `fixed`               | Patched in this digest.                                              |
| `under_investigation` | Triage pending. **Not accepted by strict Kyverno policies.**         |

Refresh the VEX whenever:

1. `DEFAULT_INFERENCE_BASE_IMAGE` in [`defaults.conf`](defaults.conf) is bumped
   to a new base digest, or
2. Scanner feeds report new CVEs against the existing digest.

```bash
# Regenerate stub from latest Trivy + Grype findings (writes .scan/* locally)
scripts/security/generate-vex.sh

# Edit security/vex/inference-base.openvex.json: triage each statement.

# Re-attest against existing images that should pick up the new dispositions
fleet-deployment/setup/attest-image.sh --image <digest-ref> --skip-sbom
```

## 🏗️ Base Image Pinning

`DEFAULT_INFERENCE_BASE_IMAGE` in [`defaults.conf`](defaults.conf) is pinned to a
digest — not a floating tag — so the committed VEX provably applies to every
build. Bumping the base is an intentional event:

1. Pick the new base digest (e.g. `crane digest mcr.microsoft.com/azureml/minimal-py312-inference:1.x`).
2. Update `DEFAULT_INFERENCE_BASE_IMAGE` in `defaults.conf`.
3. Run `scripts/security/generate-vex.sh --image <new-digest>`.
4. Triage `security/vex/inference-base.openvex.json`.
5. Commit all three changes together.

## 🔧 Common Overrides

| Override                                  | Effect                                            |
|-------------------------------------------|---------------------------------------------------|
| `--tf-dir none`                           | Skip Terraform discovery; values from flags/env only |
| `--model-version 7`                       | Build a specific AML model version (default: `latest`) |
| `--image-tag 7-sha-abc1234`               | Override the auto-derived tag                     |
| `--verify-mode notation` + `--akv-key-id` | Use AKV-backed Notation signing instead of sigstore |
| `--acr-name`/`--acr-tenant`/`--acr-subscription` | Required in cross-tenant or no-Terraform mode |
| `INFERENCE_BASE_IMAGE=…` env var          | One-off base override without editing `defaults.conf` |
| `--skip-sbom` / `--skip-vex`              | Selective attestation refresh                     |

Per-value resolution order: **Terraform output → CLI flag → `DEFAULT_*` env var
→ `defaults.conf` literal → fatal**.

## 🔍 Troubleshooting

| Symptom                                                    | Likely cause                                                              |
|------------------------------------------------------------|---------------------------------------------------------------------------|
| `Subscription '…' (tenant …) not in az session for …`      | Missing `az login --tenant <id>` for that tenant                          |
| `--akv-key-id (or DEFAULT_AKV_KEY_URI) is required …`      | `notation` mode without an AKV key URI                                    |
| `Unexpected digest shape from az acr repository show`      | ACR returned no manifest — usually a transient ACR Tasks failure          |
| `VEX file not present at '…' — skipping OpenVEX`           | Wrong `--vex-file` path or VEX not committed                              |
| `verify-image.sh not present (PR #592 not merged yet)`     | Expected until [PR #592](https://github.com/microsoft/physical-ai-toolchain/pull/592) lands |
| Sigstore signing locally rejected by Kyverno on cluster    | Signed with developer Entra identity; production builds must run in CI    |

## 🧹 Model Image Cleanup on Cluster Nodes

Model artifacts are baked into the inference image, so a node's containerd
image store grows by one full model copy per pulled tag. There is no
cluster-wide policy or CronJob managing this — image GC is a kubelet concern
and runs per-node.

Kubelet image GC is driven by three flags:

| Flag                          | Default | Purpose                                           |
|-------------------------------|---------|---------------------------------------------------|
| `--image-gc-high-threshold`   | `85`    | Disk usage % that triggers GC                     |
| `--image-gc-low-threshold`    | `80`    | GC removes unused images until disk drops to this |
| `--image-maximum-gc-age`      | `0`     | Max age of unused images before GC (e.g. `168h`)  |

Edge nodes baking multi-gigabyte models should tighten the thresholds and
cap age. For k3s, set kubelet args in the install:

```bash
curl -sfL https://get.k3s.io | sh -s - \
  --kubelet-arg=image-gc-high-threshold=70 \
  --kubelet-arg=image-gc-low-threshold=60 \
  --kubelet-arg=image-maximum-gc-age=168h
```

For manual cleanup on a k3s node:

```bash
sudo k3s crictl rmi --prune
```

This removes every image not referenced by a running container. Pair it with
a digest-pinned FluxCD `ImagePolicy` so the next reconcile re-pulls only the
current model image.

## 📚 Related

- [`scripts/security/generate-vex.sh`](../../scripts/security/generate-vex.sh) — scan + VEX stub generator
- [`security/vex/inference-base.openvex.json`](../../security/vex/inference-base.openvex.json) — committed VEX
- [`fleet-deployment/specifications/fleet-deployment.specification.md`](../specifications/fleet-deployment.specification.md) — end-to-end pipeline contract
