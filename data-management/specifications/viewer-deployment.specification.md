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

| Image    | Base               | Build Context                                                             |
|----------|--------------------|---------------------------------------------------------------------------|
| Backend  | Python 3.13 slim   | `data-management/viewer/backend/`                                         |
| Frontend | nginx-unprivileged | Repository root, with `--file data-management/viewer/frontend/Dockerfile` |

Images are pushed to the Azure Container Registry provisioned by the infrastructure domain. `data-management/setup/deploy-dataviewer.sh` builds them and rolls them out to Container Apps.

* The backend Dockerfile's `BACKEND_EXTRAS` build argument selects the Python extras to install. Its default, `azure analysis export auth yolo`, includes `yolo`, whose `ultralytics` dependency pulls CUDA-enabled PyTorch and makes the image about 10 GB compressed. The Container Apps Consumption profile supports images up to 8 GB per replica, so the script builds with `DATAVIEWER_BACKEND_EXTRAS` (default `azure analysis export auth`). Object detection then returns HTTP 503 on Container Apps.
* The frontend Dockerfile uses BuildKit features (`RUN --mount`, `HEALTHCHECK --start-interval`) that ACR quick builds reject. Build it with the script's `--local-build` option, which builds with local Docker BuildKit and pushes to the registry.

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
| Entra ID App Registration  | OAuth authentication, when auth is enabled         |

## Authentication

| Method                                 | Use Case                                                                   |
|----------------------------------------|----------------------------------------------------------------------------|
| Entra ID JWT (`azure_ad`)              | Production deployments with organizational identity                        |
| Easy Auth (`easy_auth`)                | Container Apps deployments where `deploy-dataviewer.sh` configures sign-in |
| API key (`apikey`, the default)        | Service-to-service communication                                           |
| Auth0 (`auth0`)                        | Alternative identity provider                                              |
| None (`DATAVIEWER_AUTH_DISABLED=true`) | Local development and internal deployments without sign-in                 |

## Configuration

Runtime behavior is controlled by environment variables on the backend container:

| Variable                             | Description                                             |
|--------------------------------------|---------------------------------------------------------|
| `STORAGE_BACKEND`                    | `local` (the image default) or `azure`                  |
| `AZURE_STORAGE_ACCOUNT_NAME`         | Storage account name (when `STORAGE_BACKEND=azure`)     |
| `AZURE_STORAGE_DATASET_CONTAINER`    | Blob container for datasets                             |
| `AZURE_STORAGE_ANNOTATION_CONTAINER` | Blob container for annotations                          |
| `CORS_ORIGINS`                       | Allowed frontend origins                                |
| `DATAVIEWER_AUTH_PROVIDER`           | `apikey` (default), `azure_ad`, `auth0`, or `easy_auth` |
| `DATAVIEWER_AUTH_DISABLED`           | `true` bypasses authentication                          |

Backend rollouts through `deploy-dataviewer.sh` set `STORAGE_BACKEND=azure`. The viewer README has the [full environment variable reference](../viewer/README.md#full-environment-variable-reference).
