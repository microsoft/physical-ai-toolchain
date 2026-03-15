# Gating Manifests

Kubernetes manifests for deploying the gating service to edge clusters.

## Planned Resources

| Resource     | Purpose                                    |
|--------------|--------------------------------------------|
| Deployment   | Gating service workload                    |
| Service      | Internal cluster endpoint for gate checks  |
| ConfigMap    | Gate criteria and threshold configuration  |
