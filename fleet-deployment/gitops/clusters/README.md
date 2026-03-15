# Cluster Overlays

Per-cluster Kustomize overlays that customize fleet deployment manifests for individual edge clusters or cluster groups.

## Structure

Each cluster directory contains environment-specific patches:

```text
clusters/
├── cluster-a/           # Overlays for cluster A
├── cluster-b/           # Overlays for cluster B
└── ...
```
