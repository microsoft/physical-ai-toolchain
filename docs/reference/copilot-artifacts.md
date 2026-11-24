---
title: Copilot Artifacts
description: >-
  Inventory and reference for GitHub Copilot agents, instructions, and
  skills configured in this repository.
author: Microsoft Robotics-AI Team
ms.date: 2026-10-31
ms.topic: reference
keywords:
  - copilot
  - agents
  - instructions
  - skills
  - extensibility
---

GitHub Copilot extensibility artifacts provide AI-assisted workflows
for dataset analysis, training job management, and coding standards
enforcement. Committed artifacts activate automatically in VS Code.
The cloud-agent setup workflow provisions the RPI skill suite at runtime.

## 📋 Artifact Inventory

| Type        | Name                                  | Description                                         | Path                                                              |
|-------------|---------------------------------------|-----------------------------------------------------|-------------------------------------------------------------------|
| Agent       | Dataviewer Developer                  | Interactive dataset analysis and tool development   | `.github/agents/dataviewer-developer.agent.md`                    |
| Agent       | OSMO Training Manager                 | LeRobot training lifecycle on OSMO with Azure ML    | `.github/agents/osmo-training-manager.agent.md`                   |
| Agent       | Dependabot PR Reviewer                | Dependency update review                            | `.github/agents/dependabot-pr-reviewer.agent.md`                  |
| Agent       | Agentic Workflows                     | GitHub agentic workflow authoring                   | `.github/agents/agentic-workflows.agent.md`                       |
| Instruction | Commit Messages                       | Conventional Commits format for all commit messages | `.github/instructions/commit-message.instructions.md`             |
| Instruction | Dataviewer                            | Coding standards for dataviewer development         | `.github/instructions/dataviewer.instructions.md`                 |
| Instruction | Docs Style and Conventions            | Writing standards for all markdown files            | `.github/instructions/docs-style-and-conventions.instructions.md` |
| Instruction | RPI Tracking                          | Shared RPI working-artifact conventions             | `.github/instructions/hve-core/copilot-tracking.instructions.md`  |
| Instruction | Shell Scripts                         | Implementation standards for bash scripts           | `.github/instructions/shell-scripts.instructions.md`              |
| Skill       | `/chatlog` (workflow)                 | Create and maintain conversation logs               | `.github/skills/chatlog/SKILL.md`                                 |
| Skill       | `/check-training-status` (workflow)   | Monitor OSMO training job progress                  | `.github/skills/check-training-status/SKILL.md`                   |
| Skill       | `/dataviewer-annotate` (workflow)     | Annotate dataset episodes                           | `.github/skills/dataviewer-annotate/SKILL.md`                     |
| Skill       | `/start-dataviewer` (workflow)        | Launch Dataset Analysis Tool                        | `.github/skills/start-dataviewer/SKILL.md`                        |
| Skill       | `/submit-lerobot-training` (workflow) | Submit LeRobot training job to OSMO                 | `.github/skills/submit-lerobot-training/SKILL.md`                 |
| Skill       | azureml-k3s-compute-target-setup      | Attach an Arc-connected K3s GPU cluster to Azure ML | `.github/skills/azureml-k3s-compute-target-setup/SKILL.md`        |
| Skill       | dataviewer                            | Dataset browsing, annotation, and export            | `.github/skills/dataviewer/SKILL.md`                              |
| Skill       | e2e-infrastructure                    | Deploy and undeploy cloud E2E infrastructure        | `.github/skills/e2e-infrastructure/SKILL.md`                      |
| Skill       | e2e-tests                             | Launch and monitor cloud E2E tests                  | `.github/skills/e2e-tests/SKILL.md`                               |
| Skill       | environment-deployment                | Generate and consume environment deployment bundles | `.github/skills/environment-deployment/SKILL.md`                  |
| Skill       | fleet-deployment                      | Deploy trained policies through fleet GitOps        | `.github/skills/fleet-deployment/SKILL.md`                        |
| Skill       | fleet-intelligence                    | Monitor fleet telemetry and drift                   | `.github/skills/fleet-intelligence/SKILL.md`                      |
| Skill       | infrastructure                        | Deploy and manage Azure infrastructure              | `.github/skills/infrastructure/SKILL.md`                          |
| Skill       | osmo-lerobot-training                 | Training submission, monitoring, and analysis       | `.github/skills/osmo-lerobot-training/SKILL.md`                   |
| Skill       | synthetic-data                        | Generate synthetic robotics training data           | `.github/skills/synthetic-data/SKILL.md`                          |
| Skill       | rpi-*                                 | Cloud-agent research, plan, implement, review       | `.github/skills/rpi-*/` (runtime-provisioned, gitignored)         |

## 🔗 Quick Reference

| Want to...                                | Use this artifact                                        |
|-------------------------------------------|----------------------------------------------------------|
| Launch the Dataset Analysis Tool          | `/start-dataviewer` skill → Dataviewer Developer         |
| Browse and annotate training episodes     | Dataviewer Developer agent                               |
| Deploy or remove cloud E2E infrastructure | `e2e-infrastructure` skill                               |
| Run and monitor cloud E2E tests           | `e2e-tests` skill                                        |
| Submit a LeRobot training job             | `/submit-lerobot-training` skill → OSMO Training Manager |
| Check training job status                 | `/check-training-status` skill → OSMO Training Manager   |
| Save a conversation log                   | `/chatlog` skill                                         |
| Run the full RPI lifecycle                | `rpi-quick` skill                                        |
| Enforce commit message standards          | `commit-message` instruction (auto-applied)              |
| Enforce coding standards in dataviewer    | `dataviewer` instruction (auto-applied)                  |
| Enforce markdown writing standards        | `docs-style-and-conventions` instruction (auto-applied)  |
| Enforce shell script standards            | `shell-scripts` instruction (auto-applied)               |

## 🤖 Agents

### Dataviewer Developer

Interactive agent for launching, browsing, annotating, and improving the
Dataset Analysis Tool.

| Property | Value                                               |
|----------|-----------------------------------------------------|
| Handoffs | Start Dataviewer, Browse Dataset, Annotate Episodes |
| Tools    | All (no restrictions)                               |
| Skill    | `dataviewer`                                        |
| Skills   | `/start-dataviewer`, `/dataviewer-annotate`         |

Five-phase workflow: Launch/Configure → Interactive Browsing (Playwright) →
Episode Annotation (API+UI) → Feature Development (React+FastAPI) → VLM-as-Judge Evaluation.

### OSMO Training Manager

Multi-turn agent for managing LeRobot imitation learning training lifecycle
on OSMO with Azure ML integration.

| Property | Value                                                                |
|----------|----------------------------------------------------------------------|
| Handoffs | Submit Training Job, Check Training Status                           |
| Tools    | `execute`, `read`, `edit`, `search`, `agent`, `todo`, `playwright/*` |
| Skill    | `osmo-lerobot-training`                                              |
| Skills   | `/submit-lerobot-training`, `/check-training-status`                 |

Five-phase workflow: Submit → Monitor → Analyze → Summarize → Inference
Evaluation. Handles VM eviction recovery, CUDA errors, and KeyError
failures. The tools are Copilot aliases, so the agent keeps terminal, file,
search, delegation, and todo access in both the Local and Copilot harnesses.
`playwright/*` resolves only when you configure a Playwright MCP server named
`playwright`; see the `dataviewer` skill for an example.

## 📝 Instructions

Instructions activate automatically when files matching their `applyTo`
pattern appear in the chat context.

| Name                       | Applies To                  | Purpose                                                 |
|----------------------------|-----------------------------|---------------------------------------------------------|
| Commit Messages            | `**`                        | Conventional Commits format, scopes, line-length limits |
| Dataviewer                 | `data-management/viewer/**` | SOLID principles, test-first, validation commands       |
| Docs Style and Conventions | `**/*.md`                   | Document hierarchy, tables, voice/tone, frontmatter     |
| Shell Scripts              | `**/*.sh`                   | Script template, library functions, deployment patterns |

## 🛠️ Skills

Skills provide multi-file capabilities with progressive 3-level loading:
discovery (frontmatter only) → instructions (SKILL.md body) → resources
(bundled reference files).

### Workflow skills

Workflow skills are slash commands invoked via `/` in the chat input. They
replace the former prompt files, which the Copilot harness does not load, and
keep the same command names. Each sets `disable-model-invocation: true` so it
runs only when you type `/name`, never on its own. A workflow that belongs to
an agent links that agent's protocol, so it also works from other agents.

| Command                    | Follows               | Arguments                                          |
|----------------------------|-----------------------|----------------------------------------------------|
| `/chatlog`                 | Any agent             | `mode`, `log`, `maintain` (all optional)           |
| `/check-training-status`   | OSMO Training Manager | `workflowId`, `metrics`, `summary` (all optional)  |
| `/start-dataviewer`        | Dataviewer Developer  | `datasetPath`, ports, VLM judge options (optional) |
| `/dataviewer-annotate`     | Dataviewer Developer  | `datasetId` (required), judge options              |
| `/submit-lerobot-training` | OSMO Training Manager | `dataset` (required), training options             |

### dataviewer

| Property  | Value                                                                      |
|-----------|----------------------------------------------------------------------------|
| Directory | `.github/skills/dataviewer/`                                               |
| Resources | `references/PLAYWRIGHT.md` (selectors, interaction recipes, API endpoints) |
| Used by   | Dataviewer Developer agent                                                 |

### e2e-infrastructure

| Property  | Value                                                                        |
|-----------|------------------------------------------------------------------------------|
| Directory | `.github/skills/e2e-infrastructure/`                                         |
| Resources | `scripts/manage-e2e-infrastructure.sh`, `templates/terraform.tfvars.example` |
| Used by   | Copilot sessions managing dedicated cloud E2E infrastructure                 |

Configure user-specific governance, naming, and subscription requirements through the skill's [personal environment overrides](https://github.com/microsoft/physical-ai-toolchain/blob/main/.github/skills/e2e-infrastructure/SKILL.md#personal-environment-overrides).

### e2e-tests

| Property  | Value                                                     |
|-----------|-----------------------------------------------------------|
| Directory | `.github/skills/e2e-tests/`                               |
| Resources | `scripts/run-e2e-tests.sh`                                |
| Used by   | Copilot sessions launching and monitoring cloud E2E tests |

### osmo-lerobot-training

| Property  | Value                                                                                                                  |
|-----------|------------------------------------------------------------------------------------------------------------------------|
| Directory | `.github/skills/osmo-lerobot-training/`                                                                                |
| Resources | `references/DEFAULTS.md` (env, datasets, GPU profiles), `references/REFERENCE.md` (CLI, inference, AzureML navigation) |
| Used by   | OSMO Training Manager agent                                                                                            |

### rpi-* skill suite

The cloud-agent setup workflow uses `gh skill install` to download eight explicit RPI skill paths from a pinned `microsoft/hve-core` commit into gitignored `.github/skills/rpi-*/` directories. It also downloads the shared `copilot-tracking.instructions.md` from the same commit into `.github/instructions/hve-core/`.

Each skill is an immediate child of `.github/skills/`, as required for discovery. Local clones do not contain these runtime artifacts unless the setup workflow has provisioned them. Each skill retains its upstream references and templates, and the GitHub CLI injects the source commit and tree metadata into its `SKILL.md` front matter.

| Skill               | Purpose                                             |
|---------------------|-----------------------------------------------------|
| `rpi-quick`         | Coordinate the complete RPI lifecycle               |
| `rpi-research`      | Gather evidence and produce planning-ready findings |
| `rpi-plan`          | Build and critique an implementation plan           |
| `rpi-implement`     | Execute approved plan phases and validate changes   |
| `rpi-review`        | Review implementation evidence and route follow-ups |
| `rpi-challenger`    | Challenge scope and assumptions                     |
| `rpi-plan-critique` | Assess plans independently                          |
| `rpi-walkthrough`   | Walk through RPI artifacts and decisions            |

## 🔄 Workflow Chains

Agents compose workflow skills and capability skills into end-to-end workflows:

```text
OSMO Training Manager (agent)
  ├── /submit-lerobot-training (skill)
  ├── /check-training-status   (skill)
  └── osmo-lerobot-training    (skill)
       ├── references/DEFAULTS.md
       └── references/REFERENCE.md

Dataviewer Developer (agent)
  ├── /start-dataviewer        (skill)
  ├── /dataviewer-annotate     (skill)
  └── dataviewer               (skill)
       └── references/PLAYWRIGHT.md

Standalone:
  ├── /chatlog                 (skill, generic)
  └── rpi-quick                (skill)
       ├── rpi-research
       ├── rpi-plan
       ├── rpi-implement
       └── rpi-review
```

## ➕ Adding New Artifacts

VS Code provides generator commands for scaffolding new artifacts:

- `/create-agent` — Create a new custom agent
- `/create-instruction` — Create a new instruction file
- `/create-skill` — Create a new agent skill, including user-invoked workflow skills

Place new artifacts in the corresponding `.github/` subdirectory and update
this inventory page.

## Related Documentation

For broader project context, see these companion guides:

- [Contributing Guide](../contributing/README.md) — Development workflow and coding standards
- [Architecture](../contributing/architecture.md) — System architecture and agent skills design
- [Prerequisites](../contributing/prerequisites.md) — Required tools and VS Code settings

---

🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.
