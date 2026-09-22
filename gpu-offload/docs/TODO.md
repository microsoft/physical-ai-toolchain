---
title: GPU Offload Work Items
description: Track pending GPU-offload implementation work
ms.date: 2026-08-28
ms.topic: reference
---

<!-- cspell:ignore simplelog -->

Track implementation work that has been identified but not completed. Remove each task after its code changes and validation are merged.

## Mise Task Layout

Target files: [`mise.toml`](../mise.toml), [`scripts`](../scripts/), [`examples`](../examples/)

The tasks have been extracted into scripts and reordered to match the execution path. One
deviation from [`mise-tasks.instructions.md`](../../.github/instructions/mise-tasks.instructions.md)
remains: group prefixes are hyphen-separated (`d-offload-50-deploy`) rather than
colon-separated, so related tasks do not match a single `mise run` pattern.

### Mise Task Implementation

* [x] Move every embedded `run` body out of `mise.toml` into a script file, leaving each task as an invocation of that script.
* [x] Place platform-wide scripts in `gpu-offload/scripts` and example-specific scripts in `gpu-offload/examples`, prefixed with the example name.
* [ ] Regroup task names with colon-separated prefixes so related tasks match a single pattern.
* [x] Start every task name with a letter, never a digit or symbol.
* [x] Order the groups so the alphanumeric sort matches the human path: setup, deploy, verify, develop, then teardown.
* [x] Update the READMEs and walkthroughs that reference the old task names.

### Mise Task Acceptance Criteria

* [x] `mise.toml` contains no multi-line shell bodies.
* [x] `mise tasks` lists the groups in the order a contributor executes them.
* [x] Each script is independently executable and passes `shellcheck`.
* [ ] Every documented command matches a task that exists.

### Mise Task Validation

* [x] Run `shellcheck` over the extracted scripts.
* [ ] Run the full first-run path through the renamed tasks.

## Local Podman and kind Hardening

The CPU-only and WSL2 NVIDIA first-run paths completed successfully with rootless Podman 4.9.3, kind 0.30.0, Kubernetes 1.35.0, and an RTX 3060. The validated WSL2 path requires local runtime configuration that is not suitable for production clusters.

### Observed Issues

* kind can retain an older image behind a mutable `:local` tag. Loading a new archive does not reliably replace the existing containerd image without removing the tag first.
* Generated server Deployments are asynchronous. Calling `kubectl rollout status` immediately after Helm returns can fail with `NotFound`; the walkthrough waits for resource creation first.
* The one-shot client can invoke its remote function before the generated server becomes discoverable. Restarting the client after the server rollout succeeds avoids the race.
* Changing `remote.yaml` from a CPU stage to an NVIDIA stage can leave the obsolete generated CPU Deployment until reconciliation removes it or an operator deletes it.
* NVIDIA's standard device plugin fails WSL2 GPU-PV discovery through nested local Kubernetes because NVML initialization returns `Not Supported`.
* The validated WSL2 workaround uses a privileged generic device plugin to register `/dev/dxg` as `nvidia.com/gpu`.
* The validated kind runtime adds `/usr/lib/wsl` to the base OCI specification, making the read-only driver tree visible to every pod on that local node.
* The TCP messenger previously entered the global heartbeat list before initializing its socket. Depending on thread timing, the heartbeat could close the new connection and fail the remote call.
* The synchronized first-run chart briefly contained both `.yaml` and `.yaml.tpl` copies of the same template. Helm rendered duplicate resources, and whitespace trimming joined the YAML language-server directive to `apiVersion` in one copy.

### Local Runtime Implementation

* [ ] Add an automated Podman and kind smoke harness for CPU-only and WSL2 NVIDIA paths.
* [ ] Make the smoke harness wait for each generated Deployment to exist before checking rollout status.
* [ ] Use immutable image references or make the harness remove mutable tags before loading replacements.
* [ ] Gate the first client invocation on server-stage discovery instead of requiring a client restart.
* [ ] Reconcile generated Deployments immediately when server stages are removed or renamed.
* [ ] Replace the privileged generic WSL2 device plugin with a CDI-aware allocation path when kind and containerd support the WSL GPU device end to end.
* [ ] Scope WSL driver-library injection to GPU-allocated containers instead of the node-wide OCI base specification.
* [x] Initialize TCP transport state before registering the messenger for heartbeat transmission.
* [x] Keep one `.yaml.tpl` first-run template so Helm renders each resource once without raw YAML editor diagnostics.

### Local Runtime Acceptance Criteria

* A single command creates the Podman-backed kind cluster, loads fresh images, and runs the selected path.
* The first client pod returns a remote result without a manual restart.
* Stage changes remove obsolete generated Deployments.
* Only GPU-allocated pods receive `/dev/dxg` and WSL driver libraries.
* [x] Successful calls do not emit heartbeat attribute errors.

### Local Runtime Validation

* [x] CPU server returned `{"executed_by":"first-run-client-remote-server-cpu-...","predictions":[1,4,9,16]}`.
* [x] Kubernetes advertised `nvidia.com/gpu: 1` on the WSL2 kind node.
* [x] A disposable GPU pod reported NVIDIA GeForce RTX 3060 through `/dev/dxg`.
* [x] The generated NVIDIA server requested one GPU while the client requested none.
* [x] The generated NVIDIA server reported NVIDIA GeForce RTX 3060, driver 595.95, and 12288 MiB.
* [x] NVIDIA server returned `{"executed_by":"first-run-client-remote-server-nvidia-...","predictions":[1,4,9,16]}`.
* [x] Fresh CPU and NVIDIA client runs completed without heartbeat attribute, bad file descriptor, or missing-result errors.
* [x] Synchronized commit `fbb50d27` passed runtime tests and fresh CPU and NVIDIA cluster runs after rebuilding and replacing the kind image.
* [x] The first-run chart passed Helm lint, single-resource render checks, and Kubernetes server-side dry-run validation.

## Bound Log Rollover Failure Handling

Target file: [`runtime/remoter/simplelog.py`](../runtime/remoter/simplelog.py)

The logger invokes `lsof` during process startup to avoid rotating a log file that another process has open. A missing `lsof` executable now returns `False`, but other subprocess and filesystem failures can still block startup indefinitely.

### Failure Modes

* `subprocess.run()` has no timeout. A stalled `lsof` process stalls logger and application initialization.
* `rollover()` catches every exception and retries with a new filename in an unbounded loop.
* Persistent filesystem errors, including a read-only directory, insufficient permissions, exhausted storage, or rename failures, cause infinite retries.
* An execution error other than `FileNotFoundError`, such as `PermissionError`, enters the same unbounded retry loop.
* The current implementation provides no final error identifying why rollover could not complete.

### Implementation

* [ ] Add a short timeout to the `lsof` invocation.
* [ ] Treat `FileNotFoundError`, `subprocess.TimeoutExpired`, and other expected `OSError` failures as an unavailable `lsof` check.
* [ ] Preserve argument-list invocation and continue to avoid `shell=True`.
* [ ] Replace the unbounded rollover loop with a finite number of filename attempts.
* [ ] Raise a clear exception containing the original failure after all attempts are exhausted.
* [ ] Preserve the existing log retention behavior when rollover succeeds.
* [ ] Remove the duplicate `os` import while editing the module.

### Acceptance Criteria

* Application startup completes when `lsof` is not installed.
* Application startup does not wait indefinitely when `lsof` stalls.
* A persistent filesystem failure exits rollover after a bounded number of attempts.
* The final exception identifies the target log path and preserves the underlying error as its cause.
* Existing log files continue to rotate according to the `keep` value.
* Paths are passed as subprocess arguments without shell interpolation.

### Validation

* [ ] Add focused tests that replace `subprocess.run()` with missing-command, timeout, open-file, and closed-file outcomes.
* [ ] Add focused tests for successful rollover and persistent filesystem failure.
* [ ] Verify the persistent-failure test completes within a fixed test timeout.
* [ ] Run the GPU-offload runtime test suite.
* [ ] Start the first-run client in the slim container image, where `lsof` is absent, and verify remote execution reaches the server pod.
