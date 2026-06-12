# WS3 — On-Ramp & Entry-Point Reframing

Reframe the top-level entry points so a newcomer sees T0 (laptop + one robot, zero cloud, zero
Kubernetes) as the sanctioned starting path, with higher tiers presented as opt-in additions.

- **Branch:** `docs/tiered-arch/ws3-onramp`
- **Phase:** 1 (parallel with WS1, WS2, WS4)
- **Depends on:** WS0 (default-tier decision, link/anchor contract)

## Owned Paths

| Path                                 | Action                                                             |
|--------------------------------------|--------------------------------------------------------------------|
| `README.md` (repository root)        | Reframe overview, on-ramp, and Quick Start around the tier ladder  |
| `docs/README.md`                     | Add a tier-keyed entry guide alongside the existing audience guide |
| `docs/getting-started/README.md`     | Introduce tier-specific entry points                               |
| `docs/getting-started/quickstart.md` | Present T0 as the default path; link higher tiers as additions     |

> [!IMPORTANT]
> Link to architecture tier anchors (owned by WS1) and per-tier recipes (owned by WS4) using the WS0
> link/anchor contract. Do not create or edit those files here.

## Tasks

1. Root `README.md`: lead the on-ramp with T0 as the honest floor (the capture → train → validate →
   run loop on one laptop and one robot). Present cloud, Kubernetes, Arc, and fleet components as
   tier-gated opt-in additions rather than baseline prerequisites. Apply the WS0 default-tier decision
   (e.g., T0 default, T2 recommended-production, T3–T5 advanced).
2. `docs/README.md`: add a tier-keyed navigation table mapping each tier to its quick-start and
   architecture section, complementing the existing role-based index.
3. `docs/getting-started/`: make T0 the default quick-start; link T1/T2 (and beyond) as graduation
   steps with the "graduate when…" triggers from the proposal.
4. Ensure all cross-links resolve to the contract paths defined by WS0.

## Acceptance Criteria

- The root README presents T0 as the sanctioned starting point; heavy components read as opt-in.
- Getting-started content offers a clear local-first path with graduation triggers.
- Entry points link correctly to WS1 architecture anchors and WS4 recipes via the WS0 contract.
- README H2 headings retain the repository emoji convention.
- `npm run lint:md`, `npm run spell-check`, `npm run format:tables`, `npm run lint:links` pass.
