---
title: "Physical AI Toolchain Roadmap"
description: "Project roadmap covering documentation, testing, CI/CD, governance, security, and OpenSSF compliance through Q1 2027."
author: wberry
ms.date: 2026-02-10
ms.topic: reference
keywords:
  - roadmap
  - project planning
  - openssf
  - azure
  - nvidia
  - robotics
estimated_reading_time: 8
---

This roadmap covers planned work for the Physical AI Toolchain through Q1 2027.
Six priority areas align to milestones v0.2.0 through v0.7.0, progressing from documentation through security hardening.
An additional v0.8.0 milestone covers dependency update automation.
The Architecture Domain Rollout priority phases the repository reorganization around eight lifecycle domains defined in the [Future and Ongoing Architecture](architecture.md#future-and-ongoing-architecture).
Each area lists concrete deliverables with linked issues and explicit items we will not pursue.

> [!NOTE]
> This roadmap represents current project intentions and is subject to change.
> It is not a commitment or guarantee of specific features or timelines.
> Community feedback and contributions influence priorities.
> See [How to Influence the Roadmap](#how-to-influence-the-roadmap) for ways to participate.

## Current State

The project reached v0.1.0 on 2026-02-07 with 30 commits on main and 24 merged pull requests.
Seven milestones are planned through v0.8.0, spanning foundation work through security hardening.
OpenSSF Best Practices Passing criteria are approximately 85% met (43 Met, 7 Partial, 12 Gap, 6 N/A).

## Priorities

### Documentation and Contributing (v0.2.0, Q1 2026)

Complete the contributing guide suite and establish maintenance policies.
This milestone closes the remaining documentation gaps identified during the OpenSSF Passing assessment.

**Will Do:**

* Expand developer setup and prerequisites (#89)
* Define security expectations for contributors (#91)
* Publish a maintenance and upgrade policy (#92, #102)
* Commit to 48-hour achievement update cadence (#93)
* Add accessibility guidelines for documentation (#94)
* Standardize install and uninstall conventions (#95)

**Won't Do:**

* API reference documentation (this is a reference architecture, not a library)
* Automated documentation generation from source code

### Core Scripts and Utilities (v0.3.0, Q2 2026)

Standardize linting infrastructure across shell, Python, and markdown.
Shared modules reduce duplication and enforce consistent quality gates.

**Will Do:**

* Implement verified downloads with hash checking (#54)
* Create testing directory structure and runner (#55)
* Add YAML and GitHub Actions linting (#56)
* Implement frontmatter validation (#57)
* Enable dependency pinning and scanning (#58)
* Migrate shared linting modules from hve-core (#68, #69)
* Standardize `os.environ` usage patterns (#130)

**Won't Do:**

* Custom linting rule development beyond existing tools
* IDE-specific plugin creation

### Testing Infrastructure (v0.4.0, Q2 2026)

Stand up pytest and Pester test frameworks with coverage reporting and CI integration.
Baseline test suites validate training utilities and CI helper modules.

**Will Do:**

* Configure pytest with baseline test directory (#80)
* Add unit tests for training utilities (#82)
* Enable coverage reporting (#83)
* Create pytest CI workflow (#81)
* Configure Pester with shared test helpers (#63)
* Write Pester tests for CIHelpers, linting, security, and download modules (#64, #65, #66, #67)
* Establish regression test requirements for bug fixes (#107)

**Won't Do:**

* End-to-end deployment tests (cost-prohibitive in CI)
* GPU-dependent tests in CI pipelines

### CI/CD and Workflows (v0.5.0, Q2 2026)

Expand CI pipelines with Python linting, security scanning, and workflow orchestration.
CodeQL and Bandit scanning catch vulnerabilities before merge.

**Will Do:**

* Configure Ruff linter with project rules (#85, #86)
* Resolve existing Ruff violations (#87)
* Add Bandit security scanning (#88)
* Enable CodeQL on pull request triggers (#84)
* Mirror PR validation across branches (#71)
* Orchestrate new CI jobs into existing workflows (#70)
* Port remaining workflows from hve-core (#20)

**Won't Do:**

* Deployment automation requiring Azure credentials in CI
* External registry or package releases

### Governance and OpenSSF (v0.6.0, Q2 2026)

Formalize project governance and complete OpenSSF Passing badge criteria.
N/A justifications document criteria that do not apply to this project.

**Will Do:**

* Publish a governance model (#98)
* Define contributor and maintainer roles (#99)
* Document access continuity procedures (#100)
* Address bus factor with succession planning (#101)
* Establish DCO or CLA requirements (#97)
* Add vulnerability credit policy (#103)
* Document reused software components (#104)
* Define deprecated interface conventions (#105)
* Enable strict compiler warnings equivalents (#106)
* File N/A justifications for build, install, crypto, site password, i18n, and dynamic analysis criteria (#113, #114, #115, #116, #117, #118)
* Register for OpenSSF Passing badge (#96)

**Won't Do:**

* External security audit engagements
* Paid compliance tooling subscriptions

### Security and Hardening (v0.7.0, Q2 2026)

Implement release integrity, input validation, and threat modeling.
OpenSSF Scorecard integration provides continuous security measurement.

**Will Do:**

* Integrate OpenSSF Scorecard with automated reporting (#60)
* Establish weekly security maintenance cadence (#61)
* Detect and remediate SHA staleness (#59)
* Implement release signing and verification (#108, #109)
* Add input validation for scripts and CI parameters (#110)
* Publish hardening guidance for deployment (#111)
* Create an assurance case and threat model (#112)

**Won't Do:**

* Penetration testing engagements
* Hardware security module (HSM) integration

### Architecture Domain Rollout (Q2 2026 – Q3 2026)

All future enhancements, features, and functionality align to the eight lifecycle domains defined in the [Future and Ongoing Architecture](architecture.md#future-and-ongoing-architecture). Each domain represents a distinct functional concern in the physical AI lifecycle, from data capture through fleet-scale production monitoring. New work items target a specific domain and follow the patterns, specifications, and directory structure established in the architecture.

Domains roll out in three phases based on dependency order and infrastructure readiness:

| Phase | Timeline | Domains                                        |
|-------|----------|------------------------------------------------|
| 1     | Q2 2026  | Infrastructure, Training, Evaluation           |
| 2     | Q2 2026  | Data Pipeline, Data Management, Synthetic Data |
| 3     | Q3 2026  | Fleet Deployment, Fleet Intelligence           |

Phase 1 migrates existing `deploy/` and `src/` content into the Infrastructure and Training domains, then establishes the Evaluation domain for SiL and HiL validation pipelines. Phase 2 introduces data capture from physical robots, episodic data curation, and synthetic data generation using NVIDIA Cosmos. Phase 3 adds edge deployment through FluxCD GitOps and production telemetry through Azure IoT Operations and Fabric Real-Time Intelligence.

**Will Do:**

* Migrate existing Terraform IaC from `deploy/001-iac/` into `infrastructure/terraform/`
* Migrate existing setup scripts from `deploy/002-setup/` into `infrastructure/setup/`
* Reorganize `src/training/` and `src/inference/` into `training/rl/`, `training/il/`, and `training/vla/`
* Establish `evaluation/sil/` and `evaluation/hil/` with Isaac Sim-based validation pipelines
* Create `data-pipeline/` with Azure Arc edge agent setup and ROS 2 episodic capture
* Create `data-management/` with LeRobot-based episodic data viewer and curation tooling
* Create `synthetic-data/` with NVIDIA Cosmos Transfer, Predict, and Reason integration
* Create `fleet-deployment/` with FluxCD GitOps manifests and policy gating service
* Create `fleet-intelligence/` with Azure IoT Operations telemetry and Fabric RTI dashboards
* Add Agent Skill specification documents (`specifications/`) in each domain directory
* Add simulation guidance documentation under `docs/simulation/`

**Won't Do:**

* Maintain a separate simulation code domain (NVIDIA provides comprehensive OSS tooling)
* Build custom robot hardware drivers or firmware
* Implement production SLA monitoring beyond reference dashboard examples

## Out of Scope

* Production SLA or uptime guarantees
* Multi-cloud support
* Custom robot hardware integration guides
* Paid support tiers or enterprise licensing
* Backward compatibility guarantees for infrastructure modules
* Automated deployment pipelines for end users

## Success Metrics

| Metric                       | Current | Q2 2026 Target | Q4 2026 Target |
|------------------------------|---------|----------------|----------------|
| OpenSSF Passing criteria met | ~85%    | 95%            | 100%           |
| OpenSSF Silver criteria met  | ~30%    | 50%            | 80%            |
| Test coverage (Python)       | 0%      | 60%            | 80%            |
| CI workflow count            | 4       | 8              | 10             |
| Contributing guide count     | 7       | 8              | 9              |
| Average PR review time       | N/A     | < 3 days       | < 2 days       |

## Timeline Overview

```text
Q1 2026 (Jan-Mar) — Foundation
├── Documentation: Complete v0.2.0 contributing guide suite (roadmap, security, maintenance policies)
└── Release: v0.2.0 (due 2026-02-13)

Q2 2026 (Apr-Jun) — Quality, Governance, Security, and Domain Rollout Phases 1-2
├── Core Scripts: Verified downloads, linting standardization, frontmatter validation (v0.3.0, due Apr 14)
├── Testing: pytest + Pester infrastructure, coverage reporting, CI integration (v0.4.0, due Apr 30)
├── CI/CD: Ruff, Bandit, CodeQL PR triggers, workflow orchestration (v0.5.0, due May 14)
├── Governance: Governance model, roles, DCO/CLA, OpenSSF N/A documentation (v0.6.0, due May 31)
├── Security: OpenSSF Scorecard, release signing, threat model, hardening guidance (v0.7.0, due Jun 14)
├── Dependencies: Dependency update automation (v0.8.0, due Jun 30)
├── Infrastructure: Migrate deploy/ IaC and setup scripts into infrastructure/
├── Training: Reorganize src/ into training/rl/, training/il/, training/vla/
├── Evaluation: Establish SiL and HiL validation pipelines
├── Data Pipeline: Azure Arc edge agents and ROS 2 episodic capture
├── Data Management: LeRobot-based episodic data viewer and curation
├── Synthetic Data: NVIDIA Cosmos Transfer, Predict, and Reason pipelines
└── Release: v0.3.0, v0.4.0, v0.5.0, v0.6.0, v0.7.0, v0.8.0

Q3 2026 (Jul-Sep) — Domain Rollout Phase 3
├── Fleet Deployment: FluxCD GitOps manifests and policy gating on Arc-enabled Kubernetes
├── Fleet Intelligence: Azure IoT Operations telemetry and Fabric RTI dashboards
├── Architecture: Agent Skill specification documents for all domains
├── OpenSSF: Complete Silver attestation
├── Platform: Azure and NVIDIA integration updates (OSMO workload identity)
└── Community: External contributor onboarding, maintainer documentation

Q4 2026 (Oct-Dec) — Growth
├── Community: Conference presentations, partner integrations
├── Roadmap: Publish updated 2027-2028 roadmap
└── Architecture: Production deployment guides, performance benchmarking

Q1 2027 (Jan-Mar) — Sustainability
├── OpenSSF: Begin Gold-level assessment and gap analysis
└── Community: Adoption case studies, contributor growth initiatives
```

## How to Influence the Roadmap

* Open an issue describing the feature or improvement you need.
* Comment on existing issues to share use cases or signal priority.
* Join GitHub Discussions to propose broader changes.
* Submit a pull request referencing an open issue.
* Provide feedback on in-progress milestones through issue comments.

## Version History

| Date       | Version | Notes                                                    |
|------------|---------|----------------------------------------------------------|
| 2026-02-10 | 1.0     | Initial 12-month roadmap                                 |
| 2026-02-10 | 1.1     | Extend timeline to Q1 2027 for OpenSSF 12-month coverage |
| 2026-02-24 | 1.2     | Add Architecture Domain Rollout priority and timeline    |
