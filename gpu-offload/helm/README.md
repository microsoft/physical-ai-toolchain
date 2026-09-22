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

## 🚦 Production rollout and recovery

The webhook uses `failurePolicy: Fail` and intercepts only CREATE requests for
workloads labeled `xavier: "true"`. Keep the fail-closed policy so a workload is
not admitted without the client mutation and matching server reconciliation.
Pause new GPU-offload workload creation during controller upgrades and recovery.

### Validate admission

Run a server-side dry-run after every install, upgrade, or rollback. The ConfigMap
exists only for the duration of the probe, and the Pod is never persisted.

```bash
NAMESPACE=gpu-offload
CONTEXT=<kube-context>
PROBE="gpu-offload-webhook-probe-$$"

cleanup_probe() {
  kubectl --context "$CONTEXT" delete configmap "$PROBE" \
    --namespace "$NAMESPACE" --ignore-not-found
}
trap cleanup_probe EXIT

kubectl --context "$CONTEXT" create configmap "$PROBE" \
  --namespace "$NAMESPACE" \
  --from-literal=remote.yaml=$'encryption: false\nnoserverdeployment: true\n' \
  --dry-run=client -o yaml |
  kubectl --context "$CONTEXT" apply -f -

MUTATION_MARKER="$(
  cat <<EOF | kubectl --context "$CONTEXT" apply --dry-run=server -f - \
    -o jsonpath='{.spec.containers[0].env[?(@.name=="XAVIER_CONTAINER")].value}'
apiVersion: v1
kind: Pod
metadata:
  name: $PROBE
  namespace: $NAMESPACE
  annotations:
    xavierconfig: |
      remoteablecm: $PROBE
  labels:
    xavier: "true"
spec:
  containers:
    - name: probe
      image: probe
      command: ["true"]
      env:
        - name: REMOTERPORT
          value: "30000"
EOF
)"
test "$MUTATION_MARKER" = "true"
```

The final command must exit successfully. A timeout, admission rejection, or
missing `true` marker means the controller is not safe for offload workload
creation.

### Roll back a failed upgrade

Select a previously validated revision, wait for the controller Deployment, and
rerun the admission probe before resuming workload creation.

```bash
RELEASE=gpu-offload
NAMESPACE=gpu-offload
CONTEXT=<kube-context>
REVISION=<known-good-revision>

helm --kube-context "$CONTEXT" history "$RELEASE" --namespace "$NAMESPACE"
helm --kube-context "$CONTEXT" rollback "$RELEASE" "$REVISION" \
  --namespace "$NAMESPACE" \
  --wait \
  --cleanup-on-fail \
  --timeout 5m

CONTROLLER="$(
  kubectl --context "$CONTEXT" get deployment \
    --namespace "$NAMESPACE" \
    --selector "app.kubernetes.io/name=gpu-offload,app.kubernetes.io/instance=$RELEASE" \
    -o jsonpath='{.items[0].metadata.name}'
)"
kubectl --context "$CONTEXT" rollout status "deployment/$CONTROLLER" \
  --namespace "$NAMESPACE" \
  --timeout 5m
```

Helm rollback remains available during a webhook outage. The chart resources are
not labeled `xavier: "true"`, and the webhook rules do not intercept UPDATE or
DELETE operations.

### Remove an unavailable webhook

Delete the webhook configuration only when rollback cannot restore admission.
This immediately allows labeled workloads to be created without mutation, so
keep GPU-offload workload creation paused until the controller is restored.

```bash
RELEASE=gpu-offload
CONTEXT=<kube-context>

kubectl --context "$CONTEXT" delete mutatingwebhookconfiguration \
  --selector "app.kubernetes.io/name=gpu-offload,app.kubernetes.io/instance=$RELEASE"
```

Restore the release with `helm upgrade --install`, wait for the controller
Deployment, and run the admission probe before resuming workload creation.

### Uninstall and clean generated resources

Delete or scale down GPU-offload client workloads before removing their remote
servers. Helm removes the controller and webhook resources, but generated server
Deployments, NetworkPolicies, and encryption Secrets are owned by client
workloads rather than by the Helm release.

Run cleanup separately in every workload namespace managed by the controller:

```bash
RELEASE=gpu-offload
CONTROLLER_NAMESPACE=gpu-offload
WORKLOAD_NAMESPACE=<workload-namespace>
CONTEXT=<kube-context>

helm --kube-context "$CONTEXT" uninstall "$RELEASE" \
  --namespace "$CONTROLLER_NAMESPACE" \
  --ignore-not-found

kubectl --context "$CONTEXT" delete deployment,networkpolicy \
  --namespace "$WORKLOAD_NAMESPACE" \
  --selector xavierdeployment=true \
  --ignore-not-found
kubectl --context "$CONTEXT" delete secret \
  --namespace "$WORKLOAD_NAMESPACE" \
  --selector xavier-encryption-secret=true \
  --ignore-not-found
```

Do not delete the generated resources while active clients still depend on the
remote servers or encryption keys.

### Verify recovery

After rollback or reinstall, confirm that the controller is Available and run
the admission probe. After permanent uninstall or emergency removal, confirm
that no webhook remains for the release:

```bash
RELEASE=gpu-offload
CONTEXT=<kube-context>

test -z "$(
  kubectl --context "$CONTEXT" get mutatingwebhookconfiguration \
    --selector "app.kubernetes.io/name=gpu-offload,app.kubernetes.io/instance=$RELEASE" \
    -o name
)"
```

Remove `xavier` labels and `xavierconfig` annotations from workload manifests
before recreating them without GPU offload.
