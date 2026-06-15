# Lessons

## Parallel agents sharing one git working tree must have strict file scoping

**Context:** Tiered-architecture doc rollout (WS0–WS6). WS1–WS4 ran as parallel agents in the same
working tree on one branch.

**What went wrong:** Some agents ran repo-wide tools — `markdown-table-formatter "**/*.md"` (default
glob touches every file) and `git checkout`/revert to "undo collateral changes to files they didn't
own." These cross-stomped siblings: WS0's edits to `tiered-architecture-proposal.md` and `.cspell.json`
were reverted by another stream's cleanup, and WS1/WS2 reported their own edits being reverted mid-run
and had to re-apply them. Net result survived only because a final integration pass detected the two
lost WS0 edits and restored them.

**Rules for next time:**

- Tell every parallel agent to run formatters/linters scoped to **explicit owned file paths only**,
  never a repo-wide glob (`"**/*.md"`), when sharing a working tree.
- Forbid agents from running `git checkout`/`git restore`/revert on files outside their owned set —
  the "collateral change" they see is usually a sibling's legitimate work.
- Prefer `isolation: "worktree"` (or one branch per agent) when parallel agents mutate files and any
  tool might reach outside their lane.
- Always run an integration/verification pass after a parallel fan-out that re-checks the *full* set of
  expected deliverables against `git status` — do not trust each agent's self-report, because a sibling
  may have reverted it after it finished.
