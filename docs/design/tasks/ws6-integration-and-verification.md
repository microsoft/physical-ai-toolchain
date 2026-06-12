# WS6 — Integration & Verification

Final pass after WS1–WS4 have merged. Verify cross-document references resolve, vocabulary is
consistent across the whole tree, and the full documentation lint suite passes.

- **Branch:** `docs/tiered-arch/ws6-integration` (cut from `main` after WS1–WS4 merge)
- **Phase:** 2 (runs last)
- **Depends on:** WS1, WS2, WS3, WS4 (all merged)

## Owned Paths

No exclusive ownership. This workstream makes only small reconciliation edits where cross-document
links or vocabulary drifted between independently merged branches. If a substantive change is needed,
route it back to the owning workstream rather than editing its files here.

## Tasks

1. Run the full lint suite across the entire `docs/` tree and fix any cross-branch breakage:
   - `npm run lint:md`
   - `npm run spell-check`
   - `npm run format:tables`
   - `npm run lint:links`
2. Verify every cross-tier link in the WS0 link/anchor contract resolves to a real anchor (root README
   → architecture tiers → recipes → getting-started).
3. Confirm vocabulary consistency across all merged surfaces: no bare "fleet management"; "fleet"
   refers only to robots; fleet delivery (T4) and fleet intelligence (T5) used correctly.
4. Confirm T5 components and the T5.0–T5.3 autonomy ladder are consistently labeled as roadmap.
5. Verify the deferred A0–A1 augmentation work is documented (WS5) and not accidentally implemented.

## Acceptance Criteria

- The full lint suite passes across the entire `docs/` tree.
- All link/anchor-contract references resolve.
- Vocabulary and roadmap-labeling are consistent across every merged surface.
- No augmentation code was introduced (WS5 remains deferred).
