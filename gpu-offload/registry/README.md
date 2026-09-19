# Host-local image registry

Every image the cluster pulls resolves on the host: images built in this repository are
served from a local hosting registry, and images from `docker.io`, `ghcr.io`, `nvcr.io`,
or an Azure Container Registry are fetched once and cached. Nothing crosses a slow link
twice.

## 🧭 Design

The registries run as podman containers on the host, not as Kubernetes workloads. A
registry deployed inside the cluster deadlocks on boot: kubelet needs the registry in
order to pull images, but the registry is itself a pod that must be pulled and scheduled
first. Running on the host also keeps the cache intact across cluster rebuilds.

| Kind    | Port      | Access     | Contents                                   |
|---------|-----------|------------|--------------------------------------------|
| Hosting | 5000      | Read-write | Images built here; push targets            |
| Cache   | 5001-5004 | Read-only  | One upstream each, populated on first pull |

`registry:2` proxies exactly one upstream per container, so each upstream gets its own
port and its own blob directory. Every endpoint binds `127.0.0.1`; k3s runs on the host,
so its containerd reaches the same loopback address the build tooling pushes to.

Two consumers are registered against these endpoints:

| Consumer | File                                                      | Privilege          |
|----------|-----------------------------------------------------------|--------------------|
| k3s      | `/etc/rancher/k3s/registries.yaml`                        | sudo, restarts k3s |
| podman   | `~/.config/containers/registries.conf.d/gpu-offload.conf` | none               |

podman is registered with a `mirror` rather than a location rewrite, so builds fall back
to the upstream when a cache is stopped.

## 🚀 Usage

```bash
cd gpu-offload
mise run b-host-20-registry          # start everything and register both runtimes
mise run b-host-21-registry-status   # what is running and what is cached
```

The k3s step needs sudo and restarts k3s. It is skipped automatically when the resolved
runtime is not k3s, and can be skipped explicitly:

```bash
registry/registry-up.sh --skip-k3s
```

Push an image built locally, then reference it as `localhost:5000/<repository>:<tag>`:

```bash
registry/registry-push.sh gpu-offload-ur10e-single:local
```

Stop the registries but keep every cached blob:

```bash
registry/registry-down.sh
```

> [!WARNING]
> `registry-down.sh --purge` deletes the blobs as well. The next deployment refetches
> every upstream image over the internet.

## ⚙️ Upstreams

[upstreams.conf](./upstreams.conf) declares one row per cached registry. Add a registry by
adding a row; no script changes are needed.

| Column         | Purpose                                                      |
|----------------|--------------------------------------------------------------|
| `host`         | Registry hostname as it appears in an image reference        |
| `port`         | Loopback port the cache listens on, unique per row           |
| `remote`       | Upstream URL the cache fetches from                          |
| `username_env` | Environment variable holding the username, `-` for anonymous |
| `password_env` | Environment variable holding the password, `-` for anonymous |

Values may reference environment variables. A row whose host still contains an empty
expansion is skipped, which is how the Azure Container Registry row ships in the file
without committing a registry name.

> [!IMPORTANT]
> Credentials are read from the environment at run time and are never written to the
> repository. Put them in `gpu-offload/.env`, which is gitignored. Never put a secret, an
> account name, or any other environment-specific value in `upstreams.conf`.

### Credentials

| Upstream                 | Variables                                                        | Notes                                     |
|--------------------------|------------------------------------------------------------------|-------------------------------------------|
| `docker.io`              | `GPU_OFFLOAD_DOCKERIO_USERNAME`, `GPU_OFFLOAD_DOCKERIO_PASSWORD` | Optional; raises the anonymous rate limit |
| `ghcr.io`                | `GPU_OFFLOAD_GHCR_USERNAME`, `GPU_OFFLOAD_GHCR_PASSWORD`         | Token needs `read:packages`               |
| `nvcr.io`                | `GPU_OFFLOAD_NVCR_USERNAME`, `GPU_OFFLOAD_NVCR_PASSWORD`         | Username is the literal `$oauthtoken`     |
| Azure Container Registry | `GPU_OFFLOAD_ACR_USERNAME`, `GPU_OFFLOAD_ACR_PASSWORD`           | Service principal id and secret           |

Anonymous access is enough for public images. `nvcr.io` and Azure Container Registry
require credentials for anything else, and the scripts warn when those caches start
without them.

### Azure Container Registry

Set the registry name and its credentials, then start the registries. The row in
`upstreams.conf` activates on its own:

```bash
export GPU_OFFLOAD_ACR_NAME=<registry-name>
export GPU_OFFLOAD_ACR_USERNAME=<service-principal-id>
export GPU_OFFLOAD_ACR_PASSWORD=<service-principal-secret>
mise run b-host-20-registry
```

`<registry-name>.azurecr.io/...` then resolves through `localhost:5004` for both k3s and
podman. To publish there instead of to the local hosting registry, point
`GPU_OFFLOAD_REGISTRY_HOST` at the ACR endpoint and log in with `az acr login`.

## 📦 Files

| Path                 | Content                                                     |
|----------------------|-------------------------------------------------------------|
| `upstreams.conf`     | Declarative table of cached registries                      |
| `registry-common.sh` | Endpoint layout, upstream parsing, rendered runtime configs |
| `registry-up.sh`     | Start the registries and register k3s and podman            |
| `registry-down.sh`   | Stop the registries, optionally purge blobs                 |
| `registry-status.sh` | Report container state, cache sizes, and registration       |
| `registry-push.sh`   | Tag and push a local image into the hosting registry        |

## 🔍 Troubleshooting

| Symptom                                            | Cause                                                                               |
|----------------------------------------------------|-------------------------------------------------------------------------------------|
| `ImagePullBackOff` on a `localhost:5000` reference | The k3s step never ran; `registry-status.sh` reports the registration state         |
| Pulls still slow after starting the caches         | k3s was not restarted, so containerd has not read `registries.yaml`                 |
| `401 Unauthorized` from a cache                    | The upstream needs credentials; set the variables named in `upstreams.conf`         |
| Cache answers but stores nothing                   | The image was already in the local podman or containerd store, so no fetch occurred |
| A row in `upstreams.conf` appears to be ignored    | Its host contains an unset environment variable, which skips the row by design      |
