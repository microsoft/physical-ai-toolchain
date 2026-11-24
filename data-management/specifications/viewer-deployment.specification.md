---
title: Viewer Deployment
description: Deployment, caller authentication and recovery contracts for the Dataset Analysis Tool
ms.date: 2026-10-22
ms.topic: reference
---

Deployment contracts for the Dataset Analysis Tool on Kubernetes and Azure Container Apps.

## Architecture

| Component     | Technology                       | Port                |
|---------------|----------------------------------|---------------------|
| Backend       | FastAPI (Python)                 | 8000                |
| Frontend      | React/Vite (nginx in production) | 8080                |
| Reverse Proxy | nginx                            | 443                 |
| Caller helper | Existing backend Python runtime  | 8090, loopback only |

The frontend serves static assets and proxies `/api/` requests to the backend service.

## Container Images

| Image    | Base               | Build Context                                                             |
|----------|--------------------|---------------------------------------------------------------------------|
| Backend  | Python 3.13 slim   | Repository root, with `--file data-management/viewer/backend/Dockerfile`  |
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

| Resource                    | Purpose                                                  |
|-----------------------------|----------------------------------------------------------|
| Container Apps Environment  | Shared networking and logging                            |
| Container App (backend)     | FastAPI with managed identity for storage access         |
| Container App (frontend)    | nginx with backend service binding                       |
| Frontend auth configuration | Easy Auth with anonymous requests redirected             |
| Shared managed identity     | Azure RBAC for Blob Storage and Container Registry       |
| Frontend caller identity    | Exclusive app-token caller; no storage or registry roles |
| Entra ID App Registration   | OAuth authentication, when auth is enabled               |
| Legacy proxy secret objects | Inert migration remnants retained for old revisions      |

Terraform owns the application registration, identities, secrets and frontend Easy Auth configuration. The backend starts with the complete caller-verification tuple. The frontend starts in `pending` mode, which denies protected forwarding even when infrastructure health is ready. Real hosted frontend images include a `caller-helper` sidecar using the compatible backend image; placeholder and non-hosted templates do not.

The shared identity remains on both apps for registry access and backend storage. Only the frontend app receives the separate caller identity. Identity attachment is an app boundary, not a per-container credential boundary: every frontend container can use its attached identities.

## Authentication

| Method                                 | Use Case                                                                   |
|----------------------------------------|----------------------------------------------------------------------------|
| Entra ID JWT (`azure_ad`)              | Production deployments with organizational identity                        |
| Easy Auth (`easy_auth`)                | Container Apps deployments where `deploy-dataviewer.sh` configures sign-in |
| API key (`apikey`, the default)        | Service-to-service communication                                           |
| Auth0 (`auth0`)                        | Alternative identity provider                                              |
| None (`DATAVIEWER_AUTH_DISABLED=true`) | Local development and internal deployments without sign-in                 |

Authenticated Container Apps deployments use `easy_auth`. Easy Auth authenticates the browser and injects `X-MS-CLIENT-PRINCIPAL`. Nginx makes an internal, bodyless loopback request to the helper, without browser headers or cookies. It overwrites incoming caller proof with one raw JWT in `X-Dataviewer-Caller-Token`, then forwards the original user request over verified HTTPS with SNI. The helper never exposes a browser token endpoint. End-user `Authorization` remains separate.

The helper uses explicit `ManagedIdentityCredential` selection, not a default/developer credential chain. It requests `api://<api-client-id>/.default`. The backend requires an RS256 v2 token with the exact tenant issuer, API UUID audience and `tid`, `idtyp=app`, non-whitespace `oid`, `sub` and `azp`, finite numeric `exp`/`nbf`, and no delegated `scp`. It then checks the dedicated frontend object and client IDs. The shared storage identity is not authorized.

Missing, duplicate, invalid, expired or wrong-audience caller proof returns `401`. A cryptographically valid app token outside the caller ACL returns `403`. Caller proof alone never authenticates a user. After caller verification, the backend requires a bounded, unambiguous Easy Auth principal; user scope normalization and nonempty roles remain unchanged. MI authenticates the forwarding workload, not the asserted user independently, and does not isolate model or decoder workloads.

The backend Container App remains internal-only. This boundary preserves native image, frame, video, `HEAD`, and Range requests because the browser uses its Easy Auth cookie rather than attaching a separate API token to every media request.

### Routes and Input Limits

Every hosted backend HTTP request except exact GET/HEAD `/health` requires caller and user authentication before routing. This includes docs, CSRF bootstrap, OPTIONS, unknown paths and health aliases. Nginx only maps exact GET/HEAD `/health` to exempt backend health; other methods and prefix/encoded aliases cannot rewrite into that exemption.

| Input                  | Limit                                                              |
|------------------------|--------------------------------------------------------------------|
| Caller JWT             | 8192 bytes; one raw header                                         |
| Encoded user principal | 8192 bytes; one header                                             |
| Decoded user principal | 6144 bytes                                                         |
| Claims                 | 64                                                                 |
| Claim type / value     | 256 / 2048 UTF-8 bytes                                             |
| Subject source         | Exact WS-2005 nameidentifier URI or `sub`; repeated sources denied |

Malformed JSON/base64/Unicode, blank subjects and ambiguous subjects are denied, not coerced. No token or principal payload belongs in diagnostics.

### Readiness and Token Lifetime

| Signal                               | Meaning                                                     |
|--------------------------------------|-------------------------------------------------------------|
| Frontend `/` liveness                | Static nginx availability, independent of token acquisition |
| Frontend `/health`, off/pending      | Backend storage health; pending still denies API traffic    |
| Frontend `/health`, managed identity | Private helper readiness followed by backend storage health |
| Helper `/ready`                      | Usable cached/acquired token, without returning proof       |
| Signed-in operator gate              | Actual user API and native media acceptance                 |

The helper binds only to `127.0.0.1:8090`; platform probes use nginx on 8080, not the helper port. Acquisition has a five-second deadline, serialized refresh, a 300-second refresh-ahead window, a 60-second expiry safety margin and ten-second retry backoff.
An acquisition failure may use cached proof only before that safety margin. Unusable proof fails closed without failing static liveness. Bounded JWKS retrieval also denies transient verification failures. Identity/permission propagation and cached tokens prevent promises of instant activation or revocation.

## Configuration

Runtime behavior is controlled by environment variables on the backend container:

| Variable                             | Description                                                    |
|--------------------------------------|----------------------------------------------------------------|
| `STORAGE_BACKEND`                    | `local` (the image default) or `azure`                         |
| `AZURE_STORAGE_ACCOUNT_NAME`         | Storage account name when `STORAGE_BACKEND=azure`              |
| `AZURE_STORAGE_DATASET_CONTAINER`    | Blob container for datasets                                    |
| `AZURE_STORAGE_ANNOTATION_CONTAINER` | Blob container for annotations                                 |
| `CORS_ORIGINS`                       | Allowed frontend origins                                       |
| `DATAVIEWER_AUTH_PROVIDER`           | `apikey` (default), `azure_ad`, `auth0`, or `easy_auth`        |
| `DATAVIEWER_AUTH_DISABLED`           | `true` bypasses authentication                                 |
| `AZURE_CLIENT_ID`                    | Shared identity selector for backend storage, not caller proof |
| `DATAVIEWER_AZURE_TENANT_ID`         | Exact hosted tenant UUID                                       |
| `DATAVIEWER_AZURE_CLIENT_ID`         | API application UUID; also required by helper                  |
| `DATAVIEWER_CALLER_OBJECT_ID`        | Dedicated frontend identity principal UUID, backend only       |
| `DATAVIEWER_CALLER_CLIENT_ID`        | Dedicated frontend identity client UUID, backend and helper    |
| `DATAVIEWER_CALLER_MODE`             | Frontend only: `off`, `pending` or `managed_identity`          |

Backend rollouts through `deploy-dataviewer.sh` set `STORAGE_BACKEND=azure`. The viewer README has the [full environment variable reference](../viewer/README.md#full-environment-variable-reference).

The frontend mode is explicit. Unset defaults to `off`; empty/unknown values fail startup. `off` strips caller proof and preserves ordinary app authentication without acquiring tokens. `pending` denies protected forwarding. `managed_identity` requires private proof and never falls back. Host location, storage mode and `AZURE_CLIENT_ID` do not select it. A nonempty retired `DATAVIEWER_PROXY_KEY` fails frontend startup; remove it rather than configuring a fallback.

## Authenticated Rollout

Apply the Terraform contract before running the deployment script interactively:

```bash
bash data-management/setup/deploy-dataviewer.sh --config-preview
bash data-management/setup/deploy-dataviewer.sh --local-build --tag <reviewed-image-tag>
```

The nullable `dataviewer.caller_auth` output contains exactly `mode`, `tenant_id`, `api_client_id`, `caller_identity_id`, `caller_client_id` and `caller_object_id`. Deployment validates it against Easy Auth, identity attachments, the callback, the existing login secret and the service principal's assignment policy.
Assignment-required issuance needs an approved Application-role design; neither Terraform nor deployment disables that policy to obtain tokens. Existing User-only roles are not workload authorization.

Selected images must resolve to digests in the configured registry before app mutation. Complete named-container manifests reconcile images, entry points, arguments, environment, probes, resources and identity bindings despite Terraform's ignored templates. Credentials must use secret references, not inherited plaintext environment settings. Generated temporary manifests are private and removed after use; application-scoped secret objects are not copied or deleted.

1. Close frontend ingress, then configure and verify Easy Auth.
2. Install the backend's token-only revision and verify all four caller UUIDs with auth enabled.
3. Install and verify the pending frontend/helper revision after the backend is ready.
4. Verify frontend Easy Auth, reopen frontend ingress and check anonymous forged-principal denial before enabling managed-identity forwarding.
5. Install the active frontend/helper revision, recheck auth and denial, then complete the interactive browser/media gate within 15 minutes.

Each update must return a revision; that exact revision must become ready and match the desired named containers, commands, settings, resources, probes, mounts and identities. An earlier ready revision cannot mask failure. Initial shared-proof migration requires a controlled deny window; mixed versions are not promised uninterrupted traffic.

`--skip-build` deploys existing registry images. `--skip-update` builds only. Preview and skipping both apps perform no app mutation. Partial hosted updates require an already verified MI pair; frontend-only updates reuse its separately pinned compatible helper image and never build or deploy the skipped backend. Backend-only updates leave frontend configuration unchanged. Non-hosted updates remove selected apps' stale caller settings/helper while retaining the explicit storage identity.

## Recovery and Credentials

Recovery eligibility requires a ready, digest-pinned pair already enforcing the complete caller ACL and frontend Easy Auth. Legacy shared-proof/header-only snapshots are ineligible. Recovery restores the selected backend and pending frontend while preserving Easy Auth, verifies enforcement, then restores active MI forwarding. Both required safe restores are attempted even if one fails. Rejection, EOF or timeout at the operator gate uses the same recovery policy.

Without an eligible pair, or after any failed restore, disable and verify frontend ingress. A failed recovery returns failure even if closure succeeds. Never disable user authentication, relax caller validation or reintroduce a shared credential to recover availability.

The Easy Auth login secret remains Terraform-owned and required. Old `dataviewer-proxy-key` password/secret objects are inert migration remnants, not runtime configuration or supported fallback. Retain them while older revisions reference them; decommission them separately after that window. Do not rotate them as the MI proof mechanism.
[Container Apps secret changes](https://learn.microsoft.com/azure/container-apps/manage-secrets) do not create revisions or update already-loaded values. Never print secret values, tokens or live principal payloads.

## Compatibility and Acceptance

| Runtime                            | Application authentication                                                | Caller helper                    |
|------------------------------------|---------------------------------------------------------------------------|----------------------------------|
| Local launcher/development         | Existing local, API-key, direct Entra JWT or Auth0 configuration          | None                             |
| Local-storage Compose              | Existing non-hosted application mode                                      | None; frontend defaults off      |
| Compose on an Azure host with Blob | Existing non-hosted application mode; storage credentials remain separate | None                             |
| Hosted ACA Easy Auth               | Platform browser cookie plus verified workload proof and user principal   | Required; frontend-only identity |

Deployments must satisfy the following acceptance requirements:

| Area                  | Requirement                                                                                        |
|-----------------------|----------------------------------------------------------------------------------------------------|
| Runtime compatibility | Local and Compose modes remain independent of hosted caller authentication, including Blob access  |
| Hosted authentication | Require valid caller proof and a complete user principal; retain internal-only backend ingress     |
| Browser and media     | Preserve sign-in, user scopes and native media, including HEAD and Range requests                  |
| Persistence           | Preserve CSRF protection and revision-conditional writes with the configured storage backend       |
| Token lifecycle       | Refresh proof before its safety cutoff and deny protected forwarding when usable proof is absent   |
| Rollout and recovery  | Verify the selected revisions and restore an eligible authenticated pair or close frontend ingress |
