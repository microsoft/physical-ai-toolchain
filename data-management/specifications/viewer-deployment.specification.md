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

* The backend Dockerfile's `BACKEND_EXTRAS` build argument lists the Python extras to install, separated by commas. Its default, `azure,analysis,export,auth,yolo`, includes `yolo`, whose `ultralytics` dependency pulls CUDA-enabled PyTorch and makes the image about 10 GB compressed.
* The Container Apps Consumption profile supports images up to 8 GB per replica, so the script builds with `DATAVIEWER_BACKEND_EXTRAS` (default `azure,analysis,export,auth`). Object detection then returns HTTP 503 on Container Apps.
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

The frontend resolves its assets and API paths against the document URL, so it runs at the root or under a path prefix such as a `kubectl proxy` service URL.

The local OSMO profile implements this contract in `infrastructure/local/manifests/dataviewer.yaml` without an Ingress or Secret. The backend runs with `DATAVIEWER_AUTH_DISABLED=true` as the owner of the host datasets directory, which it mounts read-write, and images come from the workstation's loopback registry. An optional Azure Blob mode adds `STORAGE_BACKEND=azure`, the account and container values, and a workload identity ServiceAccount, and keeps authentication off.

## Azure Container Apps Deployment

The Terraform module in `infrastructure/terraform/modules/dataviewer/` provisions:

| Resource                    | Purpose                                            |
|-----------------------------|----------------------------------------------------|
| Container Apps Environment  | Shared networking and logging                      |
| Container App (backend)     | FastAPI with managed identity for storage access   |
| Container App (frontend)    | nginx with backend service binding                 |
| Frontend auth configuration | Easy Auth with anonymous requests redirected       |
| Managed Identity            | Azure RBAC for Blob Storage and Container Registry |
| Entra ID App Registration   | OAuth authentication, when auth is enabled         |
| Random password             | Shared frontend-to-backend proxy credential        |

Terraform owns the initial application registration, Container Apps secrets, frontend Easy Auth configuration, and `easy_auth` backend provider selection. The public frontend bootstrap revision does not receive the proxy credential, so it cannot convert an unauthenticated forged principal into trusted backend proof. The deployment script adds the frontend secret reference only after verifying that Easy Auth rejects anonymous traffic.

## Authentication

| Method                                 | Use Case                                                                   |
|----------------------------------------|----------------------------------------------------------------------------|
| Entra ID JWT (`azure_ad`)              | Production deployments with organizational identity                        |
| Easy Auth (`easy_auth`)                | Container Apps deployments where `deploy-dataviewer.sh` configures sign-in |
| API key (`apikey`, the default)        | Service-to-service communication                                           |
| Auth0 (`auth0`)                        | Alternative identity provider                                              |
| None (`DATAVIEWER_AUTH_DISABLED=true`) | Local development and internal deployments without sign-in                 |

Authenticated Container Apps deployments use `easy_auth`. Easy Auth authenticates the browser at the public frontend and injects `X-MS-CLIENT-PRINCIPAL`. Nginx overwrites `X-Dataviewer-Proxy-Key` with the shared Container Apps secret before forwarding the request. The backend verifies that credential using a constant-time comparison before decoding the principal. A direct caller with a forged principal header but no valid proxy credential receives HTTP 401.

The backend Container App remains internal-only. This boundary preserves native image, frame, video, `HEAD`, and Range requests because the browser uses its Easy Auth cookie rather than attaching a separate API token to every media request.

## Configuration

Runtime behavior is controlled by environment variables on the backend container:

| Variable                             | Description                                                             |
|--------------------------------------|-------------------------------------------------------------------------|
| `STORAGE_BACKEND`                    | `local` (the image default) or `azure`                                  |
| `AZURE_STORAGE_ACCOUNT_NAME`         | Storage account name when `STORAGE_BACKEND=azure`                       |
| `AZURE_STORAGE_DATASET_CONTAINER`    | Blob container for datasets                                             |
| `AZURE_STORAGE_ANNOTATION_CONTAINER` | Blob container for annotations                                          |
| `CORS_ORIGINS`                       | Allowed frontend origins                                                |
| `DATAVIEWER_AUTH_PROVIDER`           | `apikey` (default), `azure_ad`, `auth0`, or `easy_auth`                 |
| `DATAVIEWER_AUTH_DISABLED`           | `true` bypasses authentication                                          |
| `DATAVIEWER_PROXY_KEY`               | Secret reference required by frontend nginx and the `easy_auth` backend |

Backend rollouts through `deploy-dataviewer.sh` set `STORAGE_BACKEND=azure`. The viewer README has the [full environment variable reference](../viewer/README.md#full-environment-variable-reference).

## Authenticated Rollout

The deployment script verifies that both Container Apps contain the Terraform-owned `dataviewer-proxy-key` secret and that the frontend contains the Terraform-owned Easy Auth client credential before mutation. It then captures the existing images, backend provider settings, proxy-key references, and Easy Auth state and classifies whether the snapshot is a secure recovery pair.

The rollout order is backend, Easy Auth verification, then frontend proof injection. Each update must return a revision, that exact revision must become ready, and its image and secret references must match the intended target. A forged-principal request without a session must return a redirect or denial before and after proof injection. One terminal browser gate verifies `/api/auth/context`, images, frames, videos, `HEAD`, and HTTP Range behavior before success is reported.

If the paired rollout fails, automatic recovery restores only a captured pair that already enforced Easy Auth, backend proof validation, and matching proxy-secret references. It restores and verifies both selected revisions while Easy Auth remains enabled. If the snapshot is unsafe or any restore step fails, the script disables frontend ingress and verifies the deny state.

Terraform retains ownership of the Entra credential, so recovery does not create, replace, print, or delete credential values. Recovery output names resources and secret references but never prints secret values or principal payloads.

`--skip-backend` is valid only when the existing backend uses `easy_auth` and references `dataviewer-proxy-key`. `--skip-frontend` is valid only when frontend Easy Auth is enabled and the existing frontend revision references that secret.

## Credential Rotation

[Container Apps secrets are application-scoped](https://learn.microsoft.com/azure/container-apps/manage-secrets). Updating a secret does not create a revision or change the value loaded by an existing revision. Rotate the proxy credential through a reviewed maintenance operation:

1. Run a Terraform plan that replaces `module.dataviewer[0].random_password.dataviewer_proxy_key[0]`. Confirm the plan changes the generated password and both Container App secret values without changing either active template.
2. Apply the reviewed plan. Do not restart either app until both application-scoped secrets contain the new value; the running revisions continue using the previous loaded value.
3. Disable frontend ingress and verify that it is closed.
4. Restart the active backend and frontend revisions so both load the new value. Keep ingress closed if either restart or readiness check fails.
5. Verify the backend and frontend revision identities, Easy Auth enforcement, the anonymous forged-principal denial, and direct-backend missing/incorrect-proof rejection.
6. Re-enable frontend ingress and run the authenticated media and annotation smoke tests.

Do not expose the credential through Terraform outputs, source files, shell tracing, deployment logs, or recovery commands.
