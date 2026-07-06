# GitOps Specification

FluxCD GitOps architecture for the fleet-delivery control plane (T4 — Scale): source management,
per-site cluster reconciliation, and image automation that delivers validated policies onto robots.
"Fleet" refers to robots; the Kubernetes layer reconciled here is described as "clusters" and "sites."
See [tier-model.md](../../docs/design/tier-model.md) for canonical tier and vocabulary definitions.

## Status

Planned: placeholder for future implementation.

## Components

| Component             | Description                                      |
|-----------------------|--------------------------------------------------|
| GitRepository source  | Git-based manifest source for FluxCD             |
| OCIRepository source  | OCI artifact source for container images         |
| Kustomization         | Reconciliation target for raw manifests          |
| HelmRelease           | Reconciliation target for Helm charts            |
| ImagePolicy           | Version selection rules for automated updates    |
| ImageUpdateAutomation | Commit automation for manifest image tag updates |

## Reconciliation Flow

```text
Git Commit → FluxCD Source Controller → Kustomize/Helm Controller → Cluster State
```

## Cluster Overlays

Per-cluster customization via Kustomize overlays in `gitops/clusters/`. Each overlay patches base manifests with cluster-specific values (resource limits, node selectors, image pull secrets).
