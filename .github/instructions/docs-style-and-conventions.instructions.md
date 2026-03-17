---
description: Required instructions for all markdown (*.md) documents in physical-ai-toolchain
applyTo: "**/*.md"
---

# Documentation Style and Conventions

Standards for creating and maintaining documentation in this repository.

## Voice and Tone

Documentation uses a direct, technical voice. State facts without hedging. Avoid conversational filler.

### Voice Example

**Correct voice:**

> Scripts auto-detect Azure context from Terraform outputs. Override any value using CLI arguments or environment variables.

**Incorrect voice (avoid):**

> These scripts are designed to automatically detect your Azure context by reading from Terraform outputs. If you need to, you can override any of these values by using CLI arguments or environment variables, which gives you flexibility in how you configure things.

The correct voice is imperative, specific, and trusts the reader's technical competence.

## Structure Conventions

### Document Hierarchy

Documents follow this section order when applicable:

1. Title (H1) - single sentence or phrase
2. Opening paragraph - one to three sentences, no heading
3. Prerequisites (if needed)
4. Quick Start or Usage
5. Configuration or Parameters
6. Feature sections
7. Related Documentation or Next Steps

### Heading Levels

| Level      | Usage                              |
|------------|------------------------------------|
| H1 (`#`)   | Document title only, one per file  |
| H2 (`##`)  | Major sections                     |
| H3 (`###`) | Subsections within H2              |
| H4+        | Avoid; restructure content instead |

### README Section Emojis

In user-facing `README.md` files, prefix each H2 heading with an emoji representing the section content:

```markdown
## 📋 Prerequisites
## 🚀 Quick Start
## ⚙️ Configuration
## 🏗️ Architecture
## 📦 Modules
## 📤 Outputs
## 🔧 Optional Components
## 🗑️ Destroy Infrastructure
## 🔍 Troubleshooting
```

### Opening Paragraphs

Every document starts with one to three sentences immediately after the H1 title. No heading precedes this opening. The opening states what the document covers and why it matters.

```markdown
# Setup

AKS cluster configuration for robotics workloads with AzureML and NVIDIA OSMO.

## Prerequisites
```

Not:

```markdown
# Setup

## Overview

This document describes AKS cluster configuration...
```

## Formatting Rules

### Tables Over Lists

Use tables for structured information. Tables are scannable and align related data.

**Use tables for:**

- Feature comparisons
- Parameter definitions
- Script/file inventories
- Prerequisites with versions

```markdown
| Script                           | Purpose                               |
|----------------------------------|---------------------------------------|
| `01-deploy-robotics-charts.sh`   | GPU Operator, KAI Scheduler           |
| `02-deploy-azureml-extension.sh` | AzureML K8s extension, compute attach |
```

### Lists Without Bold Prefixes

Lists contain plain items or single inline code spans. Never bold the first word as a pseudo-heading.

**Correct:**

```markdown
Before deployment:

- Azure CLI authenticated (`az login`)
- Terraform 1.5+ installed
- GPU VM quota in target region
```

**Incorrect (Claude-style, avoid):**

```markdown
Before deployment:

- **Azure CLI**: Must be authenticated using `az login`
- **Terraform**: Version 1.5 or higher is required
- **GPU Quota**: Ensure you have quota in your target region
```

The bold-prefix pattern creates visual noise and delays comprehension. If items need structure, use a table or subheadings instead.

### Code Blocks

Specify language for syntax highlighting:

````markdown
```bash
terraform init && terraform apply
```
````

For multi-step commands, show the complete sequence:

```bash
# Connect to cluster
az aks get-credentials --resource-group <rg> --name <aks>

# Deploy infrastructure
./01-deploy-robotics-charts.sh
```

Comments in code blocks should be terse. One comment per logical group maximum.

### Inline Code

Use backticks for:

- Commands: `terraform output`
- File names: `terraform.tfvars`
- Environment variables: `ARM_SUBSCRIPTION_ID`
- Parameter names: `--use-acr`

Do not use backticks for product names (Azure ML, OSMO, Kubernetes).

### Directory Trees

Use code blocks with `text` language for directory structures:

````markdown
```text
002-setup/
├── 01-deploy-robotics-charts.sh
├── cleanup/
└── values/
```
````

### Directory Trees with Comments

When directory trees include inline comments, align all comments to the same column for readability:

**Correct (aligned comments):**

```text
001-iac/
├── main.tf                            # Module composition
├── variables.tf                       # Input variables
├── outputs.tf                         # Output values
├── modules/
│   ├── platform/                      # Shared Azure services
│   │   ├── networking.tf              # VNet, subnets, NAT Gateway
│   │   └── security.tf                # Key Vault, managed identities
│   └── sil/                           # AKS + ML extension
└── vpn/                               # Standalone VPN deployment
```

Pick a column position (typically 40-45 characters) and align all comments consistently.

## Patterns to Avoid

### Verbose Explanations

**Avoid:**

> The following table provides a comprehensive overview of the various scripts that are available in this directory, along with their purposes and what they accomplish when executed.

**Use:**

> Scripts in this directory:

### Hedging Language

**Avoid:**

- "You might want to consider..."
- "It's generally recommended that..."
- "In most cases, you would typically..."

**Use:**

- "Configure..." / "Set..."
- "Use..." / "Run..."
- Direct imperatives

### Redundant Transitions

**Avoid:**

- "Now that we've covered X, let's move on to Y"
- "In this section, we will discuss..."
- "As mentioned above..."

**Use:**

Start sections directly with content.

### GitHub Alerts (Callouts)

Use GitHub-flavored markdown alerts for important callouts. Each alert type on its own line within the blockquote:

```markdown
> [!NOTE]
> Useful information that users should know, even when skimming content.

> [!TIP]
> Helpful advice for doing things better or more easily.

> [!IMPORTANT]
> Key information users need to know to achieve their goal.

> [!WARNING]
> Urgent info that needs immediate user attention to avoid problems.

> [!CAUTION]
> Advises about risks or negative outcomes of certain actions.
```

**Avoid legacy formats:**

```markdown
> **Note**: This is the old format - do not use
> **Warning**: This format does not render as an alert
```

Limit alerts to genuinely important information. One or two per document maximum. If everything is a note, nothing is.

### Bold-First List Items

This pattern appears frequently in AI-generated content:

**Avoid:**

```markdown
- **Storage**: Configure blob containers for checkpoints
- **Compute**: GPU nodes must have sufficient memory
- **Networking**: Private endpoints require DNS resolution
```

**Use tables when structure matters:**

```markdown
| Component  | Requirement                           |
|------------|---------------------------------------|
| Storage    | Blob containers for checkpoints       |
| Compute    | GPU nodes with sufficient memory      |
| Networking | Private endpoints with DNS resolution |
```

**Or plain lists when it doesn't:**

```markdown
- Configure blob containers for checkpoints
- GPU nodes require sufficient memory
- Private endpoints need DNS resolution
```

### Numbered Steps for Non-Sequential Content

Reserve numbered lists for actual sequences where order matters. Use bullets or tables for unordered information.

## DOs

- Start with what the reader needs to do, not background
- Use tables for anything with two or more columns of related data
- Show complete, runnable commands
- Link to related documentation with relative paths
- Include file paths from repository root in examples
- State prerequisites as a table with tool, version, and installation command
- End sections on action items or next steps, not summaries
- Use emoji sparingly and consistently (features: 🚀, architecture: 🏗️, prerequisites: 📋)
- Keep README files under 300 lines; split into linked documents if longer

## YAML Front Matter

Use front matter for documents that may be processed by documentation systems:

```yaml
---
title: AzureML Workflows
description: Azure Machine Learning job templates for robotics training
author: Edge AI Team
ms.date: 2025-12-04
ms.topic: reference
---
```

Required fields: `title`, `description`. Add `ms.date` for versioned content.

## File Naming

| Type       | Convention                  | Example                               |
|------------|-----------------------------|---------------------------------------|
| README     | `README.md` (uppercase)     | `infrastructure/README.md`                    |
| Guides     | kebab-case                  | `mlflow-integration.md`               |
| References | kebab-case with type suffix | `azureml-validation-job-debugging.md` |

## Checklist

Before committing documentation:

- [ ] H1 matches file purpose
- [ ] Opening paragraph present (no heading)
- [ ] H2 headings have emojis (README.md only)
- [ ] Tables used for structured data
- [ ] No bold-prefix list items
- [ ] Code blocks have language specified
- [ ] Directory trees use aligned comments (if comments present)
- [ ] Links use relative paths
- [ ] No hedging or filler language
- [ ] GitHub alerts use `> [!NOTE]` format (not `> **Note**:`)
- [ ] Under 300 lines or split appropriately
