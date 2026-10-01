---
title: OSMO Upgrade from Pre-6.3 Releases
description: Staged upgrade of a pre-6.3 OSMO control plane to OSMO 6.3 in ConfigMap mode, with backups and rollback
author: Microsoft Robotics-AI Team
ms.date: 2026-09-30
ms.topic: how-to
keywords:
  - osmo
  - upgrade
  - configmap
  - rollback
---

<!-- cspell:ignore pgroll -->

Upgrade an OSMO install that predates 6.3 to this repository's OSMO 6.3 deployment with `infrastructure/setup/optional/upgrade-osmo.sh`. The script handles 6.0-era builds, 6.1, and 6.2 installs that run separate `service`, `router`, and `ui` Helm releases with database config. `03-deploy-osmo.sh` stops on those installs instead of installing over them.

## Choose a Path

| Path      | Stages                                                            | Use when                                              |
|-----------|-------------------------------------------------------------------|-------------------------------------------------------|
| Keep data | `backup` → `hop-6.2` → `tokens` → `export` → `hop-6.3` → `verify` | You need existing workflow history, pools, and config |
| Fresh     | `backup` → `reset` → `hop-6.3` → `verify`                         | You don't need the OSMO database or Redis data        |

Both paths end on OSMO 6.3 in ConfigMap mode, where pools, platforms, pod templates, and other configs come from Helm values instead of the database. Neither path deletes the storage container or the `mek-config` ConfigMap that holds the master encryption key (MEK).

## Prerequisites

| Requirement         | Details                                                                                                                                                                                                                                                                                                               |
|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Environment window  | The control plane is unavailable during `hop-6.2` and for several minutes of `hop-6.3`. Announce the downtime.                                                                                                                                                                                                        |
| Cluster access      | Network access to the AKS API and the OSMO internal ingress, plus the Azure roles in [Cluster Setup](cluster-setup.md#azure-rbac-permissions)                                                                                                                                                                         |
| OSMO authentication | Disabled, as `03-deploy-osmo.sh` deploys it. The script calls the OSMO API as `admin` through the no-auth `x-osmo-user` header. For installs with authentication, follow NVIDIA's guides.                                                                                                                             |
| OSMO CLI            | 6.2.10 or later for `tokens`, and 6.3.0 once the upgrade completes. See [Install the OSMO CLI](#install-the-osmo-cli).                                                                                                                                                                                                |
| uv                  | `export`, `hop-6.3`, and `verify` parse YAML with a uv-managed Python                                                                                                                                                                                                                                                 |
| Key Vault secrets   | `osmo-admin-password`, `psql-admin-password`, and `redis-primary-key`, readable from this host. `03-deploy-osmo.sh` mounts `osmo-admin-password`, which Terraform creates when `osmo_config.should_create_secret` is `true`, the default. `reset` and `hop-6.3` check the secrets they need before removing anything. |
| Backup directory    | An absolute path outside the repository and environment bundles. The script creates it with mode 700. It holds the MEK, the operator token, and the database dump.                                                                                                                                                    |
| Environment bundle  | `infrastructure/setup/generated/<environment>/` from the environment-deployment skill. `export` writes `osmo-platforms.yaml` there, and `hop-6.3` reads it.                                                                                                                                                           |

Each run executes one stage, checks that the previous stage succeeded, and records the outcome in `<backup-dir>/upgrade-state.json`. The stages that change the control plane (`hop-6.2`, `reset`, and `hop-6.3`) print a plan and wait for you to type the AKS cluster name. A stage that stops before changing anything leaves no record, so you can fix the cause and run it again. Add `--config-preview` to any stage to check its inputs.

## Keep OSMO Data

Back up, move to 6.2, recreate the backend operator token, and export the database configs:

```bash
cd infrastructure/setup
backup_dir="$HOME/osmo-upgrade/<environment>"
bundle_dir="generated/<environment>"

optional/upgrade-osmo.sh --stage backup --backup-dir "$backup_dir"
optional/upgrade-osmo.sh --stage hop-6.2 --backup-dir "$backup_dir"
optional/upgrade-osmo.sh --stage tokens --backup-dir "$backup_dir" --token-expiry <yyyy-mm-dd>
optional/upgrade-osmo.sh --stage export --backup-dir "$backup_dir" --bundle-dir "$bundle_dir"
```

| Stage     | What it does                                                                                                                                                                                                                                                                                                                                                                                                        |
|-----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `backup`  | Dumps the OSMO database with `pg_dump` from a temporary pod that reads `db-secret`, and checks that the dump holds table data. Saves `mek-config`, the values and manifests of every OSMO Helm release, the operator token Secret, and every config type.                                                                                                                                                           |
| `hop-6.2` | Upgrades the legacy releases to chart 1.2.1 with `nvcr.io/nvidia/osmo` images tagged `6.2`. The service release's pre-upgrade hook runs NVIDIA's pgroll database migrations. Before it changes anything, the stage checks the database for the columns, tables, and indexes those migrations add and drop, and afterwards it confirms they applied. The envoy, oauth2Proxy, rateLimit, and authz sidecars stay off. |
| `tokens`  | Replaces the service tokens that the 6.2 migrations delete. Creates the `backend-operator` user and an `osmo-backend` token, stores it in the operator's token Secret, restarts the operator, and waits for the backend to come online.                                                                                                                                                                             |
| `export`  | Runs NVIDIA's `export_configs_to_helm.py` from the pinned commit after checking its SHA-256. Confirms that every backed-up pool, platform, pod template, and backend is in the export, then writes `<bundle>/osmo-platforms.yaml` if that file is absent.                                                                                                                                                           |

NVIDIA's migrations expect a 6.0 release schema. A pre-release build can differ, and pgroll then skips a whole migration while the Helm hook still succeeds. `hop-6.2` stops before upgrading such a database; use [Start Fresh](#start-fresh) for those installs.

Review `<bundle>/osmo-platforms.yaml` before you continue:

- The values replace the database config. Add or remove pools, platforms, and pod templates to match the node pools you run.
- Create a Kubernetes Secret in `osmo-control-plane` for each secret reference that `export` reports, or remove the settings that use it.
- The pinned exporter copies secrets that the API masks, such as storage `access_key`, `backend_images.credential.auth`, and alert tokens, as literal asterisks. ConfigMap mode would load them as real values. Remove those settings or replace them with secret references; with workload identity, storage credentials need only `endpoint`. `export` lists them, and `hop-6.3` refuses values that still contain them.
- The 6.3 chart's default pool sets `common_pod_template`. Check that the `default` pool keeps the templates you intend.
- Helm merges these values over `infrastructure/setup/values/osmo-control-plane.yaml`: maps merge, and lists replace. That file's `default_user` template mounts `/dev/shm` sized by `{{USER_SHM_SIZE}}`, so give every pool a `USER_SHM_SIZE` default. If the export defines `default_user` containers, add the `dshm` volume mount to them.

Take a 6.2 restore point, then install 6.3 and verify it:

```bash
optional/upgrade-osmo.sh --stage backup --backup-dir "$backup_dir"
optional/upgrade-osmo.sh --stage hop-6.3 --backup-dir "$backup_dir" --bundle-dir "$bundle_dir"
optional/upgrade-osmo.sh --stage verify --backup-dir "$backup_dir"
```

## Start Fresh

```bash
cd infrastructure/setup
optional/upgrade-osmo.sh --stage backup --backup-dir "$backup_dir"
optional/upgrade-osmo.sh --stage reset --backup-dir "$backup_dir"
# Generate $bundle_dir/osmo-platforms.yaml from Terraform node_pools with the environment-deployment skill
optional/upgrade-osmo.sh --stage hop-6.3 --backup-dir "$backup_dir" --bundle-dir "$bundle_dir"
optional/upgrade-osmo.sh --stage verify --backup-dir "$backup_dir"
```

`reset` uninstalls the legacy releases, then runs `cleanup/uninstall-osmo.sh --skip-backend --skip-k8s-cleanup --purge-postgres --purge-redis`. That drops the database's `public` schema and pgroll's migration state, and flushes the Redis `{osmo}:*` keys, so OSMO workflow records and config history are gone. The storage container and its data, `mek-config`, the database and Redis secrets, the namespaces, and the backend operator stay.

## Install 6.3 and Verify

`hop-6.3` parses the values file, previews `03-deploy-osmo.sh`, and lists the releases it will uninstall. After you confirm, it uninstalls them with `--keep-history` and runs `03-deploy-osmo.sh --platform-values <file>`, which installs the `osmo` release and upgrades `osmo-operator`.

Pass other `03-deploy-osmo.sh` options after `--`, for example `-- --use-acr --image-manifest <file>`. The script rejects `--force-mek` and `--mek-config-file` because `03-deploy-osmo.sh` must keep the existing MEK to read encrypted database values.

`verify` checks that:

- OSMO reports the pinned version, 6.3.0
- every pool, platform, pod template, and backend in the values file is configured
- the AKS backend is online
- a config write returns HTTP 409, which confirms ConfigMap mode

Then submit a test workflow to each pool.

## Work in ConfigMap Mode

- `osmo config update` and other config writes return HTTP 409.
- To change pools, platforms, or pod templates, edit the values file and rerun `03-deploy-osmo.sh --platform-values <file>`.
- To return to database config, set `services.configs.enabled: false`. The database keeps whatever was last written to it.

## Renew HiL Backend Tokens

`tokens` lists the backends other than the AKS backend. Each needs a new token: rerun `04-prepare-osmo-hil-node.sh` with `--renew-token` from the environment-operator host, then redeploy the backend on the HiL host. See [Ubuntu HiL OSMO Backend](../recipes/tier-3-production/ubuntu-hil-osmo-backend.md).

## Install the OSMO CLI

Each [OSMO release](https://github.com/NVIDIA/OSMO/releases) publishes versioned client installers at `https://github.com/NVIDIA/OSMO/releases/download/<version>/`:

| Platform              | Asset                                             | Install                                |
|-----------------------|---------------------------------------------------|----------------------------------------|
| macOS (Apple silicon) | `osmo-client-installer-<version>-macos-arm64.pkg` | `sudo installer -pkg <file> -target /` |
| Linux x86_64          | `osmo-client-installer-<version>-linux-x86_64.sh` | `chmod +x <file> && ./<file>`          |
| Linux arm64           | `osmo-client-installer-<version>-linux-arm64.sh`  | `chmod +x <file> && ./<file>`          |

The devcontainer installs the 6.3.0 client from the same assets. Check the result with `osmo version`.

## Roll Back

Roll back to the latest backup taken before the failed stage. Each `<backup-dir>/backup-<timestamp>/` directory holds:

| File                           | Contents                                                     |
|--------------------------------|--------------------------------------------------------------|
| `osmo-db.dump`                 | Custom-format `pg_dump` of the OSMO database                 |
| `db-connection.txt`            | Database connection string, without the password             |
| `db-helper-pod.json`           | Pod manifest with a PostgreSQL client that reads `db-secret` |
| `helm/releases-*.json`         | Release names, charts, and revisions                         |
| `helm/<namespace>.<release>.*` | Values and rendered manifests                                |
| `k8s/mek-config.json`          | MEK ConfigMap                                                |
| `k8s/<token-secret>.json`      | Backend operator token Secret                                |
| `configs/*.json`               | OSMO config snapshots                                        |

Use the isolated kubeconfig for every command, and don't delete `mek-config`:

1. Point kubectl and Helm at the cluster, and restore the MEK if it's missing:

   ```bash
   export KUBECONFIG="$HOME/.kube/physical-ai-toolchain/<cluster>.yaml"
   backup="$backup_dir/backup-<timestamp>"
   kubectl get configmap mek-config -n osmo-control-plane || kubectl apply -f "$backup/k8s/mek-config.json"
   ```

2. Stop the upgraded control plane and keep the legacy release history:

   ```bash
   # After hop-6.2
   helm uninstall service router ui -n osmo-control-plane --keep-history --wait
   # After hop-6.3
   helm uninstall osmo -n osmo-control-plane --wait
   ```

3. Restore the database. The purge removes tables and the pgroll schema that the migrations added:

   ```bash
   cd infrastructure/setup
   cleanup/uninstall-osmo.sh --skip-backend --skip-k8s-cleanup --purge-postgres --purge-redis
   kubectl apply -f "$backup/db-helper-pod.json"
   kubectl wait pod/osmo-upgrade-db-restore -n osmo-control-plane --for=condition=Ready
   kubectl exec -n osmo-control-plane osmo-upgrade-db-restore -- \
     psql "$(cat "$backup/db-connection.txt")" -c 'DROP SCHEMA IF EXISTS pgroll CASCADE'
   kubectl exec -i -n osmo-control-plane osmo-upgrade-db-restore -- \
     pg_restore --clean --if-exists --no-owner --no-privileges \
     --dbname "$(cat "$backup/db-connection.txt")" < "$backup/osmo-db.dump"
   kubectl delete pod osmo-upgrade-db-restore -n osmo-control-plane
   ```

4. Roll the legacy releases back to their backed-up revisions. This restores the original chart versions, values, and image tag:

   ```bash
   jq -r '.[] | "\(.name) \(.revision)"' "$backup/helm/releases-control-plane.json"
   helm rollback <release> <revision> -n osmo-control-plane --wait
   ```

   If Helm can't roll a release back, apply its saved manifest with `kubectl apply -f "$backup/helm/osmo-control-plane.<release>.manifest.yaml"`.

5. Restore the backend operator. Roll the release back only after `hop-6.3`, which upgrades it:

   ```bash
   kubectl apply -f "$backup/k8s/<token-secret>.json"
   helm rollback osmo-operator <revision> -n osmo-operator --wait
   kubectl rollout restart deployment -n osmo-operator
   ```

## Clean Up

After `verify` passes and you no longer need a rollback, delete the kept release history with `helm uninstall service router ui -n osmo-control-plane`. Delete the backup directory when your retention policy allows, because it holds the MEK, the operator token, and the database dump.

## Related Documentation

- NVIDIA's upgrade guides at the pinned commit: [6.0 to 6.2](https://github.com/NVIDIA/OSMO/blob/07b71409e5535dabae7c897c04a86954d276f946/deployments/upgrades/6_0_to_6_2_upgrade.md) and [6.2 to 6.3](https://github.com/NVIDIA/OSMO/blob/07b71409e5535dabae7c897c04a86954d276f946/deployments/upgrades/6_2_to_6_3_upgrade.md)
- [Cluster Setup](cluster-setup.md) for deployment scenarios and the supported OSMO version
- [Cleanup and Destroy](cleanup.md) for `uninstall-osmo.sh` options
