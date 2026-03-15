---
sidebar_position: 8
title: Private DNS Configuration
description: DNS zone setup for OSMO UI access through private endpoints
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: how-to
keywords:
  - dns
  - private-dns
  - osmo
  - private-endpoints
---

Internal DNS resolution for the OSMO UI service running on an internal LoadBalancer.

> [!NOTE]
> Part of the [Deployment Guide](README.md). Return there for navigation and deployment order.

## 📋 Prerequisites

* Platform infrastructure deployed (`cd deploy/001-iac && terraform apply`)
* VPN Gateway deployed ([VPN Gateway](vpn.md))
* OSMO UI service running with internal LoadBalancer IP

## 🚀 Usage

Get the OSMO UI LoadBalancer IP from your cluster:

```bash
kubectl get svc -n osmo-control-plane osmo-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

Deploy the DNS zone:

```bash
cd deploy/001-iac/dns
terraform init
terraform apply -var="osmo_loadbalancer_ip=10.0.x.x"
```

## ⚙️ Configuration

| Variable                     | Description              | Default      |
|------------------------------|--------------------------|--------------|
| `osmo_loadbalancer_ip`       | Internal LoadBalancer IP | (required)   |
| `osmo_private_dns_zone_name` | DNS zone name            | `osmo.local` |
| `osmo_hostname`              | Hostname within zone     | `dev`        |

## 💡 How It Works

1. DNS zone (e.g., `osmo.local`) is linked to the VNet
2. A record (`dev.osmo.local`) points to the LoadBalancer IP
3. VPN clients use the Private DNS Resolver to resolve internal names
4. Access OSMO UI at `http://dev.osmo.local` when connected via VPN

## 🔗 Related

* [Infrastructure Deployment](infrastructure.md) — Main infrastructure documentation
* [VPN Gateway](vpn.md) — VPN Gateway setup (required for DNS resolution)

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
