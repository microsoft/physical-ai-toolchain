# GitOps Specification

FluxCD GitOps architecture for fleet deployment: source management, reconciliation, and image automation.

## Status

Planned — placeholder for future implementation.

## Components

| Component              | Description                                          |
|------------------------|------------------------------------------------------|
| GitRepository source   | Git-based manifest source for FluxCD                 |
| OCIRepository source   | OCI artifact source for container images             |
| Kustomization          | Reconciliation target for raw manifests              |
| HelmRelease            | Reconciliation target for Helm charts                |
| ImagePolicy            | Version selection rules for automated updates        |
| ImageUpdateAutomation  | Commit automation for manifest image tag updates     |

## Reconciliation Flow

```text
Git Commit → FluxCD Source Controller → Kustomize/Helm Controller → Cluster State
```

## Cluster Overlays

Per-cluster customization via Kustomize overlays in `gitops/clusters/`. Each overlay patches base manifests with cluster-specific values (resource limits, node selectors, image pull secrets).
