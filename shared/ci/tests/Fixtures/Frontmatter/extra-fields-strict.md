---
description: Instruction with extra disallowed fields
applyTo: "**/*.py"
extraField: this-should-not-be-here
---

# Extra Fields Test

This instruction file has an additional property that should fail schema validation
when additionalProperties is false.
