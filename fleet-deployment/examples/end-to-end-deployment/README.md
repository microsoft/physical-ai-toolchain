# End-to-End Deployment Example

Walkthrough of deploying a trained policy from the model registry to a single edge robot using FluxCD GitOps.

## Status

Planned — placeholder for future implementation.

## Steps

| Step | Description                                      |
|------|--------------------------------------------------|
| 1    | Publish model image to container registry        |
| 2    | Configure FluxCD source and image automation     |
| 3    | Define deployment gating criteria                |
| 4    | Bootstrap FluxCD on the target cluster           |
| 5    | Verify inference pod is running on the edge node |
