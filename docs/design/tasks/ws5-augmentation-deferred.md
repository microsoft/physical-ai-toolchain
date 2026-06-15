# WS5 — A0–A1 Data Augmentation (Deferred Build Workstream)

> [!IMPORTANT]
> This workstream is **deferred**. It is documented here so it can be picked up as a subsequent build
> effort, but it is **not executed** as part of the tiered-architecture documentation rollout
> (WS0–WS4, WS6). It is the only net-new code item the proposal names; everything else in this plan is
> documentation and reframing.

The proposal calls for a tiered, optional data-augmentation axis (A0–A2) so that scarce-data users at
T0–T2 have a low and middle rung, not just the heavyweight Cosmos/SDG ceiling. Today
`synthetic-data/` ships 0 Python files and 2 placeholder specs, so A0–A1 are genuinely new code.

## Augmentation Axis

| Stage | Approach                                                                          | Where it runs                | Status today           |
|-------|-----------------------------------------------------------------------------------|------------------------------|------------------------|
| A0    | Classical CV augmentation (crops, jitter, blur, photometric/geometric transforms) | Local, CPU, no model         | not built              |
| A1    | Local small-VLM generation (vLLM locally at T0, or Azure AI Foundry at T1)        | Local GPU or hosted endpoint | not built              |
| A2    | Full Cosmos / SDG world-foundation-model pipeline                                 | Cloud, GPU cluster           | placeholder specs only |

A2 is out of scope here; this workstream targets the A0 and A1 rungs.

## Why It Is Separate

- It is a build item, not a re-framing of shipped artifacts, so it carries code, tests, and dependency
  risk the documentation workstreams do not.
- The proposal explicitly excludes augmentation from Goal: Full Training Lifecycle (the minimal floor argument), so it must
  not gate or expand the T0 on-ramp work in WS3/WS4.
- It can begin only after the augmentation axis (A0–A2) and its placement relative to the tiers are
  ratified — captured in the WS0 decision on Goal: Full Training Lifecycle scope.

## Suggested Future Sub-Tasks

When picked up, this workstream can itself be parallelized:

| Sub-task                              | Owned area                             | Notes                                                                                                   |
|---------------------------------------|----------------------------------------|---------------------------------------------------------------------------------------------------------|
| A0 classical augmentation module      | `synthetic-data/` (new Python package) | CPU-only transforms; follows repo Python conventions (`uv`, ruff, `from __future__ import annotations`) |
| A0 tests                              | `synthetic-data/**/tests`              | Behavior tests per repo testing patterns                                                                |
| A1 local-VLM generation module        | `synthetic-data/`                      | vLLM local + Azure AI Foundry endpoint backends                                                         |
| A1 tests + optional-dependency guards | `synthetic-data/**/tests`              | `try/except ImportError` pattern for heavy optional deps                                                |
| Augmentation guidance docs            | `docs/synthetic-data/**`, recipes      | "Recommended when data is scarce"; ties A0/A1 to tiers                                                  |
| Spec updates                          | `synthetic-data/specifications/**`     | Replace placeholders with A0–A1 designs                                                                 |

## Entry Criteria Before Starting

- WS0 Goal-G-scope decision recorded (augmentation remains a separate optional axis).
- `tier-model.md` published, so augmentation guidance can reference tiers correctly.
- A ratified dependency plan for vLLM / Azure AI Foundry backends.

## Out of Scope

- A2 Cosmos/SDG pipeline implementation.
- Folding augmentation into Goal: Full Training Lifecycle or the T0 on-ramp.
