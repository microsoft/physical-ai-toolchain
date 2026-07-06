# Tier Model — Canonical Reference

- **Status:** Canonical (single source of truth)
- **Operationalizes:** [Tiered Architecture Proposal](tiered-architecture-proposal.md)

This file is the canonical glossary for the tiered architecture (T0–T5). Tier IDs, stage names,
boundaries, the autonomy ladder, the fleet vocabulary rules, and the cross-document link/anchor
contract are defined here once and consumed everywhere else.

> [!IMPORTANT]
> Treat this file as **read-only canonical truth**. Downstream documentation (architecture, roadmap,
> getting-started, recipes) must cite the tier IDs, names, vocabulary, and anchors defined here rather
> than redefine them. If a tier boundary or name needs to change, change it here first.

## Naming Convention

Each tier carries a stable ID (`T0`–`T5`) and a stage name. The two are paired everywhere:

- **`T#` IDs are canonical.** Use them for every boundary reference, graduation note, and
  cross-document anchor. They never change, so links stay deterministic.
- **Stage names are user-facing labels.** Use them in prose and headings for readability.

The pairing is always written `T# — Name` (for example, `T0 — Dev`).

## Canonical Tier Table

T0 is the documented **default** starting path. T2 is the **recommended production** path. T3–T5 are
**advanced**. Infra details are drawn from Section 5 of the proposal.

| T# | Stage name | Operator reach / scope                   | Edge infra              | Cloud infra                                          | One-line purpose                                                             |
|----|------------|------------------------------------------|-------------------------|------------------------------------------------------|------------------------------------------------------------------------------|
| T0 | Dev        | Laptop + 1 robot (default)               | ROS 2 + Docker only     | None                                                 | The honest zero-cloud, zero-Kubernetes floor for the full training lifecycle.                       |
| T1 | Lab        | One site, a few robots, shared GPU       | Shared disk (NFS/SMB)   | One Blob storage account (optional AzureML / MLflow) | Add the first cloud resource, storage, for a small lab or integrator.        |
| T2 | Pilot      | One site, at scale, team (recommended)   | None beyond Docker      | AzureML + storage + model registry + MLflow          | Cloud training, registry, and shared catalogs become the default.            |
| T3 | Production | Single site, declarative deployment      | Local k3s + FluxCD      | Same as T2 (no Arc)                                  | GitOps deployment automation at one site, proving Arc is not required.       |
| T4 | Scale      | Multiple sites you cannot directly reach | Arc + AKS/Flux + gating | T2 + cross-site connectivity / identity              | Multi-site **fleet delivery** terminus; Arc as reachability/identity broker. |
| T5 | Operate    | Fleet-wide cognition (roadmap)           | + Azure IoT Operations  | + Fabric Real-Time Intelligence + drift/retraining   | **Fleet intelligence**: roadmap capability for drift detection and retraining. |

**Full training lifecycle** (the anchor goal): capture demonstrations on a robot, train an imitation policy, validate
it, and run that policy back on the robot, the full loop for one task. The full training lifecycle is fully achievable at
T0-T2 with manual deployment and no Kubernetes, Arc, or fleet infrastructure. Data augmentation is a
separate optional axis (A0-A2), not a step in the full training lifecycle.

### Boundaries

- **Multi-site boundary (Arc):** falls between T3 and T4. Arc becomes necessary only when robots span
  multiple sites you cannot reach from a single operator network.
- **Intelligence boundary:** falls between T4 and T5. T4 delivers and gates policies; it does not run
  drift detection, retraining, or aggregate analytics. Those are T5.

> [!NOTE]
> **Roadmap honesty.** T5 (Operate / fleet intelligence) is on the roadmap and not yet available.
> The fleet-intelligence domain is currently specified, with implementation planned. Label this
> status explicitly in both contributor and user-facing docs.

## The Autonomy Ladder (T5.0–T5.3)

Autonomy is a **different axis** from T0–T4. T0–T4 scale on *infrastructure reach* (sites, GPU,
collaboration); T5.0–T5.3 scale on *decision authority / loop closure*. They are orthogonal: a
single-site T3 operator can sit at T5.0, and a multi-site T4 operator can remain fully manual. The
autonomy stages are how much of the retraining decision a human delegates, not more infrastructure to
buy.

| Rung | Decision authority                                                                     | Human role                            | Status       |
|------|----------------------------------------------------------------------------------------|---------------------------------------|--------------|
| T5.0 | Gated retraining: the system surfaces signals only; humans trigger retraining.          | Human triggers every retraining cycle | Not built    |
| T5.1 | Human-in-the-loop / active learning: the system proposes what to retrain on and when.   | Human approves each cycle             | Ad-hoc (Hex) |
| T5.2 | Continual learning: the system retrains on a schedule or trigger.                       | Human reviews before deployment       | Not built    |
| T5.3 | Autonomous closed-loop: the system detects drift, retrains, gates, and deploys.         | None (fully autonomous)               | Not built    |

> [!WARNING]
> Fully autonomous retraining on production data is a foot-gun: a legitimate distribution change can
> cause the loop to bake current degraded behavior into the next dataset, and drift detection needs
> statistical power that only exists at fleet scale. T5 should default to human-supervised
> (T5.0–T5.1), not closed-loop (T5.3). T5.3 stays a roadmap direction, not a near-term target.

## Vocabulary Rules

These rules are mandatory across all documentation.

- **"Fleet" means a fleet of robots only.** It never refers to Kubernetes clusters, nor to Azure
  Kubernetes Fleet Manager (a distinct Azure product for managing a fleet of *clusters*, not robots).
  Cluster-level concerns are always written as "clusters" or "sites," never "fleets."
- **Fleet delivery (T4)** is the delivery and connectivity control plane: getting a validated policy
  onto robots across sites you cannot directly reach, with a safety gate before a policy swaps on a
  physical arm. This is the *necessary* multi-site concern.
- **Fleet intelligence (T5)** is the cognition layer: drift detection, automated retraining triggers,
  and aggregate telemetry analytics. This is the *roadmap/placeholder* concern.
- **Banned phrase: "fleet management".** Do not use it. The Kubernetes ecosystem (Azure Kubernetes
  Fleet Manager, FluxCD, Argo, Rancher Fleet) uses "fleet" for the cluster *delivery/placement*
  concern, so the bare phrase collides with Azure Kubernetes Fleet Manager and re-welds the two
  concerns this model deliberately separates. Always qualify as **fleet delivery** or **fleet
  intelligence** instead.

## Link / Anchor Contract

This section defines the canonical file paths and exact heading anchors that downstream
documentation uses when linking across the tier model, architecture, roadmap, and recipe entry
points.

### Anchor Convention

Anchors follow GitHub / markdownlint (MD051) rules, applied to the heading **text** (the `T# — Name`
pairing, not the surrounding prose):

1. Lowercase the text.
2. Drop all punctuation that is not a hyphen — this includes the em-dash (`—`) and any trailing
   period. The em-dash is removed, but the spaces on either side of it remain (step 3).
3. Replace each remaining space with a hyphen.

So `T0 — Dev` → lowercased `t0 — dev` → em-dash dropped leaves `t0  dev` (two spaces) → spaces to
hyphens → `t0--dev`. The double hyphen comes from the two spaces that flanked the dropped em-dash.

> [!IMPORTANT]
> Headings in `architecture.md` may carry descriptive trailing text (for example,
> `### T0 — Dev. One robot, one laptop`). The anchor is computed from the **full heading text**, so a
> heading with trailing prose produces a longer anchor. To keep anchors deterministic and short, the
> linkable tier sections use the exact headings published below: `### T# — Name`, with any
> descriptive sentence moved into the body text beneath the heading.

### Architecture Tier Anchors

File: `docs/contributing/architecture.md`. The architecture document exposes one `###` heading per
tier, worded exactly as the "Heading" column, yielding the "Anchor" column. Link as
`docs/contributing/architecture.md#<anchor>`.

| T# | Heading (verbatim)    | Anchor            |
|----|-----------------------|-------------------|
| T0 | `### T0 — Dev`        | `#t0--dev`        |
| T1 | `### T1 — Lab`        | `#t1--lab`        |
| T2 | `### T2 — Pilot`      | `#t2--pilot`      |
| T3 | `### T3 — Production` | `#t3--production` |
| T4 | `### T4 — Scale`      | `#t4--scale`      |
| T5 | `### T5 — Operate`    | `#t5--operate`    |

### Roadmap Anchors

File: `docs/contributing/ROADMAP.md`. The roadmap document exposes the autonomy-ladder section with
the heading below. Link as `docs/contributing/ROADMAP.md#<anchor>`.

| Section         | Heading (verbatim)                   | Anchor                        |
|-----------------|--------------------------------------|-------------------------------|
| Autonomy ladder | `## The Autonomy Ladder (T5.0–T5.3)` | `#the-autonomy-ladder-t50t53` |

> [!NOTE]
> In the autonomy-ladder anchor the parentheses and dots are dropped and the em-dash inside `T5.0–T5.3`
> is removed, collapsing it to `t50t53`. Use the anchor string verbatim from the table.

### Recipe Filenames / Paths

Directory: `docs/recipes/`. The naming scheme is one directory per tier, named
`tier-<n>-<slug>/`, each containing a `README.md` entry point. The slug is the lowercased stage name.
Entry pages and cross-links use these exact paths.

| T# | Recipe path                                | Status                    |
|----|--------------------------------------------|---------------------------|
| T0 | `docs/recipes/tier-0-dev/README.md`        | Default tier              |
| T1 | `docs/recipes/tier-1-lab/README.md`        | Available                 |
| T2 | `docs/recipes/tier-2-pilot/README.md`      | Recommended               |
| T3 | `docs/recipes/tier-3-production/README.md` | Advanced                  |
| T4 | `docs/recipes/tier-4-scale/README.md`      | Advanced                  |
| T5 | `docs/recipes/tier-5-operate/README.md`    | Roadmap                   |

> [!NOTE]
> The existing topic-based recipe folders (`docs/recipes/training/`,
> `docs/recipes/data-collection/`) are owned by their current authors and are unchanged by this work.
> The per-tier folders above are new and additive.

### Getting-Started Entry Anchors

Files: `README.md` (repo root), `docs/README.md`, `docs/getting-started/`. These entry points expose
the headings below so other docs can deep-link to the tier picker. Link as `<file>#<anchor>`.

| Entry point           | File                                 | Heading (verbatim)        | Anchor                |
|-----------------------|--------------------------------------|---------------------------|-----------------------|
| Tier picker (on-ramp) | `docs/getting-started/README.md`     | `## Choose Your Tier`     | `#choose-your-tier`   |
| Default path (T0)     | `docs/getting-started/README.md`     | `### Start Here — T0 Dev` | `#start-here--t0-dev` |
| Quick Start           | `docs/getting-started/quickstart.md` | `## Quick Start`          | `#quick-start`        |

## Cross-Cutting Augmentation Axis (Reference)

Data augmentation is an optional axis orthogonal to T0-T5, recommended only when data is scarce. It is
**not** part of the full training lifecycle, and the A0-A1 build is deferred. Listed here for vocabulary
completeness only.

| Stage | Approach                                                               | Where it runs                |
|-------|------------------------------------------------------------------------|------------------------------|
| A0    | Classical CV augmentation (crops, jitter, blur, photometric/geometric) | Local, CPU, no model         |
| A1    | Local small-VLM generation (e.g. vLLM locally, or Azure AI Foundry)    | Local GPU or hosted endpoint |
| A2    | Full Cosmos / SDG world-foundation-model pipeline                      | Cloud, GPU cluster           |
