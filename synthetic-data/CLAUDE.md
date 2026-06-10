# CLAUDE.md

You are working in the synthetic data generation folder designed for
creating and running a synthetic data generation workflow per session.
Prioritize accurate configuration, reliable workflow generation, robust
workflow execution, and explicit verification over speed.

## Before You Begin

1. Run `pwd` and confirm you are in the expected `synthetic-data` folder.
2. Skills are provided to you in the `.claude/skills` folder.
3. Do not touch existing files in the repo except the `output` folder.
4. Ask the user if they want to start a new workflow or resume an existing one.
To start a new workflow, create a folder with the current date time in `output/YYYY-MM-DD-HH-mm`. To resume an existing one, ask the user to provide the output folder.
In both cases, this is where you will store all the generated workflow artifacts.
5. Relentlessly interview the user until you both reach agreement on the following:
  - compute infra (OSMO 6.3 cluster) is up and running and can scale to the required GPUs
  - the use case to target
  - what to augment or generate
  - you have access to data and cloud resources to run compute or read/write data
6. Record your agreement in the output folder in a file called `blueprint.json` using
  the template in `.ai-reference/blueprint.json`.

## Do Your Work

- Break down your work into the following stages:
  - Stage-1: ensure compute cluster is running and storage is accessbile
  - Stage-2: ensure data is available and accessible
  - Stage-3: create workflow configuration and yaml
  - Stage-4: pause here and ask the user to review and approve the artifacts
  - Stage-5: only when you reach here, scale up the GPUs, run workflow and
  monitor until succeed or failure
  - Stage-6: verify generated data is available
- Record your progress after each stage is completed in the output folder in a
file called `progress.json` using the template in `.ai-reference/progress.json`.
- Do not proceed to the next stage until the current stage is completed with
evidence recorded.

## Before You Stop

1. If workflow completed, check if data is generated as specified
in `blueprint.json` and update `progress.json`.
2. If workflow didn't complete within a timeout, summarize what's done in
`progress.json`, then pause and notify the user.
3. Ask the user if you should scale down the GPUs.
