# GitOps Sources

FluxCD source definitions for fleet deployment: `GitRepository` and `OCIRepository` resources that declare where Flux pulls manifests and container images from.

## Planned Resources

| Resource Type | Purpose                                     |
|---------------|---------------------------------------------|
| GitRepository | Reference to this repository for manifests  |
| OCIRepository | Container registry sources for model images |
