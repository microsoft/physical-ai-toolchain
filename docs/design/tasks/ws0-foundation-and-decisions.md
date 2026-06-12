# WS0 — Foundation & Decisions

Resolve the proposal's open socialization questions, record the decisions, and publish the canonical
artifacts every other workstream depends on. This is the **gate**: WS1–WS4 cannot start until this
workstream merges.

- **Branch:** `docs/tiered-arch/ws0-foundation`
- **Phase:** 0 (blocks WS1–WS4)
- **Depends on:** nothing

## Owned Paths

| Path                                          | Action                                                                           |
|-----------------------------------------------|----------------------------------------------------------------------------------|
| `docs/design/tiered-architecture-proposal.md` | Update status from Draft to the adopted decision; record resolved open questions |
| `docs/design/tasks/**`                        | This task plan (already authored)                                                |
| `docs/design/tier-model.md`                   | New — canonical tier glossary, vocabulary rules, link/anchor contract            |
| `cspell` dictionary / project word list       | Pre-seed all new vocabulary so downstream workstreams pass spell-check           |

> [!NOTE]
> WS0 owns the shared `cspell` dictionary update on purpose. Seeding every new term once here prevents
> four parallel workstreams from racing to edit the same shared config file.

## Decisions to Resolve

These come straight from Section 7 of the proposal. Each must be answered and recorded before
downstream wording is finalized.

| # | Question                                                                                                                                                                                               | Output                              |
|---|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------|
| 1 | Default tier in README/Quick Start (T0 default, T2 recommended-production, T3–T5 advanced?). Confirm stage names (Dev, Lab, Pilot, Production, Scale, Operate) and the `T#` + name pairing convention. | Recorded decision + naming table    |
| 2 | Adopt "fleet delivery" (T4) and "fleet intelligence" (T5) as named concepts; reserve "fleet" for robots; retire bare "fleet management".                                                               | Vocabulary rules in `tier-model.md` |
| 3 | How explicitly to label placeholder domains in user-facing vs. contributor docs.                                                                                                                       | Roadmap-honesty labeling guidance   |
| 4 | Keep Goal G minimal with augmentation as a separate optional axis (A0–A2), or fold augmentation into Goal G.                                                                                           | Recorded decision                   |

## Tasks

1. Drive resolution of the four open questions (socialization). Record each decision and its rationale
   in `tiered-architecture-proposal.md`, and flip the document `Status` to the adopted state.
2. Author `docs/design/tier-model.md` as the single source of truth containing:
   - Canonical tier table: `T#`, stage name, edge infra, cloud infra, one-line purpose (T0–T5).
   - The T5.0–T5.3 autonomy ladder table (decision-authority axis, orthogonal to T0–T4).
   - Vocabulary rules: fleet delivery vs. fleet intelligence; "fleet = robots only"; banned phrase
     "fleet management".
   - The cross-document **link/anchor contract**: the canonical file paths and heading anchors each
     downstream workstream must link to (e.g., architecture tier anchors, recipe filenames,
     getting-started entry anchors). This lets WS1–WS4 link to each other's not-yet-written content
     without guessing paths.
3. Pre-seed the `cspell` dictionary with new vocabulary so WS1–WS4 pass `npm run spell-check`. Candidate
   terms: `trackio`, `vLLM`, tier stage names, and any product names introduced.

## Acceptance Criteria

- All four open questions have a recorded decision; proposal status reflects adoption.
- `docs/design/tier-model.md` exists and contains the tier table, autonomy ladder, vocabulary
  rules, and the link/anchor contract.
- `cspell` dictionary updated; `npm run spell-check` passes on the changed files.
- `npm run lint:md`, `npm run format:tables`, and `npm run lint:links` pass on owned files.

## Handoff to Downstream Workstreams

After merge, WS1–WS4 each:

- Treat `docs/design/tier-model.md` as read-only canonical truth.
- Use the link/anchor contract for all cross-tier references.
- Assume new vocabulary already passes spell-check.
