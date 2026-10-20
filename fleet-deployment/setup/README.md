# Fleet Deployment Setup

Build, sign, and attest inference container images for the robot fleet.

## 📋 Prerequisites

| Tool                                       | Purpose                                   | Required for                  |
|--------------------------------------------|-------------------------------------------|-------------------------------|
| `az` CLI                                   | Azure auth, `az ml`, `az acr login`       | AzureML models, ACR pushes    |
| `docker` (with `buildx`)                   | Local image build + push to ACR           | Build                         |
| `jq`                                       | Terraform output parsing, buildx metadata | Build                         |
| `uv`, `curl`                               | MLflow model lookup and download          | MLflow models                 |
| `cosign` ≥2.2                              | Image signing + attestation               | `sigstore` mode               |
| `syft`                                     | SBOM generation                           | Attest (unless `--skip-sbom`) |
| `notation` ≥1.1 + `notation-azure-kv` ≥1.1 | AKV-backed image signing                  | `notation` mode               |
| `oras` ≥1.2                                | SBOM upload as OCI referrer               | `notation` mode               |

Run `az login` against every Entra tenant you need (AML tenant, ACR tenant,
AKV tenant). Cross-tenant flows require a session in each.

Builds run on the local Docker daemon and push layers directly to ACR — the
script no longer routes through ACR Tasks, so the ~100 MB source-upload cap
that blocks large baked-in models does not apply.

## 📂 Files

| File                              | Purpose                                                                                   |
|-----------------------------------|-------------------------------------------------------------------------------------------|
| `build-model-image.sh`            | Package a model from AzureML, MLflow, or a checkpoint directory; push; sign ACR images    |
| `attest-image.sh`                 | Attach an SBOM and an optional OpenVEX attestation to an already-built image              |
| `Dockerfile.inference`            | Carrier: `COPY model/ /policy/` on `scratch`, or on a runtime base for a runnable carrier |
| `defaults.conf`                   | Centralized defaults consumed by both scripts                                             |
| `tests/test-model-image-pod.yaml` | Smoke-test pod that mounts a built model image via OCI image volume and lists `/policy/`  |

## 🚀 Quick Start

```bash
# 1. Build and sign (one pass, no rebuild needed for attestations)
fleet-deployment/setup/build-model-image.sh \
  --model-name lerobot-act-pickplace

# Build prints the digest-pinned reference, e.g.
#   Image (digest): acrfleetprod001.azurecr.io/lerobot-act-pickplace@sha256:abc...

# 2. Attach an SBOM; add --vex-file when an OpenVEX document exists
fleet-deployment/setup/attest-image.sh \
  --image acrfleetprod001.azurecr.io/lerobot-act-pickplace@sha256:abc...
```

The build script prints the exact `attest-image.sh` invocation to run next.

## 🔁 Workflow

```text
                 ┌──────────────────────┐
                 │ build-model-image.sh │  build + push + cosign sign
                 │                      │  + verify-image.sh self-check
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

| Mode       | Signature                    | SBOM (via `attest-image.sh`)    | VEX (via `attest-image.sh`)    |
|------------|------------------------------|---------------------------------|--------------------------------|
| `sigstore` | `cosign sign` (keyless OIDC) | `cosign attest --type spdxjson` | `cosign attest --type openvex` |
| `notation` | `notation sign` (AKV-backed) | `oras attach` as OCI referrer   | _not supported_                |
| `none`     | _no signature_               | _attest refuses `--mode none`_  | _n/a_                          |

Use `none` only for local development. Kyverno-enforcing clusters will reject
unsigned images.

## 📄 OpenVEX Workflow

The `scratch` base has no VEX document because it has no packages or
vulnerabilities to describe. For a runnable base, generate
`security/vex/inference-base.openvex.json` and follow the governing
[VEX standards](../../.github/instructions/vex-standards.instructions.md)
when assigning one of these statuses:

| `status`              | When to use                                                                                                    |
|-----------------------|----------------------------------------------------------------------------------------------------------------|
| `under_investigation` | Product-specific reachability or the possibility of exploitation is not established. Retain this safe default. |
| `not_affected`        | Product-specific analysis proves exploitation is impossible. Add `justification` and `status_notes`.           |
| `affected`            | Product-specific analysis confirms vulnerability. Add an `action_statement` and `status_notes`.                |
| `fixed`               | The exact product digest contains the verified remediation. Add `status_notes`.                                |

Generate or refresh the VEX whenever:

1. `DEFAULT_INFERENCE_BASE_IMAGE` in [`defaults.conf`](defaults.conf) is bumped
   to a new base digest, or
2. Scanner feeds report new CVEs against the existing digest.

```bash
# Create or merge the VEX document from Trivy + Grype findings (writes .scan/* locally)
scripts/security/generate-vex.sh

# Record product-specific evidence; retain under_investigation otherwise.

# Re-attest against existing images that should pick up the new dispositions
fleet-deployment/setup/attest-image.sh \
  --image <digest-ref> \
  --skip-sbom \
  --vex-file <path/to/document.openvex.json>
```

## 💻 Local Profile

On a workstation running the [local OSMO profile](../../infrastructure/local/README.md), `--profile local` packages a model from the local MLflow registry and pushes it to the loopback registry that K3s pulls from:

```bash
fleet-deployment/setup/build-model-image.sh --profile local --model-name lerobot-act-pickplace
```

The build needs no Azure login and skips signing and attestation, because only this host's K3s pulls from that registry. `--model-path` packages a checkpoint directory instead of a registered model, on either profile.

OSMO chooses pod templates per platform, so an OSMO workflow cannot mount a carrier it names at submit time. To evaluate a containerized policy on OSMO, build a runnable carrier on the evaluation runtime image with `--base-image`; the evaluation runs that image as its task image and reads the policy from `/policy/`. Kubernetes deployments outside OSMO mount the `scratch` carrier as an image volume.

## 🏗️ Base Image Pinning

`DEFAULT_INFERENCE_BASE_IMAGE` in [`defaults.conf`](defaults.conf) is `scratch`.
The image is a passive model artifact carrier mounted into consumer pods via
OCI image volumes (KEP-4639) and is never executed, so it has no OS, no
packages, and no scannable CVE surface. OpenVEX requires vulnerability
statements, so the default attestation flow skips VEX.

`--base-image` builds a runnable carrier for one build, such as the local
evaluation images. To publish runnable carriers to ACR by default (e.g. an
embedded runtime for on-node inference), bump the base intentionally:

1. Pick the new base digest (e.g. `crane digest mcr.microsoft.com/azureml/minimal-py312-inference:1.x`).
2. Update `DEFAULT_INFERENCE_BASE_IMAGE` in `defaults.conf`.
3. Run `scripts/security/generate-vex.sh --image <new-digest>`.
4. Record product-specific evidence in `security/vex/inference-base.openvex.json`; retain `under_investigation` when evidence does not support another status.
5. Set `DEFAULT_VEX_FILE` to the generated document and commit the base-image and VEX changes together.

## 🔧 Common Overrides

| Override                                         | Effect                                                |
|--------------------------------------------------|-------------------------------------------------------|
| `--tf-dir none`                                  | Skip Terraform discovery; values from flags/env only  |
| `--profile local`                                | MLflow model to the local registry, unsigned          |
| `--model-path DIR`                               | Package a checkpoint directory instead of a registry  |
| `--model-version 7`                              | Build a specific model version (default: `latest`)    |
| `--image-tag 7-sha-abc1234`                      | Override the auto-derived tag                         |
| `--verify-mode notation` + `--akv-key-id`        | Use AKV-backed Notation signing instead of sigstore   |
| `--acr-name`/`--acr-tenant`/`--acr-subscription` | Required in cross-tenant or no-Terraform mode         |
| `--base-image IMAGE`, `INFERENCE_BASE_IMAGE=…`   | One-off base override without editing `defaults.conf` |
| `--skip-sbom` / `--skip-vex`                     | Selective attestation refresh                         |
| `--vex-file PATH`                                | Attach an explicit OpenVEX document                   |

Build scripts resolve values in this order: **Terraform output → CLI flag →
`DEFAULT_*` env var → `defaults.conf` literal → fatal**.

`attest-image.sh` does not read Terraform outputs. It resolves values from CLI
flags, `DEFAULT_*` values, and `defaults.conf`.

## 🧩 Enabling OCI Image Volumes on k3s

Consumer pods mount the model carrier via OCI image volumes (KEP-4639). Two
things must hold on every node that runs them:

| Requirement                | When it is met                                                                                                     |
|----------------------------|--------------------------------------------------------------------------------------------------------------------|
| `ImageVolume` feature gate | On by default from Kubernetes 1.35 and locked on in 1.36; enable it yourself on 1.31 to 1.34                       |
| containerd 2.1 or newer    | Every current K3s patch line; earlier 1.32 patches, such as v1.32.6 with containerd 2.0.5, cannot mount the volume |

The repository's K3s installer, `data-pipeline/setup/hil/01-install-k3s.sh`,
pins a 1.35 release, which meets both. Check the runtime in the
`CONTAINER-RUNTIME` column of `kubectl get nodes -o wide`.

On 1.31 to 1.34, enable the gate on both the apiserver and the kubelet via
`/etc/rancher/k3s/config.yaml`:

```yaml
kube-apiserver-arg:
  - "feature-gates=ImageVolume=true"
kubelet-arg:
  - "feature-gates=ImageVolume=true"
```

Restart the node service to apply (`sudo systemctl restart k3s` on the server
node, `sudo systemctl restart k3s-agent` on agents — agents only need the
`kubelet-arg` entry). Verify the gate is live:

```bash
sudo k3s kubectl get --raw /metrics | grep kubernetes_feature_enabled | grep ImageVolume
```

## 🔍 Troubleshooting

| Symptom                                                 | Likely cause                                                                                |
|---------------------------------------------------------|---------------------------------------------------------------------------------------------|
| `Subscription '…' (tenant …) not in az session for …`   | Missing `az login --tenant <id>` for that tenant                                            |
| `--akv-key-id (or DEFAULT_AKV_KEY_URI) is required …`   | `notation` mode without an AKV key URI                                                      |
| `Unexpected digest shape from az acr repository show`   | ACR returned no manifest — usually a transient ACR Tasks failure                            |
| `VEX file not found: …`                                 | An explicit `--vex-file` or `DEFAULT_VEX_FILE` path is incorrect                            |
| `verify-image.sh not present (PR #592 not merged yet)`  | Expected until [PR #592](https://github.com/microsoft/physical-ai-toolchain/pull/592) lands |
| Sigstore signing locally rejected by Kyverno on cluster | Signed with developer Entra identity; production builds must run in CI                      |

## 🧹 Model Image Cleanup on Cluster Nodes

Model artifacts are baked into the inference image, so a node's containerd
image store grows by one full model copy per pulled tag. There is no
cluster-wide policy or CronJob managing this — image GC is a kubelet concern
and runs per-node.

Kubelet image GC is driven by three flags:

| Flag                        | Default | Purpose                                           |
|-----------------------------|---------|---------------------------------------------------|
| `--image-gc-high-threshold` | `85`    | Disk usage % that triggers GC                     |
| `--image-gc-low-threshold`  | `80`    | GC removes unused images until disk drops to this |
| `--image-maximum-gc-age`    | `0`     | Max age of unused images before GC (e.g. `168h`)  |

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

- [`scripts/security/generate-vex.sh`](../../scripts/security/generate-vex.sh) — scan + OpenVEX merge generator
- [VEX standards](../../.github/instructions/vex-standards.instructions.md) — authoring and mutation contract
- [`fleet-deployment/specifications/fleet-deployment.specification.md`](../specifications/fleet-deployment.specification.md) — end-to-end pipeline contract
