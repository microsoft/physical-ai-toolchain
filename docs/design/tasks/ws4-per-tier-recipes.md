# WS4 — Per-Tier Quick-Starts / Recipes

Give each tier a concrete, runnable entry point by organizing the recipes around the tier ladder,
starting with a T0 local-only quick-start that uses the already-shipping local code paths.

- **Branch:** `docs/tiered-arch/ws4-recipes`
- **Phase:** 1 (parallel with WS1, WS2, WS3)
- **Depends on:** WS0 (link/anchor contract, recipe filename conventions)

## Owned Paths

| Path              | Action                                                       |
|-------------------|--------------------------------------------------------------|
| `docs/recipes/**` | Add per-tier quick-starts; organize existing recipes by tier |

New recipe folders self-register in the Docusaurus sidebar via their own `_category_.json`; do not
edit `docs/docusaurus/sidebars.js`.

## Tasks

1. Author a T0 quick-start recipe that walks the full Goal G loop with zero cloud and zero Kubernetes,
   referencing the existing local code paths:
   - Train: `training/il/scripts/lerobot/train.py` (runtime CUDA detection).
   - Evaluate: `evaluation/sil/scripts/run-local-lerobot-eval.py` and `play.py`.
   - Curate: dataviewer in `local` mode.
   - Track: file-backed MLflow / trackio (local process, no server).
2. Map existing recipes (`docs/recipes/data-collection/**`, `docs/recipes/training/**`) to their tier
   and add tier labels / a tier index so each recipe states the minimum infrastructure it assumes.
3. Add higher-tier quick-start stubs (T2 cloud training, T3 single-site k3s + Flux, T4 multi-site
   delivery) that link to existing deployment docs rather than duplicating them.
4. Use the recipe filenames/paths from the WS0 link/anchor contract so WS3 entry points link correctly.

## Acceptance Criteria

- A T0 recipe demonstrates the full local loop with no cloud or Kubernetes dependency.
- Existing recipes are tier-labeled with explicit minimum-infrastructure statements.
- Higher-tier quick-starts link to existing deployment docs without duplication.
- New folders include `_category_.json`; sidebar is not hand-edited.
- Recipe paths match the WS0 contract consumed by WS3.
- `npm run lint:md`, `npm run spell-check`, `npm run format:tables`, `npm run lint:links` pass.
