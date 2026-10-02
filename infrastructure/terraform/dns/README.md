---
title: Private DNS for OSMO UI
description: Private DNS zone for OSMO UI hostname resolution on the internal load balancer
author: Microsoft Robotics-AI Team
ms.date: 2026-10-01
ms.topic: how-to
keywords:
  - dns
  - private-dns
  - osmo
---

Private DNS zone for OSMO UI hostname resolution. Maps the OSMO UI hostname to the internal load balancer in front of the OSMO gateway, for access through VPN.

> [!NOTE]
> Complete DNS configuration and resolution flow details are in the [Private DNS Configuration](../../../docs/infrastructure/dns.md) guide.

## 🚀 Quick Start

```bash
# Get the IP of the internal load balancer in front of OSMO
kubectl get svc azureml-ingress-nginx-internal-lb -n azureml -o jsonpath='{.status.loadBalancer.ingress[0].ip}'

# Deploy the DNS zone with that IP
cd infrastructure/terraform/dns
terraform init && terraform apply -var="osmo_loadbalancer_ip=<IP_FROM_ABOVE>"
```

## 📖 Documentation

| Guide                                                            | Description                                     |
|------------------------------------------------------------------|-------------------------------------------------|
| [Private DNS Configuration](../../../docs/infrastructure/dns.md) | DNS zone setup, resolution flow, and validation |
| [Terraform Reference](TERRAFORM.md)                              | Auto-generated inputs, outputs, and resources   |

## ➡️ Next Step

Proceed to [Cluster Setup](../../setup/).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
