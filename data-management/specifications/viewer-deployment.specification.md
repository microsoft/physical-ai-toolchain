# Viewer Deployment

Deployment contracts for the Dataset Analysis Tool on Kubernetes and Azure Container Apps.

## Architecture

| Component     | Technology                       | Port |
|---------------|----------------------------------|------|
| Backend       | FastAPI (Python)                 | 8000 |
| Frontend      | React/Vite (nginx in production) | 80   |
| Reverse Proxy | nginx                            | 443  |

The frontend serves static assets and proxies `/api/` requests to the backend service.

## Container Images

| Image    | Base         | Build Context                      |
|----------|--------------|------------------------------------|
| Backend  | Python 3.12  | `data-management/viewer/backend/`  |
| Frontend | nginx:alpine | `data-management/viewer/frontend/` |

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

## Authentication

| Method         | Use Case                                            |
|----------------|-----------------------------------------------------|
| Entra ID (JWT) | Production deployments with organizational identity |
| API Key        | Service-to-service communication                    |
| Auth0          | Alternative identity provider                       |
| None           | Local development (default)                         |

## Configuration

Runtime behavior is controlled by environment variables on the backend container:

| Variable                  | Description                                                  |
|---------------------------|--------------------------------------------------------------|
| `STORAGE_TYPE`            | `local` or `azure`                                           |
| `AZURE_STORAGE_ACCOUNT`   | Storage account name (when `STORAGE_TYPE=azure`)             |
| `AZURE_STORAGE_CONTAINER` | Blob container name                                          |
| `CORS_ORIGINS`            | Allowed frontend origins                                     |
| `AUTH_PROVIDER`           | Authentication provider (`entra`, `auth0`, `apikey`, `none`) |
