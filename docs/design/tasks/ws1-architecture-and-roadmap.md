# WS1 — Architecture & Roadmap Restructure

Restructure the contributor-facing architecture documentation around the T0–T5 tier ladder, mark the
T5 fleet-intelligence components as roadmap, and name the T5.0–T5.3 autonomy ladder.

- **Branch:** `docs/tiered-arch/ws1-architecture`
- **Phase:** 1 (parallel with WS2, WS3, WS4)
- **Depends on:** WS0 (`tier-model.md`, decisions, link/anchor contract)

## Owned Paths

| Path                                | Action                                                         |
|-------------------------------------|----------------------------------------------------------------|
| `docs/contributing/architecture.md` | Reorganize around T0–T5; mark maturity; apply fleet vocabulary |
| `docs/contributing/ROADMAP.md`      | Reframe milestones around tiers; add T5.0–T5.3 autonomy ladder |

> [!IMPORTANT]
> Do not edit the `fleet-deployment/` or `fleet-intelligence/` trees — those belong to WS2. Apply the
> fleet vocabulary only within the two files above, citing `tier-model.md` for definitions.

## Tasks

1. Restructure `architecture.md`:
   - Replace the 8-domain "all-or-nothing" framing with the T0–T5 progression. Lead with the tier
     ladder; present domains as components adopted per tier.
   - Add per-tier edge/cloud infrastructure tables drawn from the proposal's Section 5.
   - Mark T5 fleet-intelligence components as roadmap, not shipped (apply the WS0 roadmap-honesty
     labeling decision).
   - Apply the fleet delivery (T4) vs. fleet intelligence (T5) vocabulary; reserve "fleet" for robots.
   - Add stable anchors for each tier per the WS0 link/anchor contract so other workstreams can link
     in.
2. Restructure `ROADMAP.md`:
   - Align milestones to the tier ladder and the multi-site / intelligence boundaries.
   - Add the T5.0–T5.3 autonomy ladder as an ordered roadmap with decision-authority and status
     columns (from the proposal).
   - Clarify that autonomy (T5.0–T5.3) is a different axis from infrastructure reach (T0–T4).
3. Cross-link to `tier-model.md` as the canonical reference rather than redefining tier semantics.

## Acceptance Criteria

- `architecture.md` is organized around T0–T5 with per-tier infra tables and maturity markers.
- Fleet vocabulary is consistent with `tier-model.md`; no bare "fleet management".
- `ROADMAP.md` contains the T5.0–T5.3 autonomy ladder and tier-aligned milestones.
- Tier anchors match the WS0 link/anchor contract.
- `npm run lint:md`, `npm run spell-check`, `npm run format:tables`, `npm run lint:links` pass.
