---
title: Operator Workspace
description: Configure and operate simulated or physical SO-101 sessions through the Dataset Analysis Tool
author: Microsoft
ms.date: 2026-09-24
ms.topic: how-to
---

Use the operator workspace to run bounded simulation, teleoperation, recording, or policy sessions. Simulation is the default validation mode. Physical SO-101 access is an explicit local opt-in that remains blocked until authentication, configuration, profile, calibration, and preflight checks pass.

## Prerequisites

| Mode | Requirements |
|------|--------------|
| Disabled | No operator dependencies; analysis and review remain available |
| Simulated | Backend and frontend dependencies from the viewer setup |
| Physical | Linux host, local dataset storage, one backend worker, SO-101 devices, accessible emergency stop, reviewed calibration files, and authenticated Operator access |

Keep physical device identity, calibration locations, policy locations, and host lease files outside the repository. Do not commit serials, device paths, endpoints, credentials, or generated deployment values.

## Simulation

Set the adapter mode in `backend/.env`:

```env
OPERATOR_ADAPTER_MODE=simulated
```

Start the viewer and open the Operator workspace. Simulation does not require a hardware profile and does not prove physical device identity, calibration, torque-off behavior, camera access, or motion safety.

## Physical Configuration

Install all projects from their committed public-source locks:

```bash
cd data-management/viewer
uv sync --frozen --project backend
uv sync --frozen --project operator-worker --extra dev
cd frontend
npm ci
```

Do not run dependency-resolution commands in a restricted proxy environment. Generate changed locks from canonical public registries, then validate them with frozen restore commands.

Create an environment-local profile from `backend/src/api/operator/profile_data/so101.example.toml`. Store the populated file outside the repository and set only absolute local paths. The profile requires distinct leader and follower identities, exact USB identity, both saved calibration files, wrist and front camera identity, and bounded recording defaults.

Configure the backend with environment-local values:

```env
STORAGE_BACKEND=local
DATA_DIR=<absolute-dataset-root>
WEB_CONCURRENCY=1
DATAVIEWER_AUTH_DISABLED=false
DATAVIEWER_OPERATOR_PROFILE_PATHS=<absolute-profile-path>
OPERATOR_ADAPTER_MODE=lerobot
OPERATOR_HOST_LEASE_PATH=<absolute-host-lease-path>
OPERATOR_WORKER_EXECUTABLE=<absolute-operator-worker-executable>
```

Physical access also requires one authorization policy:

| Provider | Requirement |
|----------|-------------|
| API key | Set `DATAVIEWER_OPERATOR_API_KEY` to a secret distinct from `DATAVIEWER_API_KEY`; trusted clients send it as `X-Operator-API-Key` |
| Azure AD or Auth0 | Assign the exact `Operator` application role |
| Azure Easy Auth | Assign the exact `Operator` role and set `OPERATOR_TRUST_EASY_AUTH=true` only after validating the trusted proxy boundary |

Physical mutations always require the viewer's double-submit CSRF token. `OPERATOR_CSRF_DISABLED=true` is rejected in physical mode. Authentication-disabled operation is also rejected.

Policy mode remains unavailable unless the trusted backend environment defines `OPERATOR_POLICY_PYTHON` and `OPERATOR_POLICY_CHECKPOINT`. Set `OPERATOR_POLICY_CUDA_VISIBLE_DEVICES` only when the policy runtime requires an explicit device selection. These values never come from browser settings.

## Calibration and Preflight

Calibration inspection validates the saved JSON files without opening or moving either arm. A valid report proves file readability, bounded size, unique JSON keys, and the expected six SO-101 joints. It does not prove that the file matches the connected arm or that the arm can move safely.

Before physical operation:

1. Clear the workspace and make the emergency stop reachable.
2. Review the local profile and calibration file locations.
3. Confirm both arms and cameras match the expected USB identity.
4. Run preflight from the operator workspace.
5. Start only after every blocking check passes.

Preflight evidence is time-bound and tied to the profile and detected resources. A changed or stale identity requires a new preflight.

## Operating Boundaries

The backend owns authorization, preflight evidence, session state, command idempotency, and trusted paths. The isolated worker owns hardware devices. Do not start another backend worker or an external process that opens the same arms or cameras.

Stop remains the primary action during an active or uncertain session. Stop immediately after unexpected motion, stale or mismatched identity, camera or device failure, worker communication failure, or any uncertainty about torque state.

Recording destinations are derived beneath `DATA_DIR`; browser settings cannot supply a filesystem destination. Completed recordings refresh dataset discovery and then use the existing annotation, review, release, revision, and read-only workflow.

## Recovery

`cleanup-unconfirmed` means the system could not prove cleanup completion and torque-off. Treat it as a hard stop:

1. Do not start another session.
2. Use the physical emergency stop or disconnect power according to the site procedure.
3. Confirm both arms are de-energized under direct supervision.
4. Stop the viewer backend and any remaining operator worker process.
5. Resolve device ownership, profile, or worker errors before restarting the backend.
6. Run a fresh preflight before any later physical session.

Do not delete a host lease file to override an active or uncertain owner. Reopening devices is part of supervised hardware validation, not automated recovery proof.

## Validation Boundaries

Automated tests and browser checks prove API contracts, bounded state, fail-closed transitions, simulation behavior, layout, and recording handoff. They do not prove physical SO-101 readiness.

Physical readiness requires a qualified operator to record supervised evidence for preflight, saved calibration, short teleoperation and stop, torque-off confirmation, device reopening, recording playback, and any policy rollout. Mark policy validation not run when the reviewed policy runtime is unavailable.

Run the reproducible software gate from `data-management/viewer`:

```bash
npm run validate
```

This command validates the backend, release worker, operator worker, and frontend against their committed dependency locks.

## Related Documentation

* [Dataset Analysis Tool setup](../README.md)
* [SO-101 operator profile template](../backend/src/api/operator/profile_data/so101.example.toml)
