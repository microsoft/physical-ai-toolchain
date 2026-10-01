# Viewer Deployment

Deployment contracts for the Dataset Analysis Tool on Kubernetes and Azure Container Apps.

## Architecture

| Component     | Technology                       | Port |
|---------------|----------------------------------|------|
| Backend       | FastAPI (Python)                 | 8000 |
| Frontend      | React/Vite (nginx in production) | 8080 |
| Reverse Proxy | nginx                            | 443  |

The frontend serves static assets and proxies `/api/` requests to the backend service.

## Container Images

| Image    | Base               | Build Context                      |
|----------|--------------------|------------------------------------|
| Backend  | Python 3.12        | `data-management/viewer/backend/`  |
| Frontend | nginx-unprivileged | `data-management/viewer/frontend/` |

Images are pushed to the Azure Container Registry provisioned by the infrastructure domain.

## Kubernetes Deployment

| Resource              | Purpose                                             |
|-----------------------|-----------------------------------------------------|
| Deployment (backend)  | FastAPI application pods                            |
| Deployment (frontend) | nginx pods serving React build                      |
| Service (backend)     | ClusterIP service for backend pods                  |
| Service (frontend)    | ClusterIP service for frontend pods                 |
| Ingress               | External access with TLS termination                |
| ConfigMap             | Runtime configuration (storage paths, CORS origins) |
| Secret                | Storage credentials, auth provider secrets          |

## Azure Container Apps Deployment

The Terraform module in `infrastructure/terraform/modules/dataviewer/` provisions:

| Resource                   | Purpose                                            |
|----------------------------|----------------------------------------------------|
| Container Apps Environment | Shared networking and logging                      |
| Container App (backend)    | FastAPI with managed identity for storage access   |
| Container App (frontend)   | nginx with backend service binding                 |
| Managed Identity           | Azure RBAC for Blob Storage and Container Registry |
| Entra ID App Registration  | OAuth authentication for the viewer                |

Terraform provisions the application registration and Container Apps resources. Run `data-management/setup/deploy-dataviewer.sh` to build the images, configure frontend Easy Auth, set the backend to independent Entra JWT validation, and perform the staged browser verification gates.

## Authentication

| Method              | Use Case                                                                  |
|---------------------|---------------------------------------------------------------------------|
| Easy Auth and Entra | Public frontend perimeter plus independent backend delegated-token checks |
| API key             | Service-to-service communication                                          |
| Auth0               | Alternative JWT identity provider                                         |
| Disabled            | Loopback-only local development                                           |

Authenticated Azure deployments use two controls. Container Apps Easy Auth protects the public frontend and starts the server-directed sign-in flow. The frontend then requests the `access_as_user` delegated scope through MSAL, and the backend independently validates the token signature, issuer, audience, expiry, subject, scope, and route-specific roles. The backend does not trust client-supplied platform principal headers.

## Configuration

Runtime behavior is controlled by environment variables on the backend container:

| Variable                     | Description                                                  |
|------------------------------|--------------------------------------------------------------|
| `STORAGE_TYPE`               | `local` or `azure`                                           |
| `AZURE_STORAGE_ACCOUNT`      | Storage account name (when `STORAGE_TYPE=azure`)             |
| `AZURE_STORAGE_CONTAINER`    | Blob container name                                          |
| `CORS_ORIGINS`               | Allowed frontend origins                                     |
| `DATAVIEWER_AUTH_DISABLED`   | `true` only for loopback local development                   |
| `DATAVIEWER_AUTH_PROVIDER`   | `azure_ad`, `auth0`, or `apikey`                             |
| `DATAVIEWER_AZURE_TENANT_ID` | Entra tenant used for issuer and signing-key validation      |
| `DATAVIEWER_AZURE_CLIENT_ID` | API application ID used for token audience validation        |
