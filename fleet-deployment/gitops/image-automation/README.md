# Image Automation

FluxCD image automation CRDs that detect new model container images and update deployment manifests automatically.

## Planned Resources

| Resource Type         | Purpose                                           |
|-----------------------|---------------------------------------------------|
| ImageRepository       | Scan container registries for new tags            |
| ImagePolicy           | Define version selection rules for model images   |
| ImageUpdateAutomation | Commit manifest updates when new images are found |
