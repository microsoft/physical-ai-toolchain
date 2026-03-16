---
title: Copilot Artifacts
description: >-
  Inventory and reference for GitHub Copilot agents, instructions, prompts,
  and skills configured in this repository.
author: Microsoft Robotics-AI Team
ms.date: 2026-03-11
ms.topic: reference
keywords:
  - copilot
  - agents
  - instructions
  - prompts
  - skills
  - extensibility
---

GitHub Copilot extensibility artifacts provide AI-assisted workflows
for dataset analysis, training job management, and coding standards
enforcement. These artifacts are configured in `.github/` and activate
automatically in VS Code.

## 📋 Artifact Inventory

| Type        | Name                       | Description                                         | Path                                                              |
|-------------|----------------------------|-----------------------------------------------------|-------------------------------------------------------------------|
| Agent       | Dataviewer Developer       | Interactive dataset analysis and tool development   | `.github/agents/dataviewer-developer.agent.md`                    |
| Agent       | OSMO Training Manager      | LeRobot training lifecycle on OSMO with Azure ML    | `.github/agents/osmo-training-manager.agent.md`                   |
| Instruction | Commit Messages            | Conventional Commits format for all commit messages | `.github/instructions/commit-message.instructions.md`             |
| Instruction | Dataviewer                 | Coding standards for dataviewer development         | `.github/instructions/dataviewer.instructions.md`                 |
| Instruction | Docs Style and Conventions | Writing standards for all markdown files            | `.github/instructions/docs-style-and-conventions.instructions.md` |
| Instruction | Shell Scripts              | Implementation standards for bash scripts           | `.github/instructions/shell-scripts.instructions.md`              |
| Prompt      | `/chatlog`                 | Create and maintain conversation logs               | `.github/prompts/chatlog.prompt.md`                               |
| Prompt      | `/check-training-status`   | Monitor OSMO training job progress                  | `.github/prompts/check-training-status.prompt.md`                 |
| Prompt      | `/start-dataviewer`        | Launch Dataset Analysis Tool                        | `.github/prompts/start-dataviewer.prompt.md`                      |
| Prompt      | `/submit-lerobot-training` | Submit LeRobot training job to OSMO                 | `.github/prompts/submit-lerobot-training.prompt.md`               |
| Skill       | dataviewer                 | Dataset browsing, annotation, and export            | `.github/skills/dataviewer/SKILL.md`                              |
| Skill       | osmo-lerobot-training      | Training submission, monitoring, and analysis       | `.github/skills/osmo-lerobot-training/SKILL.md`                   |

## 🔗 Quick Reference

| Want to...                             | Use this artifact                                         |
|----------------------------------------|-----------------------------------------------------------|
| Launch the Dataset Analysis Tool       | `/start-dataviewer` prompt → Dataviewer Developer         |
| Browse and annotate training episodes  | Dataviewer Developer agent                                |
| Submit a LeRobot training job          | `/submit-lerobot-training` prompt → OSMO Training Manager |
| Check training job status              | `/check-training-status` prompt → OSMO Training Manager   |
| Save a conversation log                | `/chatlog` prompt                                         |
| Enforce commit message standards       | `commit-message` instruction (auto-applied)               |
| Enforce coding standards in dataviewer | `dataviewer` instruction (auto-applied)                   |
| Enforce markdown writing standards     | `docs-style-and-conventions` instruction (auto-applied)   |
| Enforce shell script standards         | `shell-scripts` instruction (auto-applied)                |

## 🤖 Agents

### Dataviewer Developer

Interactive agent for launching, browsing, annotating, and improving the
Dataset Analysis Tool.

| Property | Value                                               |
|----------|-----------------------------------------------------|
| Handoffs | Start Dataviewer, Browse Dataset, Annotate Episodes |
| Tools    | All (no restrictions)                               |
| Skill    | `dataviewer`                                        |
| Prompts  | `/start-dataviewer`                                 |

Four-phase workflow: Launch/Configure → Interactive Browsing (Playwright) →
Episode Annotation (API+UI) → Feature Development (React+FastAPI).

### OSMO Training Manager

Multi-turn agent for managing LeRobot imitation learning training lifecycle
on OSMO with Azure ML integration.

| Property | Value                                                                |
|----------|----------------------------------------------------------------------|
| Handoffs | Submit Training Job, Check Training Status, Run Inference Evaluation |
| Tools    | 11 explicit (run_in_terminal, memory, runSubagent, ...)              |
| Skill    | `osmo-lerobot-training`                                              |
| Prompts  | `/submit-lerobot-training`, `/check-training-status`                 |

Five-phase workflow: Submit → Monitor → Analyze → Summarize → Inference
Evaluation. Handles VM eviction recovery, CUDA errors, and KeyError
failures.

## 📝 Instructions

Instructions activate automatically when files matching their `applyTo`
pattern appear in the chat context.

| Name                       | Applies To                  | Purpose                                                 |
|----------------------------|-----------------------------|---------------------------------------------------------|
| Commit Messages            | `**`                        | Conventional Commits format, scopes, line-length limits |
| Dataviewer                 | `data-management/viewer/**` | SOLID principles, test-first, validation commands       |
| Docs Style and Conventions | `**/*.md`                   | Document hierarchy, tables, voice/tone, frontmatter     |
| Shell Scripts              | `**/*.sh`                   | Script template, library functions, deployment patterns |

## ⚡ Prompts

Prompts are slash commands invoked via `/` in the chat input. Each prompt
targets a specific agent.

| Command                    | Agent Target          | Required Inputs       |
|----------------------------|-----------------------|-----------------------|
| `/chatlog`                 | Generic               | None                  |
| `/check-training-status`   | OSMO Training Manager | workflowId (optional) |
| `/start-dataviewer`        | Dataviewer Developer  | datasetPath           |
| `/submit-lerobot-training` | OSMO Training Manager | dataset (required)    |

## 🛠️ Skills

Skills provide multi-file capabilities with progressive 3-level loading:
discovery (frontmatter only) → instructions (SKILL.md body) → resources
(bundled reference files).

### dataviewer

| Property  | Value                                                                      |
|-----------|----------------------------------------------------------------------------|
| Directory | `.github/skills/dataviewer/`                                               |
| Resources | `references/PLAYWRIGHT.md` (selectors, interaction recipes, API endpoints) |
| Used by   | Dataviewer Developer agent                                                 |

### osmo-lerobot-training

| Property  | Value                                                                                                                  |
|-----------|------------------------------------------------------------------------------------------------------------------------|
| Directory | `.github/skills/osmo-lerobot-training/`                                                                                |
| Resources | `references/DEFAULTS.md` (env, datasets, GPU profiles), `references/REFERENCE.md` (CLI, inference, AzureML navigation) |
| Used by   | OSMO Training Manager agent                                                                                            |

## 🔄 Workflow Chains

Agents compose prompts and skills into end-to-end workflows:

```text
OSMO Training Manager (agent)
  ├── /submit-lerobot-training (prompt)
  ├── /check-training-status   (prompt)
  └── osmo-lerobot-training    (skill)
       ├── references/DEFAULTS.md
       └── references/REFERENCE.md

Dataviewer Developer (agent)
  ├── /start-dataviewer        (prompt)
  └── dataviewer               (skill)
       └── references/PLAYWRIGHT.md

Standalone:
  └── /chatlog                 (prompt, generic)
```

## ➕ Adding New Artifacts

VS Code provides generator commands for scaffolding new artifacts:

- `/create-agent` — Create a new custom agent
- `/create-instruction` — Create a new instruction file
- `/create-prompt` — Create a new prompt file
- `/create-skill` — Create a new agent skill

Place new artifacts in the corresponding `.github/` subdirectory and update
this inventory page.

## Related Documentation

For broader project context, see these companion guides:

- [Contributing Guide](../contributing/README.md) — Development workflow and coding standards
- [Architecture](../contributing/architecture.md) — System architecture and agent skills design
- [Prerequisites](../contributing/prerequisites.md) — Required tools and VS Code settings

---

*Crafted with precision by Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
