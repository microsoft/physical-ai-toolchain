---
name: e2e-tests
description: "Autonomously launch, monitor, retry, and report all Physical AI cloud E2E tests across independent Azure ML and OSMO GPU infrastructure."
---

<!-- cspell:ignore amlcompute azureml chdir finalizers finetune nodepool pytest -->

# Cloud E2E Tests

Run detached cloud E2E test attempts through the repository-owned launcher. The launcher uses pytest collection to discover every test module marked `e2e`. It then exits and hands Copilot every command, PID, log, result, and resubmission handle needed to babysit the runs.

Resolve the launcher from the active checkout:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
DRIVER="$REPO_ROOT/.github/skills/e2e-tests/scripts/run-e2e-tests.sh"
```

## Operating Contract

Execute the workflow autonomously. Do not ask the user to run commands, monitor jobs, classify failures, or request routine retries.

1. Read Terraform outputs before any Azure, Kubernetes, OSMO, or pytest operation.
2. Stop if `terraform output -json` is unavailable, empty, stale against live resource IDs, or missing a required output. Do not infer deployed resource names from conventions, the current Kubernetes context, Azure resource enumeration, or remembered environments.
3. Validate Azure authentication, storage, and Azure ML compute before submitting tests. Validate OSMO authentication, the gateway, and AKS GPU pools when the selected subset includes OSMO tests.
4. Launch the requested test files concurrently, or every discovered default test when no subset is requested. Azure ML and OSMO use independent compute and do not contend with each other.
5. Capture `E2E_HANDLE` and take ownership of monitoring. The launcher never retries or diagnoses a completed attempt.
6. For every non-pass, inspect the exact command and complete output. Resubmit classified transient failures individually. Investigate other failures, correct clear in-scope defects, validate locally, then resubmit only the affected test.
7. Continue until the handle reports `PASSED`. Never alter Terraform or node-pool configuration to work around unavailable GPU capacity.
8. Report each test's commands, attempts, cloud job or workflow identifiers, durations, and final state. Run handle cleanup after all tests reach terminal states.

Each handle owns an `XDG_CONFIG_HOME` under its run directory. OSMO reads and rewrites `login.yaml` in that directory, so every launch, detached attempt, resubmission, and diagnostic command must source the handle's `config.env`. Never use the global `~/.config/osmo/login.yaml` while an E2E handle is active; concurrent deployments would redirect each other's CLI commands.

## Local Dependency Bootstrap

Synchronize the frozen local environment before launching attempts. Use the package index configured for the current development environment:

```bash
cd "$REPO_ROOT"
uv sync --frozen --no-progress
```

Do not treat a direct `files.pythonhosted.org` connection failure as a cloud-test failure. Bootstrap through the configured index, then resubmit the affected terminal attempts.

## Hugging Face Prerequisite

`tests/e2e/test_e2e_aml_vla_pi0_training.py` requires `HF_TOKEN` with access to the gated `google/paligemma-3b-pt-224` model. Export the token before a default full run or before selecting that test explicitly. The requirement does not apply when an explicit subset omits tests marked `requires_hf_token`.

Never write the token to `config.env`, `command.sh`, logs, manifests, or chat output. Preserve it only in the process environment. A missing or empty token is a non-retryable client-side setup failure: the test must fail before Azure fixture resolution, dataset staging, or job submission.

## Required Terraform Outputs

Run this first:

```bash
terraform -chdir=infrastructure/terraform output -json
```

When the current feature worktree has no Terraform state or returns empty outputs, locate the `main` worktree with `git worktree list --porcelain` and use its `infrastructure/terraform` directory as the authoritative deployment source. Keep the feature worktree as the launcher repository root so pytest collects and runs the branch code:

```bash
"$DRIVER" \
  --repo-root <feature-worktree> \
  --terraform-dir <main-worktree>/infrastructure/terraform
```

Apply the same output and live-resource validation rules to the main worktree state. Stop if those outputs are unavailable, empty, stale, or incomplete.

Require these non-null outputs:

| Output              | Required fields                            | Use                                                               |
|---------------------|--------------------------------------------|-------------------------------------------------------------------|
| `resource_group`    | `value.name`, `value.id`, `value.location` | Azure scope                                                       |
| `aks_cluster`       | `value.name`, `value.id`                   | OSMO Kubernetes target                                            |
| `azureml_workspace` | `value.name`, `value.id`                   | Azure ML job target                                               |
| `storage_account`   | `value.name`, `value.id`                   | Synthetic IL and VLA staging                                      |
| `node_pools`        | Non-empty `value` map                      | Expected OSMO GPU pools, VM sizes, priorities, labels, and taints |

The committed `node_pools` output does not expose autoscaling bounds. Read live bounds with `az aks nodepool list` only after matching the AKS resource ID to Terraform.

## Compute Validation

Validate the two independent backends before launching:

| Backend     | Required checks                                                                                                                                                                                                                                                                                                                                         |
|-------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| OSMO on AKS | Every Terraform GPU pool exists with the expected VM size; each pool has a registered node or `enableAutoScaling=true` with `maxCount>0`; the launcher logs in through its localhost gateway port-forward, selects the default pool, verifies a generic `huggingface` credential exists, and `osmo workflow list --count 1 --format-type json` succeeds |
| Azure ML    | `gpu-cluster` is `amlcompute`, provisioning is `Succeeded`, and `max_instances>0`; zero current nodes is valid scale-from-zero                                                                                                                                                                                                                          |

Scale-from-zero pools may have no registered GPU nodes. Do not interpret zero current nodes as a configuration failure when autoscaling can add capacity.

## Launch

Run from the repository root:

```bash
"$DRIVER"
```

Select a subset by repeating `--test`:

```bash
"$DRIVER" \
  --test test_e2e_aml_il_pipeline \
  --test test_e2e_aml_il_lifecycle \
  --test test_e2e_osmo_il_lifecycle
```

The command exits after printing:

```text
E2E_HANDLE=<absolute-run-directory>
E2E_STATUS_COMMAND=<absolute-script-path> --status <absolute-run-directory>
E2E_RESUBMIT_COMMAND=<absolute-script-path> --resubmit <absolute-run-directory> <test-name>
E2E_CLEANUP_COMMAND=<absolute-script-path> --cleanup <absolute-run-directory>
```

Save `E2E_HANDLE`. Each test owns one detached process. No coordinator or retry loop remains active after launch.

## Handoff Data

The handle contains all data needed to investigate and reproduce an attempt:

| Path                          | Content                                                                               |
|-------------------------------|---------------------------------------------------------------------------------------|
| `config.env`                  | Shell-escaped Terraform-resolved environment used by every attempt                    |
| `manifest.tsv`                | Handle creation metadata                                                              |
| `tests.txt`                   | Ordered tests selected for this handle                                                |
| `terraform-output.json`       | Terraform outputs validated before launch                                             |
| `live-node-pools.json`        | AKS pool state validated before launch                                                |
| `azureml-compute.json`        | Independent Azure ML compute state validated before launch                            |
| `xdg-config/`                 | Handle-local OSMO login and profile state                                             |
| `commands.tsv`                | Test, attempt, PID, exact command-file path, and output-log path                      |
| `<test>/status`               | Latest test state                                                                     |
| `<test>/latest-attempt`       | Latest attempt number                                                                 |
| `<test>/attempt-N/command.sh` | Exact executable command, including exports, watchdog, pytest flags, and output paths |
| `<test>/attempt-N/output.log` | Complete stdout and stderr                                                            |
| `<test>/attempt-N/pytest.xml` | Isolated pytest result                                                                |
| `<test>/attempt-N/exit-code`  | Process exit code                                                                     |
| `<test>/attempt-N/status`     | Attempt classification                                                                |

Use `command.sh` as the authoritative record. Do not reconstruct commands from chat history or partial logs.

## Tracking

Read aggregate and per-test state without attaching to the coordinator:

```bash
"$DRIVER" --status "$E2E_HANDLE"
```

Inspect a test's current attempt:

```bash
tail -n 100 "$E2E_HANDLE/<test-name>/attempt-<n>/output.log"
```

Use five-minute status intervals while jobs are provisioning or running. Use twenty-minute intervals when all pending jobs are blocked on documented GPU capacity. Do not poll continuously.

Resubmit one terminal attempt:

```bash
"$DRIVER" \
  --resubmit "$E2E_HANDLE" <test-name>
```

This creates `attempt-N+1` with a new exact `command.sh`; it never overwrites earlier evidence.

After every test is terminal:

```bash
"$DRIVER" --cleanup "$E2E_HANDLE"
```

## Test Discovery

The launcher runs `pytest --collect-only` over `tests/e2e` and derives the available E2E modules from every test marked `e2e`. The default selection includes the complete collected set, including tests with additional prerequisite markers such as `requires_hf_token`.

## Retry Classification

The launcher labels these conditions transient but does not resubmit them:

- Watchdog exit code `124`
- `SkuNotAvailable`
- `node-disruption`
- OSMO gateway HTTP `503` or `504`
- `upstream request timeout`
- `upstream connect error`
- `Timed out waiting for .* to start`
- `Timed out waiting for .* to complete`
- `did not .* complete within`
- `to start within`

Copilot resubmits every transient terminal attempt with `--resubmit` and continues until it passes. Each resubmission creates a new workflow or job. OSMO's `_await_osmo_status_with_restarts` already handles task-level spot disruptions inside one invocation; do not add another disruption restart mechanism.

Each E2E handle exports a private `XDG_CONFIG_HOME` and selects a free handle-local `OSMO_GATEWAY_PORT` before OSMO login. Preserve that profile and port for every attempt and resubmission so concurrent sessions targeting different deployments cannot rewrite each other's endpoint or contend for the same localhost listener. Every OSMO resubmission re-establishes the handle-local gateway login and selects the default pool inside that isolated profile.

After concurrent OSMO submissions return gateway 503/504 responses, serialize resubmissions. Wait for the active OSMO workflow to reach a terminal state before submitting the next test.

## Failure Rules

- A pytest skip is `FAILED_SETUP_SKIP`, never a pass.
- A failure outside the retry classification is `FAILED` and requires investigation.
- Never resubmit a test while its state is `STARTING` or `RUNNING`.
- Do not delete staged data manually; pytest finalizers own cleanup.
- Do not change GPU SKUs, regions, autoscaling limits, Terraform state, or node pools to make a test pass.
- Preserve every attempt log and XML result under the handle.
- If `osmo-service` returns repeated gateway 503/504 responses, inspect its container `lastState` before retrying. An `OOMKilled` termination at the 1 GiB limit requires increasing the service memory allocation and redeploying OSMO.
- Treat Azure CLI token-refresh TLS EOF and `RemoteDisconnected` failures during Blob staging or cleanup as transient. Refresh the storage token, then retry the test.
- An Azure ML parent pipeline in `Running` can still have a queued child; inspect child jobs before diagnosing progress.
- An OSMO pod in `Pending` is expected while AKS scales from zero. Confirm autoscaler configuration before attributing it to capacity.
