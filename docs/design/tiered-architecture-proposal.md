# Physical AI Toolchain — Tiered Architecture Proposal

**Date:** June 9th, 2026
**Author:** David White
**Status:** Draft for socialization and feedback
**Scope:** Architecture and on-ramp framing for [microsoft/physical-ai-toolchain](https://github.com/microsoft/physical-ai-toolchain)
**Type:** Proposal — not yet adopted

---

## Executive Summary (TL;DR)

The Physical AI Toolchain ships a powerful end-to-end stack, but it presents that stack as
**all-or-nothing**. A newcomer reading the architecture documentation concludes they must stand up
Azure Arc, AKS, FluxCD, ACSA, IoT Operations, and a cloud training plane *before they can do
anything useful*. In reality, the repository's working code can close a complete capture → train →
evaluate → run loop on a single laptop and one robot with **zero cloud and zero Kubernetes**. The
gap between what the code supports and what the documentation implies is the single largest barrier
to entry.

This proposal reframes the architecture around **graduated adoption tiers**. Each tier states the minimum edge and cloud infrastructure required to reach a concrete goal, and each tier is a legitimate stopping point. Users adopt only the infrastructure their scale actually demands, and the heaviest components become opt-in additions rather than baseline prerequisites.

The proposal also corrects a framing problem: the repository bundles two distinct concerns under the
word **"fleet."** One is a *delivery and connectivity control plane* (get a validated policy onto robots
across sites you cannot directly reach). The other is *fleet management cognition* (drift detection,
automated retraining, aggregate telemetry analytics). The first is legitimate and necessary at multi-site
scale. The second is largely unimplemented and over-advertised. Separating them lets a multi-site
operator adopt the delivery plane without inheriting the speculative management layer.

### What This Proposal Argues

1. The barrier to entry is a **documentation and sequencing problem**, not a fundamental property of the architecture. The code already supports a low-friction path; the docs do not describe it.
2. Adoption should be modeled as **six tiers (T0–T5)** keyed to operator reach, GPU availability, collaboration, and site count — not robot count.
3. The word **"fleet"** conflates a necessary *delivery control plane* with a speculative *management layer*. These should be separate tiers.
4. The maturity evidence supports drawing the line where we propose: the implemented core is infrastructure, training, data management, and evaluation. The fleet-management cognition is mostly placeholder.

---

## 1. The Problem: On-Ramp Friction

The current architecture documentation foregrounds the maximal configuration. It describes a closed-loop, multi-site, GitOps-managed robot fleet with telemetry-driven retraining as the headline identity of the project. Everything else reads as a component *of* that endgame.

The practical consequence: a user evaluating the toolkit perceives the cost of entry as the sum of **every** moving part, on both the edge and the cloud:

| Surface | Components the docs imply are required |
|---|---|
| Edge | Arc-enabled Kubernetes (K3s or AKS Edge Essentials), ACSA extension, cert-manager, IngestSubvolume CRD, FluxCD, ROS 2 recording service, gating service, IoT Operations MQTT broker |
| Cloud | AKS, AzureML workspace, Azure Storage, Container Registry, Key Vault, managed identity, Event Hubs, Microsoft Fabric Real-Time Intelligence, Grafana, point-to-site VPN |

Even with the repository's deploy scripts, the perceived setup is a long, ordered sequence of installations and credential wiring across two environments before the first useful result. The "private AKS by default" network model compounds this: a VPN must exist before any `kubectl` command runs.

> [!NOTE]
> The friction is *perceived* cost, and perception is set by documentation. The same components, presented as optional additions layered onto a working local baseline, would not read as a barrier. This is why the fix is primarily a re-framing, not a rewrite.

### The Core Claim

> The edge architecture is premature and over-advertised relative to its implementation, and there is no documented low or middle tier, so a newcomer mistakes the aspirational ceiling for the required floor.

---

## 2. Evidence: What Is Actually Built

A file-level survey of the eight domains separates shipped code from aspirational specification. The signal is unambiguous.

| Domain | Python | Shell | Terraform | Placeholder specs |
|---|---|---|---|---|
| `infrastructure` | 0 | 18 | 80 | 0 |
| `data-management` | 49 | 2 | 0 | 0 |
| `training` | 23 | 8 | 0 | 0 |
| `evaluation` | 15 | 7 | 0 | 0 |
| `data-pipeline` | 3 | 1 | 0 | 0 |
| `synthetic-data` | 0 | 0 | 0 | 2 |
| `fleet-deployment` | 1 | 1 | 0 | 5 |
| `fleet-intelligence` | 0 | 4 | 0 | 4 |

The implemented center of gravity is **infrastructure provisioning plus training, data management, and evaluation tooling.** The two domains that carry the "fleet" identity — `fleet-deployment` and `fleet-intelligence` — hold **one Python file between them and nine placeholder specifications.** The headline identity of the project is its least-built region.

Two consequences follow:

1. The heavy edge stack is largely **documented intent**, not shipped complexity. The barrier is mostly prerequisite weight and framing, not a large body of code a user must understand.
2. Because the fleet domains are unbuilt, formalizing a lighter default on-ramp **costs almost nothing** — there is little working code to reorganize.

### The Local Path Already Exists

Three of the four core domains run with no cloud dependency today:

- **Training** — `training/il/scripts/lerobot/train.py` detects available CUDA devices at runtime and trains wherever it is invoked. The OSMO and AzureML submission scripts are wrappers *around* this, not a requirement of it.
- **Evaluation** — `evaluation/sil/scripts/run-local-lerobot-eval.py` and `play.py` provide an explicit local evaluation path.
- **Data management** — the dataviewer defaults to `STORAGE_BACKEND=local` and serves entirely from a laptop.

The capability to start small is present in the code. It is simply undocumented as a sanctioned path.

---

## 3. Reframing "Fleet": Delivery Plane vs. Management Layer

The strongest objection to the current architecture is not "Arc and AKS are unnecessary." At multi-site scale they are genuinely needed. The objection is that the repository welds two different concerns together under one word.

| Concern | What it is | Repository domain | Maturity |
|---|---|---|---|
| Delivery control plane | Multi-site connectivity, declarative delivery, safe-swap gating | `fleet-deployment` | 1 Python file, 5 placeholders |
| Fleet management | Drift detection, retraining triggers, aggregate telemetry analytics | `fleet-intelligence` | 0 Python files, 4 placeholders |

The **delivery control plane** is transport. Across sites you do not control, you need a reachability and identity broker (Arc), a runtime (AKS), and a delivery mechanism (FluxCD). This is, in effect, a `git push` that survives bad networks and locked-down sites, with a safety gate before a policy swaps on a physical arm. It is legitimate and necessary once an operator spans multiple sites.

The **fleet management layer** is cognition. Observing many robots, detecting aggregate degradation, and closing the retraining loop is the part that earns the word "fleet" — and it is the unbuilt, over-advertised part.

> [!IMPORTANT]
> Separating these is what lets a multi-site operator adopt the necessary delivery plane **without** inheriting the speculative management layer. The repository's single word "fleet" makes the necessary inseparable from the speculative. Unbundling the word is the core architectural move of this proposal.

One honest caveat: the split is real but porous. The gating service is a *decision* (approve a safe deployment window), so a small amount of cognition already lives in the delivery plane, and a multi-site delivery plane inevitably records per-site desired state, which is the seed of management. The tiers below treat gating as part of delivery and draw the management boundary at drift/retraining/analytics.

---

## 4. The Tier Model

Tiers are keyed to the variables that actually drive infrastructure cost:

- **Operator reach** — can one network and one `kubectl` context reach every robot?
- **GPU availability** — is there a local GPU, or must compute be rented?
- **Collaboration** — one person, or a team that needs shared catalogs, tracking, and registries?
- **Site count** — one location, or many?

Robot count correlates with these but does not cause them. Three identical robots in one lab you control are trivial; three robots across three customer sites you cannot reach are not.

### Reference Goal

Each tier states the minimum infrastructure to reach a concrete goal:

> **Goal G:** Capture demonstrations on a robot, train an imitation policy, validate it, and run that policy back on the robot — the full loop, one task. Fleet management is explicitly out of scope of Goal G.

Goal G is fully achievable at Tiers 0 through 2 without any Kubernetes, Arc, or fleet infrastructure.

### Tier Ladder Overview

Each tier carries a stable ID (`T0`–`T5`) and a stage name. The IDs are canonical and are used for every boundary and graduation reference; the names are the user-facing labels.

```text
T0  Dev               Laptop + 1 robot          edge: none               cloud: none
T1  Lab               Shared GPU + few robots   edge: shared disk        cloud: Blob (+ optional AzureML/MLflow)
T2  Pilot             Single site, at scale     edge: none (manual)      cloud: AzureML + registry + MLflow
T3  Single-Site Prod  Single-site declarative   edge: local k3s + Flux   cloud: same as T2 (no Arc)
─────────────────────────────────────────────────────────────────── multi-site boundary (Arc)
T4  Multi-Site Prod   Multi-site delivery       edge: Arc + AKS + Flux   cloud: + connectivity/identity
─────────────────────────────────────────────────────────────────── management boundary
T5  Fleet Ops         Fleet management          edge: + IoT Operations   cloud: + Fabric RTI + drift/retraining
```

T0–T2 satisfy Goal G with manual deployment. T3 adds single-site declarative deployment (local k3s + Flux) **without Arc** — proving GitOps does not require a cloud fleet control plane. T4 is the **multi-site delivery control plane terminus**, where Arc becomes necessary as the cross-site reachability and identity broker. T5 is the fleet-management layer, marked deferred and over-advertised relative to its implementation.

---

## 5. Tier Detail

### T0 — Dev. One robot, one laptop. No cloud, no Kubernetes

The honest floor for Goal G.

| Concern | Implementation |
|---|---|
| Capture | ROS 2 bag recording to local disk on the robot or laptop. No Arc, no ACSA, no PVC. |
| Move data | `cp` or `rsync` from robot to laptop. |
| Curate | Dataviewer in `local` mode on the laptop. |
| Train | `train.py` on a laptop or workstation GPU. |
| Validate | `run-local-lerobot-eval.py` / `play.py` locally. |
| Run on robot | The ACT inference node as a plain process or container. No Flux, no gating, no GitOps. |

**Edge infra:** ROS 2 and Docker only. **Cloud infra:** none.

This tier exists in the code today and is undocumented. Surfacing it is the highest-leverage change in this proposal.

**Graduate when:** no local GPU; the task needs many training iterations as conditions vary; or a second person needs the data.

### T1 — Lab. One site, a few robots, a shared GPU box. Storage-only cloud

The small-lab and integrator tier. The first cloud resource added is a single storage account.

| Concern | Implementation | Delta from T0 |
|---|---|---|
| Capture | ROS 2 recording to shared NFS/SMB, or each robot `rsync`s up. | shared disk |
| Move data | `azcopy` or `az storage blob upload-batch` to one Blob container. | + Blob storage |
| Curate | Dataviewer in `azure` mode against that container (managed identity or SAS). | viewer → cloud |
| Train | Local shared GPU box, or first optional reach to AzureML on saturation. | optional cloud GPU |
| Track | MLflow becomes worthwhile with multiple runs or collaborators. | + tracking |
| Run on robot | Plain container per robot; hand-update 2–3 robots via `docker pull`. | unchanged |

**Edge infra:** shared disk. **Cloud infra:** one storage account, optionally AzureML and MLflow. No Kubernetes, no Arc, no Flux.

**Graduate when:** training scale or team size outgrows one GPU box; dataset governance and catalogs become necessary.

### T2 — Pilot. One site, several robots, real training scale and collaboration

The tier where cloud training genuinely becomes the default rather than an option.

| Concern | Implementation | Delta from T1 |
|---|---|---|
| Train | AzureML or OSMO as default: multi-GPU, queued jobs, multiple people, VLA scale. | cloud GPU standard |
| Registry | Model registry and versioning become load-bearing. | + registry |
| Curate | Dataviewer deployed as a shared web app rather than localhost. | hosted viewer |
| Capture | ACSA optional if disk pressure or unattended recording warrants it. | optional ACSA |
| Run on robot | Manual `docker pull` per robot. Hand-updating a handful of reachable robots is still tractable. | unchanged |

**Edge infra:** none beyond Docker. **Cloud infra:** AzureML workspace, storage, registry, MLflow. Still no Kubernetes, no Arc, no fleet plane.

**Graduate when:** the number of robots or the update cadence makes hand-updating each robot error-prone, and version skew across robots becomes a real problem — but all robots are still at one reachable site.

### T3 — Single-Site Prod. Local k3s + Flux, no Arc

The tier that proves declarative, GitOps-style deployment does **not** require
Azure Arc.
Several robots at one site you control, updated often enough that manual `docker pull` causes
version skew, but all reachable from a single operator network. A single local k3s node running
FluxCD reconciles every robot to a Git-declared desired state. Arc is unnecessary precisely
because there is only one site and you can reach it directly.

| Concern | Implementation |
|---|---|
| Runtime | A single local k3s node (a ~60 MB binary) at the site. |
| Delivery | FluxCD reconciles robots to Git-declared desired state; rollback is a Git revert. |
| Version control | Git becomes the single source of truth for which robot runs which policy. |
| Safe swap | Optional gating before a policy swaps on hardware. |
| Train and curate | Same as T2 (AzureML, registry, MLflow, hosted viewer). |

**Why this is a full tier, not a footnote:** it isolates the single most misattributed cost in the whole architecture. The expensive part of the "fleet" stack was never running Kubernetes at the edge — single-node k3s idles near zero. The expensive part is the cloud-side control plane (Arc enrollment, identity, policy). T3 delivers GitOps deployment automation while paying none of that. It is the decoupling that makes the entire middle of the ladder honest.

**Edge infra:** one local k3s node + FluxCD. **Cloud infra:** same as T2. **No Arc, no IoT Operations.**

**Graduate when:** robots span multiple sites, or sites become unreachable from a single operator network — the point at which a cross-site reachability and identity broker becomes genuinely necessary.

### T4 — Multi-Site Prod. Multi-site delivery control plane

The legitimate top of the necessary ladder. This is the "communication and delivery" plane: getting validated policies onto robots across sites you cannot directly reach, safely. The defining difference from T3 is **multiple sites** — which is exactly what makes Arc necessary, as the cross-site reachability and identity broker that single-site k3s did not need.

| Concern | Implementation |
|---|---|
| Connectivity and identity | Azure Arc as the reachability and identity broker across sites. |
| Runtime | AKS or Arc-enabled Kubernetes at each site. |
| Delivery | FluxCD GitOps; per-site desired state recorded in Git. |
| Safe swap | Gating service approves deployment windows before a policy swaps on hardware. |

**Explicitly excluded at T4:** drift detection, automated retraining, aggregate telemetry analytics. This tier delivers and gates; it does not manage.

**Edge infra:** Arc + AKS/Flux + gating. **Cloud infra:** connectivity, identity, registry.

**Graduate when:** the operator explicitly wants production signals to drive retraining and fleet-wide health analytics — a deliberate decision, not an automatic consequence of scale.

### T5 — Fleet Ops. Fleet management. Deferred

The aspirational layer. Drift detection, automated retraining triggers, aggregate telemetry, and health analytics.

| Concern | Implementation | Status |
|---|---|---|
| Edge telemetry | Azure IoT Operations MQTT aggregation | placeholder |
| Analytics | Microsoft Fabric Real-Time Intelligence, Grafana | placeholder |
| Drift and retraining | Drift detection, retraining triggers, closed loop | placeholder |

**Status:** mostly unimplemented (0 Python files, 4 placeholders in `fleet-intelligence`). This tier should be documented as a roadmap direction, with explicit human-in-the-loop gating recommended over fully autonomous retraining.

> [!WARNING]
> Fully autonomous retraining on production data is a foot-gun: a legitimate distribution change can cause the loop to bake current degraded behavior into the next dataset. Drift detection also needs statistical power that only exists at fleet scale. T5 should default to human-supervised, not closed-loop.

---

## 6. What Changes for the Repository

This proposal is primarily documentation and packaging, not a code rewrite.

| Change | Effort | Impact |
|---|---|---|
| Document T0 as the sanctioned starting path | Low — code already supports it | Removes the perceived barrier directly |
| Restructure the architecture doc around T0–T5 | Low–medium | Reframes the on-ramp; sets correct expectations |
| Split "fleet" into delivery (T4) and management (T5) language | Low | Lets multi-site users adopt delivery without management |
| Mark T5 components explicitly as roadmap | Low | Stops the unbuilt ceiling from reading as a floor |
| Add per-tier quick-starts | Medium | Gives each audience a concrete entry point |

The deploy scripts, Terraform modules, and training code do not need to change to support this. The tiers describe *which subset* of existing infrastructure a user adopts, and in what order.

---

## 7. Open Questions for Socialization

1. **Default tier in docs.** Should the README and Quick Start default to T0 (Dev), with T2 (Pilot) as the "recommended production" path and T3–T5 (Single-Site Prod, Multi-Site Prod, Fleet Ops) clearly marked advanced? Do the stage names — Dev, Lab, Pilot, Single-Site Prod, Multi-Site Prod, Fleet Ops — read correctly, and is pairing each name with its `T#` ID the right convention (stable IDs for boundary references, names for user-facing labels)?
2. **Fleet vocabulary.** Does the repository adopt "delivery control plane" and "fleet management" as distinct named concepts, retiring the unqualified word "fleet"?
3. **Roadmap honesty.** How explicitly should placeholder domains be labeled in user-facing docs versus contributor docs?
4. **Scope of Goal G.** Is the single-task capture → train → validate → run loop the right anchor goal, or should the reference goal include synthetic data augmentation?

---

## 8. Summary

The toolchain is strong where it is built and over-advertised where it is not. The barrier to entry is the result of presenting an aspirational, multi-site, fleet-managed ceiling as the required floor. The fix is to:

1. Surface the **already-working local path** (T0) as the sanctioned starting point.
2. Model adoption as **graduated tiers** keyed to reach, GPU, collaboration, and site count.
3. **Unbundle "fleet"** into a necessary delivery control plane (T4) and a deferred management layer (T5).

This lowers the on-ramp without removing any capability, and it aligns the documented architecture with what the code actually delivers today.

---

## Appendix A — Future Direction: Tier-Aware Scaffolding CLI

> [!NOTE]
> This appendix is **speculative and explicitly out of scope** for the core proposal. The deliverable above is the tier model and its documentation; it stands on its own and requires no new tooling. This idea is recorded only because the tiers make it a natural next step, not because the proposal depends on it.

Once adoption is modeled as discrete, composable tiers, a tier-aware scaffolding CLI becomes a straightforward way to operationalize the on-ramp. The pattern is well established — `terraform init`, `flux bootstrap`, `gh`, and `az` all ask a small set of questions and scaffold the appropriate configuration.

A `pat init`-style command could ask a handful of questions that map directly to the tier axes:

- How many sites? (one vs. many → T3 boundary vs. T4)
- Is there a local GPU, or should training run in the cloud? (T0/T1 vs. T2)
- One person or a team? (local checkpoints vs. registry + MLflow)
- How many robots, and how often are they updated? (manual vs. declarative deployment)

From those answers it would resolve a tier and scaffold the matching subset of the **existing** deploy scripts, Terraform, and configuration — the same artifacts a user would otherwise assemble by hand.

### Design Constraint: Transparency, Not a Black Box

A scaffolding CLI must not re-create the opacity this proposal argues against. A wizard that silently provisions hidden infrastructure would *lower* friction while *raising* the conceptual barrier — the user could not see what was stood up, could not learn the tiers, and would be stranded inside generated configuration they did not write when something broke.

To avoid this, the CLI should **generate visible, editable artifacts** rather than execute a hidden runtime:

- Emit the actual scripts, Terraform, and config files into the user's working tree, then let them review and run them.
- Print the resolved tier and the reasoning, so the questions are a teaching tool, not a curtain.
- Treat generation and execution as separate steps, so a user can inspect before applying.

Under those constraints the CLI reinforces the proposal's goal — approachability through understanding — rather than substituting convenience for comprehension.

### Why This Is Feasible

This is tractable **because** the tiers define discrete, composable infrastructure sets with explicit graduation triggers. Without the tier model, "scaffold the right thing" has no well-defined target; with it, each tier is already a named, bounded subset of existing artifacts. The CLI would compose what the repository already ships — not introduce a new control plane of its own.
