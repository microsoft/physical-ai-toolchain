# WS2 — Fleet Delivery vs. Fleet Intelligence Vocabulary

Split the conflated "fleet" concept into **fleet delivery** (T4, implemented) and **fleet
intelligence** (T5, mostly placeholder) throughout the fleet domain trees, and label unbuilt T5
features honestly.

- **Branch:** `docs/tiered-arch/ws2-fleet-vocabulary`
- **Phase:** 1 (parallel with WS1, WS3, WS4)
- **Depends on:** WS0 (`tier-model.md` vocabulary rules)

## Owned Paths

| Path                                   | Action                                                       |
|----------------------------------------|--------------------------------------------------------------|
| `fleet-deployment/README.md`           | Reframe as fleet delivery (T4); apply vocabulary             |
| `fleet-deployment/specifications/**`   | Apply vocabulary; mark implemented status                    |
| `fleet-intelligence/README.md`         | Reframe as fleet intelligence (T5); mark roadmap/placeholder |
| `fleet-intelligence/specifications/**` | Mark placeholder status; map to T5.0–T5.3 where relevant     |
| `docs/fleet-deployment/**`             | Apply vocabulary to user-facing docs                         |
| `docs/fleet-intelligence/**`           | Mark roadmap/placeholder per WS0 labeling decision           |

> [!IMPORTANT]
> Do not edit `docs/contributing/architecture.md` or `docs/contributing/ROADMAP.md` — those belong to
> WS1. Both workstreams apply the same WS0 vocabulary, but to disjoint files.

## Tasks

1. In the `fleet-deployment/` tree: position the domain as the T4 fleet-delivery control plane
   (connectivity/identity broker, GitOps delivery, gating). Replace bare "fleet" / "fleet management"
   with "fleet delivery" or robot-anchored phrasing per `tier-model.md`.
2. In the `fleet-intelligence/` tree: position the domain as the T5 fleet-intelligence layer (drift,
   retraining, aggregate telemetry). Apply the WS0 roadmap-honesty labeling so the placeholder status
   (0 Python files, design specs only) is explicit in user-facing docs.
3. Where the intelligence specs describe loop closure, map them onto the T5.0–T5.3 autonomy ladder and
   carry the human-in-the-loop / foot-gun warning from the proposal.
4. Audit both trees for the banned phrase "fleet management" and any "fleet = clusters" confusion;
   correct per the vocabulary rules.

## Acceptance Criteria

- "Fleet delivery" and "fleet intelligence" are used consistently and correctly across both trees.
- No occurrence of bare "fleet management"; "fleet" refers only to robots.
- T5 fleet-intelligence docs are explicitly labeled roadmap/placeholder.
- Cross-links cite `tier-model.md` for definitions.
- `npm run lint:md`, `npm run spell-check`, `npm run format:tables`, `npm run lint:links` pass.
