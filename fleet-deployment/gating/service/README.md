# Gating Service

Deployment gating service that validates trained models before they are rolled out to production robot fleets.

## Planned Components

| Component       | Purpose                                                |
|-----------------|--------------------------------------------------------|
| Gate evaluator  | Run pre-deployment safety and performance checks       |
| Approval API    | Programmatic approval/rejection of deployment gates    |
| Webhook handler | Receive FluxCD notifications and trigger gate checks   |
