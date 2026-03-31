# Quarto Book Documentation Specification

Bookdown-style HTML documentation site for the Physical AI Toolchain, built with [Quarto](https://quarto.org/).

## Overview

This specification describes the Quarto book project that compiles all repository documentation
into a single, navigable HTML book with a bookdown-style layout. The book lives at
`docs/quarto-book/` and coexists with the existing Docusaurus site at `docs/docusaurus/`.

## Architecture

```text
docs/quarto-book/
├── _quarto.yml              # Book configuration (chapters, format, theme)
├── index.qmd                # Landing page with overview, architecture diagram, code examples
├── styles.css               # Bookdown-style theme (serif headings, Azure blue accents)
├── chapters/
│   ├── foundations/         # Educational RL/IL/VLA material (6 chapters)
│   ├── getting-started/     # Quickstart and prerequisites
│   ├── infrastructure/      # Terraform, AKS, VPN, DNS (9 chapters)
│   ├── training/            # Isaac Lab, LeRobot, AzureML, OSMO, MLflow (9 chapters)
│   ├── data/                # Pipeline, chunking, blob storage (3 chapters)
│   ├── synthetic-data/      # Cosmos SDG overview
│   ├── evaluation/          # SIL/HIL evaluation, debugging (4 chapters)
│   ├── fleet-deployment/    # GitOps, FluxCD, gating
│   ├── fleet-intelligence/  # Telemetry, drift detection
│   ├── operations/          # Security guide, troubleshooting
│   ├── recipes/             # Step-by-step walkthroughs (5 chapters)
│   ├── reference/           # GPU config, scripts, deprecation (5 chapters)
│   ├── contributing/        # All contribution guides (13 chapters)
│   ├── security/            # Threat model, release verification
│   └── appendix/            # License
└── _book/                   # Build output (HTML site)
```

## Content Strategy

Each chapter `.qmd` file uses Quarto's `{{< include >}}` shortcode to pull content directly
from the source markdown files in the repository. This avoids duplication and keeps the book
in sync with upstream documentation.

Key overview chapters (Infrastructure, Training, Evaluation, Fleet Deployment) are enriched
with additional content:

- **Mermaid diagrams** for architecture visualization
- **Code chunks** (Bash, Python, HCL) demonstrating real CLI workflows
- **Creative Commons images** from Wikimedia Commons with proper attribution
- **Callout blocks** for warnings, tips, and important notes

## Build Configuration

| Setting | Value |
|---------|-------|
| Quarto version | 1.7.29 |
| Project type | `book` |
| Output format | HTML |
| Light theme | `cosmo` |
| Dark theme | `darkly` |
| Code features | fold, tools, copy, overflow wrap |
| Highlight style | `github` |
| Number sections | Yes |
| TOC depth | 3 |
| Search | Enabled |

## Embedded Assets

### Code Chunks

Non-executable code chunks (`eval: false`) demonstrate toolchain usage:

- **Bash**: Terraform commands, `az ml job create`, OSMO CLI, kubectl
- **Python**: MLflow tracking, evaluation metrics, deployment gating logic
- **Mermaid**: Architecture diagrams, pipeline flows, resource topologies

### Creative Commons Images

All embedded images use Creative Commons licenses from Wikimedia Commons:

| Image | Source | License |
|-------|--------|---------|
| Industrial robot arm | KUKA Roboter GmbH | CC BY-SA 3.0 |
| Data center servers | Florian Hirzinger | CC BY-SA 3.0 |
| ASIMO humanoid robot | Vanillase | CC BY-SA 3.0 |
| Baxter research robot | Humanrobo | CC BY-SA 3.0 |
| Automated guided vehicle | Grendelkhan | CC BY-SA 3.0 |
| CC BY 4.0 badge | Creative Commons | CC BY 4.0 |

## Build Instructions

### Prerequisites

- Quarto CLI >= 1.7 ([install guide](https://quarto.org/docs/get-started/))

### Build

```bash
cd docs/quarto-book
quarto render
```

Output is written to `docs/quarto-book/_book/index.html`.

### Preview

```bash
cd docs/quarto-book
quarto preview
```

Opens a local development server with live reload.

## Hosting & Deployment

The Quarto book is hosted alongside the existing Docusaurus documentation site on GitHub Pages.

| Property | Value |
|----------|-------|
| **Book URL** | `https://microsoft.github.io/physical-ai-toolchain/book/` |
| **Docs URL** | `https://microsoft.github.io/physical-ai-toolchain/` |
| **Workflow** | `.github/workflows/deploy-docs.yml` |
| **Trigger** | Push to `main` on `docs/**` or manual dispatch |

### How It Works

The `deploy-docs.yml` workflow builds both sites and merges them into a single deployment artifact:

1. **Docusaurus build** → `docs/docusaurus/build/`
2. **Quarto build** → `docs/quarto-book/_book/`
3. **Merge** → `cp -r docs/quarto-book/_book docs/docusaurus/build/book`
4. **Deploy** → `actions/deploy-pages` uploads `docs/docusaurus/build/` (includes `/book/`)

The Docusaurus navbar includes a **"📖 Book"** link pointing to the Quarto book subpath.

### Site URL Configuration

`_quarto.yml` sets `site-url: https://microsoft.github.io/physical-ai-toolchain/book/` so that
internal navigation, search, and social sharing links resolve correctly at the subpath.

## Theme Customization

The `styles.css` file provides bookdown-style visual treatment:

- Serif heading font (`Source Serif Pro`) with sans-serif body (`Source Sans Pro`)
- Microsoft Azure blue (`#0078d4`) link color and code block left-border
- NVIDIA green (`#76b900`) part header accents
- Dark mode support via Bootstrap data-theme
- Print-friendly styles that hide sidebar and metadata

## Chapter Count

| Part | Chapters |
|------|----------|
| Foundations of Robot Learning | 6 |
| Getting Started | 2 |
| Infrastructure | 9 |
| Training | 9 |
| Data Management | 3 |
| Synthetic Data | 1 |
| Evaluation | 4 |
| Fleet Deployment | 1 |
| Fleet Intelligence | 1 |
| Operations | 2 |
| Recipes | 5 |
| Reference | 5 |
| Contributing | 13 |
| Security | 2 |
| Appendix | 1 |
| **Total** | **64 + index** |

## Foundations of Robot Learning (Educational Chapters)

Six standalone chapters introduce core ML/RL concepts for robotics engineers:

| Chapter | Topics | Key Assets |
|---------|--------|------------|
| Markov Decision Processes | MDP formalism, Bellman equations, value functions, POMDPs | Gridworld value iteration code, state-transition Mermaid diagram, LaTeX notation |
| Reward Functions & Shaping | Sparse/dense rewards, potential-based shaping, reward hacking, multi-objective | Reach-task reward code, reward strategy comparison table |
| Simulation for Robotics | Physics engines, Isaac Sim architecture, domain randomization, sim-to-real | Isaac Lab env code, simulator comparison table |
| Reinforcement Learning | PPO deep dive, algorithm families, on/off-policy, sample efficiency | SKRL config code, algorithm comparison table |
| Imitation Learning | Behavioral cloning, DAgger, ACT, Diffusion Policy, dataset requirements | LeRobot ACT config code, IL method comparison table |
| Vision-Language-Action Models | RT-2, Octo, OpenVLA, π0, architecture, fine-tuning, zero-shot generalization | VLA inference pseudocode, model comparison table |

Each chapter includes LaTeX math notation, Mermaid diagrams, Python code examples, CC-licensed Wikimedia images, and callouts connecting concepts to the Physical AI Toolchain.

## Expanded Overview Chapters

Four thin overview chapters were rewritten with thorough standalone content:

| Chapter | Original | Expanded | Content Added |
|---------|----------|----------|---------------|
| Fleet Deployment | 27 words | ~1,200 words | GitOps pipeline, FluxCD config, gating service, canary deployments |
| Data Pipeline | 103 words | ~1,300 words | ROS 2 capture, MCAP format, Arc agent, conversion pipeline |
| Synthetic Data | 164 words | ~1,250 words | Cosmos Transfer/Predict/Reason, domain randomization, OSMO workflows |
| Fleet Intelligence | 211 words | ~1,450 words | MQTT telemetry, drift detection, Grafana dashboards, retraining triggers |

## Known Limitations

- Cross-document relative links in included markdown produce warnings (links still work as anchor fragments)
- Images referenced by relative path in source docs may not resolve in the book context
- Code chunks are display-only (`eval: false`) — no live execution
- Quarto must be installed separately (not in npm/pip project dependencies)

## Future Improvements

- Add a `quarto publish gh-pages` GitHub Actions workflow
- Resolve cross-document link warnings by adding a link resolution preprocessor
- Add downloadable PDF/EPUB outputs via `format: pdf` and `format: epub`
- Integrate with the existing Docusaurus CI for unified documentation deployment
