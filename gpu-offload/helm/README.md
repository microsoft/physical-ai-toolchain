# GPU-Offload Helm Chart

Deploys the transparent GPU-offloading mutating admission webhook controller that
enables opt-in workloads to be rewritten for GPU offloading. The chart deploys a
non-root controller, ServiceAccount, RBAC, and TLS wiring. The chart is
registry-parameterized and consumes prebuilt external images; it does not build them.

## 📋 Prerequisites

| Requirement       | Detail                                                                                                                                                      |
|-------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Kubernetes        | 1.27+ with admission webhooks enabled                                                                                                                       |
| Helm              | 3.12+                                                                                                                                                       |
| cert-manager      | Optional. Install cluster-wide when `mutate.certManager.enabled` is `true`. The chart defaults to a local-friendly mode that does not require cert-manager. |
| Offloading images | `xavier-mutate` (mutate controller) mirrored into your registry                                                                                             |
| Registry access   | Workload identity (preferred) or an image pull secret                                                                                                       |

> [!IMPORTANT]
> This chart requires the mutate controller image (`xavier-mutate`). The chart
> carries the deployment topology only; the mutate controller is provided as a
> prebuilt image that you supply via `image.registry` or via fully-qualified
> references in `values.yaml`.

## 🚀 Quick Start

```bash
helm install gpu-offload gpu-offload/helm/gpu-offload \
  --namespace gpu-offload --create-namespace \
  --set image.registry=ghcr.io/my-org \
  --set mutate.image.digest=sha256:<mutate-digest>
```

Render the manifests without installing to review them first:

```bash
helm template gpu-offload gpu-offload/helm/gpu-offload \
  --set image.registry=example.azurecr.io
```

## ⚙️ Configuration

| Value                                              | Default         | Description                                                                                                                  |
|----------------------------------------------------|-----------------|------------------------------------------------------------------------------------------------------------------------------|
| `image.registry`                                   | `""`            | Neutral registry. Leave empty to use fully-qualified image names in values, or set to your registry (e.g. `ghcr.io/my-org`). |
| `image.pullPolicy`                                 | `IfNotPresent`  | Pull policy applied to every container.                                                                                      |
| `imagePullSecrets`                                 | `[]`            | Pull secret references. Prefer workload identity; leave empty when using it.                                                 |
| `mutate.image.repository`                          | `xavier-mutate` | Mutate controller image name within `image.registry`.                                                                        |
| `mutate.image.tag`                                 | `""`            | Mutable tag. Leave empty and prefer a digest.                                                                                |
| `mutate.image.digest`                              | `""`            | `sha256:` digest pin. Wins over `tag` when set.                                                                              |
| `mutate.webhookPort`                               | `6443`          | TLS port for the webhook Service and endpoint.                                                                               |
| `mutate.logLevel`                                  | `warning`       | Mutate controller log verbosity.                                                                                             |
| `mutate.certManager.enabled`                       | `false`         | Use cert-manager to provision TLS and CA injection when `true` (production).                                                 |
| `mutate.certManager.duration`                      | `4320h`         | Serving certificate validity window (when using cert-manager).                                                               |
| `mutate.tls.secretName`                            | `""`            | Use an existing `kubernetes.io/tls` Secret in this namespace (preferred).                                                    |
| `mutateScheduling.nodeSelector.kubernetes.io/arch` | `amd64`         | Default architecture selector for the controller pod. Change this for non-amd64 clusters.                                    |

Node-agent staging and privileged hostPath mounts have been removed from this
chart. Mutated workloads should bundle or provide their client libraries via
their own init mechanisms or images.

## 🔑 External-image prerequisite

The chart references the mutate controller image `xavier-mutate` via `image.registry`.
Mirror the image into a registry you control and set `image.registry` accordingly.

```bash
# Example: mirror into your registry (source registry supplied out of band).
crane copy <source-registry>/xavier-mutate@sha256:<digest> \
  example.registry.io/xavier-mutate@sha256:<digest>
```

Grant the cluster pull access with workload identity where possible:

- Assign the `AcrPull` role to the cluster's managed identity (or kubelet identity).
- Configure a federated credential so pods authenticate without stored secrets.
- Leave `imagePullSecrets` empty.

When workload identity is unavailable, create an image pull secret out of band and
reference it by name in `imagePullSecrets`. Never inline registry credentials in
values files.

## 📌 Digest-pinning guidance

Pin both images to immutable `sha256:` digests rather than mutable tags. A digest is
tamper-evident and reproducible; a tag can be repointed after review.

Set `mutate.image.digest`; leave the `tag` field empty.

- When a digest is set it takes precedence over any tag.
- When neither digest nor tag is set, the runtime resolves the registry default
  (typically `:latest`) — acceptable only for throwaway evaluation.

Resolve a digest from a tag before pinning:

```bash
crane digest example.azurecr.io/xavier-mutate:<tag>
```

## ⚠️ Safety caveat

Offloading is opt-in: only workloads labeled `xavier: "true"` are mutated. Offloading a
control-loop `get_action` call across machines injects network latency and jitter into a
15-50 Hz loop, which is a stability and safety risk. Same-node offload is safe;
cross-machine offload of control-loop functions requires explicit review.

## 🏗️ Components

| Template                           | Kind                                 | Purpose                                                         |
|------------------------------------|--------------------------------------|-----------------------------------------------------------------|
| `templates/mutate-deployment.yaml` | ServiceAccount, Service, Deployment  | Runs the mutate controller.                                     |
| `templates/mutating-webhook.yaml`  | Secret, Issuer, Certificate, webhook | Registers the authoritative mutating webhook and TLS materials. |

> [!NOTE]
> When the chart generates the TLS Secret, Helm reuses it on upgrade through
> `lookup`. Delete the Secret to force certificate rotation.
