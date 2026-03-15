---
sidebar_position: 6
title: Cluster Operations and Troubleshooting
description: Accessing OSMO, troubleshooting common issues, and optional deployment scripts
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: reference
keywords:
  - troubleshooting
  - osmo-access
  - vpn
  - port-forward
---

Operations guide for accessing OSMO services, troubleshooting deployment issues, and using optional scripts.

> [!NOTE]
> This page is part of the [deployment guide](README.md). Return there for the full deployment sequence.

## 🔌 Accessing OSMO

OSMO services are deployed to the `osmo-control-plane` namespace. Access method depends on your network configuration.

### Via VPN (Default Private Cluster)

When connected to VPN, OSMO services are accessible via the internal load balancer:

| Service      | URL                   |
|--------------|-----------------------|
| UI Dashboard | `http://10.0.5.7`     |
| API Service  | `http://10.0.5.7/api` |

```bash
# Login to OSMO via internal load balancer
osmo login http://10.0.5.7 --method=dev --username=testuser

# Verify connection
osmo info
osmo backend list
```

> [!NOTE]
> The internal load balancer IP (`10.0.5.7`) is assigned by the AzureML nginx ingress controller. Verify the actual IP with: `kubectl get svc -n azureml azureml-nginx-ingress -o jsonpath='{.status.loadBalancer.ingress[0].ip}'`

### Via Port-Forward (Public Cluster without VPN)

If `should_enable_private_aks_cluster = false` and you are not using VPN, use `kubectl port-forward`:

| Service      | Command                                                               | Local URL               |
|--------------|-----------------------------------------------------------------------|-------------------------|
| UI Dashboard | `kubectl port-forward svc/osmo-ui 3000:80 -n osmo-control-plane`      | `http://localhost:3000` |
| API Service  | `kubectl port-forward svc/osmo-service 9000:80 -n osmo-control-plane` | `http://localhost:9000` |
| Router       | `kubectl port-forward svc/osmo-router 8080:80 -n osmo-control-plane`  | `http://localhost:8080` |

```bash
# Terminal 1: Start port-forward for API service
kubectl port-forward svc/osmo-service 9000:80 -n osmo-control-plane

# Terminal 2: Login and use OSMO CLI
osmo login http://localhost:9000 --method=dev --username=testuser

# Verify connection
osmo info
osmo backend list
```

For full OSMO functionality (UI + API + Router), run port-forwards in separate terminals:

```bash
# Terminal 1: API service (for osmo CLI)
kubectl port-forward svc/osmo-service 9000:80 -n osmo-control-plane

# Terminal 2: UI dashboard (for web browser)
kubectl port-forward svc/osmo-ui 3000:80 -n osmo-control-plane

# Terminal 3: Router (optional, for workflow exec/port-forward)
kubectl port-forward svc/osmo-router 8080:80 -n osmo-control-plane
```

> [!NOTE]
> When accessing OSMO through port-forwarding, `osmo workflow exec` and `osmo workflow port-forward` commands are not supported. These require the router service to be accessible via ingress.

## 🔍 Troubleshooting

### Private Cluster Connectivity

If you see `no such host` errors when running `kubectl` commands:

```text
E1219 15:11:03.714667 memcache.go:265] "Unhandled Error" err="couldn't get current server API group list:
Get \"https://aks-xxx.privatelink.westus3.azmk8s.io:443/api?timeout=32s\":
dial tcp: lookup aks-xxx.privatelink.westus3.azmk8s.io on 10.255.255.254:53: no such host"
```

This indicates the AKS cluster has a private endpoint and your machine cannot resolve the private DNS name.

**Resolution:**

1. Deploy the VPN Gateway: `cd infrastructure/terraform/vpn && terraform apply`
2. Download and import VPN client configuration (see [VPN Gateway](vpn.md))
3. Connect to VPN using Azure VPN Client
4. Verify connectivity: `kubectl cluster-info`

**Alternative:** Redeploy infrastructure with `should_enable_private_aks_cluster = false` in your `terraform.tfvars` for a public AKS control plane. This allows `kubectl` access without VPN while keeping Azure services (Storage, Key Vault, ACR) private if `should_enable_private_endpoint = true`.

### Workload Identity

```bash
az identity federated-credential list --identity-name osmo-identity --resource-group <rg>
az aks show -g <rg> -n <aks> --query oidcIssuerProfile.issuerUrl
```

### ACR Pull

```bash
az aks check-acr --name <aks> --resource-group <rg> --acr <acr>
az acr repository show-tags --name <acr> --repository osmo/osmo-service
```

### Storage Access

```bash
kubectl get secret postgres-secret -n osmo-control-plane
kubectl describe sa osmo-service -n osmo-control-plane
```

## 📁 Directory Structure

```text
002-setup/
├── 01-deploy-robotics-charts.sh
├── 02-deploy-azureml-extension.sh
├── 03-deploy-osmo-control-plane.sh
├── 04-deploy-osmo-backend.sh
├── cleanup/                    # Cleanup scripts
├── config/                     # OSMO configuration templates
├── lib/                        # Shared functions
├── manifests/                  # Kubernetes manifests
├── optional/                   # Volcano scheduler, validation
└── values/                     # Helm values files
```

## 🧩 Optional Scripts

| Script                                    | Purpose                      |
|-------------------------------------------|------------------------------|
| `optional/deploy-volcano-scheduler.sh`    | Volcano (alternative to KAI) |
| `optional/uninstall-volcano-scheduler.sh` | Uninstall Volcano scheduler  |
| `optional/add-user-to-platform.sh`        | Add user to OSMO platform    |

## 🔗 Related

- [Cluster Setup](cluster-setup.md) — deployment scenarios and configuration
- [VPN Gateway](vpn.md) — point-to-site VPN for private cluster access

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
