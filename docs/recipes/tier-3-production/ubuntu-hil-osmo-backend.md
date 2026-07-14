---
title: Ubuntu HiL OSMO Backend
description: Connect an Ubuntu K3s compute plane to the private OSMO control plane in AKS and run CPU and no-command HiL gates.
author: Microsoft Robotics-AI Team
ms.date: 2026-07-15
ms.topic: tutorial
---

Connect one Ubuntu K3s cluster to the existing private OSMO control plane in AKS. Complete the CPU smoke and independently non-commanding UR10E-shaped dry run before adding Arc workload identity, GPU execution, storage transfer, or physical motion.

## Prerequisites

| Requirement                                                    | Runs from                  | Purpose                                  |
|----------------------------------------------------------------|----------------------------|------------------------------------------|
| [Ubuntu Edge K3s Setup](../../data-pipeline/edge-k3s-setup.md) | Ubuntu host                | VPN and pinned K3s                       |
| Existing OSMO 6.3 control plane                                | AKS                        | Workflow API and ConfigMap desired state |
| Private RFC1918 frontend IP                                    | Azure VNet                 | Stable OSMO endpoint                     |
| Azure CLI, Terraform, Helm, kubectl, OSMO CLI                  | Dual-cluster operator host | AKS and OSMO administration              |
| Explicit AKS kubeconfig/context                                | Dual-cluster operator host | Control-plane target safety              |
| Explicit K3s kubeconfig/context                                | Dual-cluster operator host | Edge target safety                       |
| Protected OSMO profile directory                               | Dual-cluster operator host | Isolated login and pool selection        |

This recipe uses private `http://` and `ws://` inside the certificate-authenticated VPN. Do not expose this profile publicly.

## Configure Operator Values

Run from a VPN-connected host that can reach both Kubernetes APIs. For the initial single-host path, run on the Ubuntu desktop after installing Azure CLI, Helm, and OSMO CLI; its protected K3s kubeconfig uses the local API endpoint.

```bash
export OSMO_PRIVATE_IP="<reserved-rfc1918-ip>"
export OSMO_PRIVATE_URL="http://${OSMO_PRIVATE_IP}"
export HIL_BACKEND_NAME="<unique-site-backend-name>"
export HIL_POOL_NAME="$HIL_BACKEND_NAME"
export HIL_OPERATOR_NAMESPACE="osmo-hil-operator"
export HIL_WORKFLOW_NAMESPACE="osmo-hil-workflows"
export AKS_KUBECONFIG="$HOME/.kube/physical-ai-toolchain/<aks-name>.yaml"
export AKS_CONTEXT="<aks-context-name>"
export AKS_RESOURCE_ID="<full-aks-resource-id>"
export EDGE_KUBECONFIG="<protected-k3s-kubeconfig-path>"
export EDGE_CONTEXT="<edge-context-name>"
export EDGE_NODE_NAME="<unique-edge-node-name>"
export EDGE_K3S_VERSION="v1.32.6+k3s1"
export OSMO_LOGIN_METHOD="<approved-osmo-login-method>"
export OSMO_USERNAME="<approved-osmo-username>"
export OSMO_PROFILE_DIR="$HOME/.config/physical-ai-toolchain/osmo-hil"
export OSMO_TOKEN_DIR="$HOME/.local/share/physical-ai-toolchain/osmo-secrets"
export REGISTRY_CONFIG_FILE="$OSMO_TOKEN_DIR/registry-config.json"
export HIL_RESULTS_DIR="$HOME/.local/share/physical-ai-toolchain/results"
```

Create protected directories:

```bash
install -d -m 0700 "$OSMO_PROFILE_DIR" "$OSMO_TOKEN_DIR" "$HIL_RESULTS_DIR"
```

Place a Docker `config.json` containing read credentials for the OSMO image registry at
`$REGISTRY_CONFIG_FILE` through the approved secret-management handoff, then set its mode to `0600`.
Do not paste the credential into a command line or `.env.local`.

## Reserve the Private Endpoint

Set `OSMO_PRIVATE_IP` to an unused address in the AzureML ingress subnet. Confirm Azure does not already assign the address to another interface or LoadBalancer before applying the OSMO Service.

The Service remains private because `internal-lb-ingress.yaml` retains the Azure internal LoadBalancer annotation. The script rejects non-RFC1918 addresses.

## Apply the AKS HiL Backend Overlay

Preview from the dual-cluster operator host:

```bash
infrastructure/setup/03-deploy-osmo.sh \
  --kubeconfig "$AKS_KUBECONFIG" \
  --context "$AKS_CONTEXT" \
  --expected-aks-resource-id "$AKS_RESOURCE_ID" \
  --private-service-ip "$OSMO_PRIVATE_IP" \
  --hil-backend-name "$HIL_BACKEND_NAME" \
  --hil-pool-name "$HIL_POOL_NAME" \
  --hil-workflow-namespace "$HIL_WORKFLOW_NAMESPACE" \
  --config-preview
```

Apply the control-plane release and additive ConfigMap state:

```bash
infrastructure/setup/03-deploy-osmo.sh \
  --kubeconfig "$AKS_KUBECONFIG" \
  --context "$AKS_CONTEXT" \
  --expected-aks-resource-id "$AKS_RESOURCE_ID" \
  --private-service-ip "$OSMO_PRIVATE_IP" \
  --hil-backend-name "$HIL_BACKEND_NAME" \
  --hil-pool-name "$HIL_POOL_NAME" \
  --hil-workflow-namespace "$HIL_WORKFLOW_NAMESPACE"
```

The overlay adds the HiL backend and pool beside the existing `default` backend and pool. It does not use `osmo config update`; OSMO 6.3 ConfigMap mode owns this desired state through Helm.

Validate the private Service:

```bash
kubectl --kubeconfig "$AKS_KUBECONFIG" \
  --context "$AKS_CONTEXT" \
  get service azureml-ingress-nginx-internal-lb \
  --namespace azureml \
  -o jsonpath='{.metadata.annotations.service\.beta\.kubernetes\.io/azure-load-balancer-internal}{" "}{.spec.loadBalancerIP}{"\n"}'
```

Expected output starts with `true` and ends with `$OSMO_PRIVATE_IP`.

## Prepare an Isolated OSMO Profile

Run from the dual-cluster operator host:

```bash
XDG_CONFIG_HOME="$OSMO_PROFILE_DIR" osmo login "$OSMO_PRIVATE_URL" \
  --method "$OSMO_LOGIN_METHOD" \
  --username "$OSMO_USERNAME"
```

Use the authentication method and username approved for the target OSMO deployment. Keep the endpoint private. Do not use development authentication for public, multi-user, or production exposure.

## Issue the External Backend Token

Preview from the dual-cluster operator host:

```bash
infrastructure/setup/04-deploy-osmo-external-backend.sh \
  --aks-kubeconfig "$AKS_KUBECONFIG" \
  --aks-context "$AKS_CONTEXT" \
  --aks-resource-id "$AKS_RESOURCE_ID" \
  --edge-kubeconfig "$EDGE_KUBECONFIG" \
  --edge-context "$EDGE_CONTEXT" \
  --edge-node-name "$EDGE_NODE_NAME" \
  --edge-k3s-version "$EDGE_K3S_VERSION" \
  --service-url "$OSMO_PRIVATE_URL" \
  --backend-name "$HIL_BACKEND_NAME" \
  --pool-name "$HIL_POOL_NAME" \
  --operator-namespace "$HIL_OPERATOR_NAMESPACE" \
  --workflow-namespace "$HIL_WORKFLOW_NAMESPACE" \
  --token-file "$OSMO_TOKEN_DIR/${HIL_BACKEND_NAME}.token" \
  --token-metadata-file "$OSMO_TOKEN_DIR/${HIL_BACKEND_NAME}.metadata.json" \
  --osmo-config-dir "$OSMO_PROFILE_DIR" \
  --registry-config-file "$REGISTRY_CONFIG_FILE" \
  --issue-token \
  --token-expiry "$(date -u -d '+7 days' +%F)" \
  --config-preview
```

Set an expiry that matches the validation window. Issue the token and deploy the edge operator:

```bash
infrastructure/setup/04-deploy-osmo-external-backend.sh \
  --aks-kubeconfig "$AKS_KUBECONFIG" \
  --aks-context "$AKS_CONTEXT" \
  --aks-resource-id "$AKS_RESOURCE_ID" \
  --edge-kubeconfig "$EDGE_KUBECONFIG" \
  --edge-context "$EDGE_CONTEXT" \
  --edge-node-name "$EDGE_NODE_NAME" \
  --edge-k3s-version "$EDGE_K3S_VERSION" \
  --service-url "$OSMO_PRIVATE_URL" \
  --backend-name "$HIL_BACKEND_NAME" \
  --pool-name "$HIL_POOL_NAME" \
  --operator-namespace "$HIL_OPERATOR_NAMESPACE" \
  --workflow-namespace "$HIL_WORKFLOW_NAMESPACE" \
  --token-file "$OSMO_TOKEN_DIR/${HIL_BACKEND_NAME}.token" \
  --token-metadata-file "$OSMO_TOKEN_DIR/${HIL_BACKEND_NAME}.metadata.json" \
  --osmo-config-dir "$OSMO_PROFILE_DIR" \
  --registry-config-file "$REGISTRY_CONFIG_FILE" \
  --issue-token \
  --token-expiry "$(date -u -d '+7 days' +%F)"
```

This generic recipe uses the chart and image versions pinned in `infrastructure/setup/defaults.conf`.

The script:

1. Verifies the AKS and K3s contexts and rejects equal cluster identities.
2. Verifies the OSMO endpoint version matches the configured 6.3 image.
3. Verifies the AKS release contains the matching backend, pool, namespace, service URL, and router URL.
4. Installs the checksum-pinned KAI chart on K3s.
5. Creates the token Secret from a protected file.
6. Renders and rejects privileged, host-networked, host-mounted, or `cluster-admin` operator resources.
7. Deploys the OSMO backend listener and worker.
8. Verifies the listener and worker deployments reach a ready state.

Do not copy the token into command arguments, `.env.local`, Helm values, or Git.

## Verify Backend Health

Run from the dual-cluster operator host:

```bash
XDG_CONFIG_HOME="$OSMO_PROFILE_DIR" osmo config show BACKEND "$HIL_BACKEND_NAME"
```

Confirm:

| Field                               | Expected value            |
|-------------------------------------|---------------------------|
| `name`                              | `$HIL_BACKEND_NAME`       |
| `k8s_namespace`                     | `$HIL_WORKFLOW_NAMESPACE` |
| `router_address`                    | `ws://$OSMO_PRIVATE_IP`   |
| `scheduler_settings.scheduler_name` | `kai-scheduler`           |
| `online`                            | `true`                    |

Verify the edge operator on K3s:

```bash
kubectl --kubeconfig "$EDGE_KUBECONFIG" \
  --context "$EDGE_CONTEXT" \
  get deployments,pods \
  --namespace "$HIL_OPERATOR_NAMESPACE"
```

## Run the CPU Gate

Preview from the dual-cluster operator host:

```bash
evaluation/hil/scripts/run-cpu-smoke.sh \
  --pool "$HIL_POOL_NAME" \
  --service-url "$OSMO_PRIVATE_URL" \
  --osmo-config-dir "$OSMO_PROFILE_DIR" \
  --config-preview
```

Submit and wait for terminal success:

```bash
evaluation/hil/scripts/run-cpu-smoke.sh \
  --pool "$HIL_POOL_NAME" \
  --service-url "$OSMO_PRIVATE_URL" \
  --osmo-config-dir "$OSMO_PROFILE_DIR"
```

The workflow requests one CPU, 128 MiB memory, and zero GPUs. It runs as a non-root user and fails if `/dev/nvidia0` is present in the container.

Do not continue if this workflow times out, fails, or runs on the wrong cluster.

## Run the No-Command HiL Gate

### Local validation

Run first on the Ubuntu host:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh \
  --mode local \
  --output-dir "$HIL_RESULTS_DIR/ur10e-no-command" \
  --config-preview
```

Run and verify artifacts:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh \
  --mode local \
  --output-dir "$HIL_RESULTS_DIR/ur10e-no-command"
```

Expected summary fields:

| Field                    | Expected value         |
|--------------------------|------------------------|
| `status`                 | `passed`               |
| `proposed_actions`       | `10`                   |
| `applied_actions`        | `0`                    |
| `negative_command_probe` | `passed`               |
| `command_transport`      | `none`                 |
| `rejection_code`         | `NO_COMMAND_TRANSPORT` |

### OSMO validation

Run from the dual-cluster operator host:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh \
  --mode osmo \
  --pool "$HIL_POOL_NAME" \
  --service-url "$OSMO_PRIVATE_URL" \
  --osmo-config-dir "$OSMO_PROFILE_DIR" \
  --workflow-name "${HIL_BACKEND_NAME}-ur10e-no-command" \
  --config-preview
```

Submit and wait for terminal success:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh \
  --mode osmo \
  --pool "$HIL_POOL_NAME" \
  --service-url "$OSMO_PRIVATE_URL" \
  --osmo-config-dir "$OSMO_PROFILE_DIR" \
  --workflow-name "${HIL_BACKEND_NAME}-ur10e-no-command"
```

The dry-run adapter contains no RTDE control client, ROS command publisher, serial interface, USB device, CAN interface, host mount, or robot endpoint. Every proposed action is deliberately sent to the adapter boundary and must raise `NO_COMMAND_TRANSPORT`.

> [!WARNING]
> Stop after this gate. This implementation does not support physical motion. Select the exact robot/controller, policy, command owner, limits, operator, safe pose, and independent E-stop procedure before adding a bounded-motion adapter.

## Enable Optional Arc

Arc is independent of the OSMO path. Complete the private backend and CPU gate first, then follow [Enable Azure Arc](../../data-pipeline/edge-k3s-setup.md#enable-azure-arc) when Azure management or workload identity is required.

The initial CPU and no-command workflows require no Arc extension and no storage identity.

## Reconnect Validation

On the Ubuntu host, disconnect and reconnect strongSwan during a maintenance window:

```bash
sudo ipsec down "$VPN_CONNECTION_NAME"
sudo ipsec up "$VPN_CONNECTION_NAME"
```

Re-run VPN status:

```bash
data-pipeline/setup/edge/02-configure-vpn.sh \
  --status \
  --connection-name "$VPN_CONNECTION_NAME" \
  --azure-vnet-cidr "$AZURE_VNET_CIDR" \
  --p2s-cidr "$P2S_CLIENT_CIDR" \
  --osmo-url "$OSMO_PRIVATE_URL"
```

Confirm the existing backend returns online without reinstalling K3s or the operator.

## Troubleshooting

| Symptom                                       | Check                                                                              |
|-----------------------------------------------|------------------------------------------------------------------------------------|
| OSMO endpoint unreachable from host           | strongSwan status, P2S address, Azure route, internal LoadBalancer IP              |
| Host reaches OSMO but backend remains offline | K3s pod egress, token expiry metadata, listener logs, `ws://` router URL           |
| Backend values mismatch                       | Re-run `03-deploy-osmo.sh` with identical backend, pool, namespace, and private IP |
| KAI workloads stay pending                    | `schedulingshard/default`, KAI operator rollout, workflow namespace events         |
| Arc agents fail                               | Arc outbound endpoints, Azure CLI tenant/subscription, `azure-arc` namespace       |
| No-command run reports an applied action      | Stop immediately; do not add robot access or physical motion                       |
