---
name: chatlog
description: 'Create and maintain structured conversation chatlogs capturing problems, solutions, and lessons learned. Use when the user runs /chatlog.'
argument-hint: "mode={create|continue} log=<chatlog-name> maintain={true|false}"
disable-model-invocation: true
---
# Conversation Chatlog Manager

Manage conversation details by creating and maintaining structured chatlog files that capture problem statements, solutions, technical details, and lessons learned. This skill enables both creating new chatlogs and continuing conversations with existing chatlog context.

## Arguments

Parse arguments from the request as `name=value` pairs. Use the default when an argument is omitted.

* `mode` (default `create`): Mode of operation - `create` to start a new chatlog, `continue` to work with an existing chatlog
* `log` (required when `mode=continue` and a *-chatlog.md was not attached): Chat log file name, path, or relative name (e.g., `.copilot-tracking/chatlogs/20251119-azureml-job-submission-chatlog.md`, `20251119-azureml-job-submission`, `azureml-job-submission`)
* `maintain` (default `true`): Whether to continue updating the chatlog with new details from the ongoing conversation (true/false)

## Protocol

### Phase 1 - Determine Operation Mode

* Check the `mode` input to determine if this is a new chatlog or continuing an existing one
* If `mode=create`:
  * Analyze the conversation to determine a concise `briefDescription` (use kebab-case, max 4-5 words)
  * Generate chatlog filename: `.copilot-tracking/chatlogs/YYYYMMDD-{briefDescription}-chatlog.md` where YYYYMMDD is today's date (format: 20251119)
  * Proceed to Phase 2
* If `mode=continue`:
  * Check if a *-chatlog.md file is attached; if so, use that file
  * Otherwise, validate `log` input is provided
  * Resolve the chatlog file path:
    * If full path provided (starts with `.copilot-tracking/`), use as-is
    * If date prefix provided (e.g., `20251119-azureml-job-submission`), prepend `.copilot-tracking/chatlogs/` and append `-chatlog.md`
    * If brief name only (e.g., `azureml-job-submission`), search `.copilot-tracking/chatlogs/` for matching `*-{name}-chatlog.md`
  * Read the existing chatlog file to understand context
  * Proceed to Phase 3

### Phase 2 - Create New Chatlog (mode=create)

* Create the `.copilot-tracking/chatlogs/` directory if it doesn't exist
* Generate a new chatlog file with the following structure:

````markdown
# [Descriptive Title]

**Date**: YYYY-MM-DD (e.g., November 19, 2025)
**Branch**: [current-branch-name]
**Status**: 🔄 In Progress | ✅ Resolved | ⚠️ Blocked

## Problem Statement

[Clear description of the issue or question being addressed]

## Root Cause

[Technical analysis of what caused the issue - omit if not applicable]

## Solution

### [Solution Component 1]

[Detailed solution with explanations]

#### Implementation

[Code examples, commands, or configuration changes]

## Key Technical Details

### [Concept/Pattern Name]

[Important concepts, patterns, or approaches discovered]

## Testing Results

[Verification steps and outcomes]

## Commands for Reference

### [Command Category]

```bash
# Command description
command here
```

## Related Documentation

* [Link Title](https://example.com)

## Follow-up Issues

### Issue: [Brief Title]

**Problem**: [Description]
**Solution**: [Resolution]

## Lessons Learned

1. [Key takeaway with brief explanation]
2. [Another key takeaway]

````

* Populate the chatlog with details from the current conversation context
* Follow markdown linting rules strictly:
  * Add blank lines around all headings
  * Add blank lines around all code fences
  * Add blank lines around all lists
  * Specify language for all code blocks (use `text` if no specific language)
  * Avoid duplicate heading names (use modifiers like "Problem Description", "Cause", "Resolution")
  * Use H2 (`##`) for main sections, H3 (`###`) for subsections, H4 (`####`) for sub-subsections
* Inform the user of the created chatlog location with full path

### Phase 3 - Continue with Existing Chatlog (mode=continue)

* Read and parse the existing chatlog file completely
* Extract and summarize the key points from the chatlog:
  * **Title and Context**: Main topic and current status
  * **Original Problem**: What issue was being addressed
  * **Solution Applied**: How it was resolved
  * **Key Technical Details**: Important patterns or approaches discovered
  * **Outstanding Issues**: Any unresolved items or next steps mentioned
* Present a concise summary to the user in this format:

```text
📋 Chatlog Summary: [Title]

**Original Problem**: [One-sentence description]
**Solution**: [Brief summary of approach]
**Status**: [Current status from chatlog]
**Key Points**:
- [Top technical insight 1]
- [Top technical insight 2]

Ready to continue. What would you like to work on?
```

* Proceed with the conversation based on user's response

### Phase 4 - Maintain Chatlog (if maintain=true)

* Throughout the conversation, identify significant developments:
  * New problems or errors encountered
  * Solutions implemented
  * Technical insights discovered
  * Commands executed successfully
  * Configuration changes made
  * Follow-up issues resolved
* Update the appropriate sections of the chatlog:
  * Add new "Follow-up Issue" sections for additional problems
  * Append to "Commands for Reference" section
  * Add to "Lessons Learned" list
  * Update status if resolved
* After each significant update, briefly confirm what was added to the chatlog

### Phase 5 - Quality Assurance

* Validate the chatlog follows proper markdown formatting:
  * All code blocks have language specified
  * Blank lines surround headings, lists, and code blocks
  * No duplicate heading names at the same level
  * Proper heading hierarchy (H2 for main sections, H3/H4 for subsections)
* Ensure all commands and code examples are accurate and complete
* Verify links to documentation are valid and relevant

## Response Structure

### Initial Response (mode=create)

Format:

```text
✅ Created chatlog: .copilot-tracking/chatlogs/YYYYMMDD-brief-description-chatlog.md

**Captured**:
- Problem: [brief description]
- Solution approach: [if applicable]
- Status: [current state]

**Maintenance**: [Enabled/Disabled based on maintain input]

[Proceed with addressing the user's question or problem]
```

### Initial Response (mode=continue)

Use the format specified in Phase 3 above, then proceed with the conversation.

### Update Confirmations (when maintain=true)

* After significant updates, provide a brief inline note: "📝 Updated chatlog: [section] - [what was added]"
* Keep confirmations to one line and non-intrusive
* Do not interrupt the flow of technical discussion with lengthy chatlog updates

### Final Summary (optional, at conversation end)

Format:

```text
📋 Chatlog Summary

**File**: .copilot-tracking/chatlogs/[filename]
**Status**: [Updated status]
**Sections Added/Updated**:
- [Section 1]: [brief description]
- [Section 2]: [brief description]

**Key Additions**:
* [Most important new insight/solution]

```

---

Proceed with determining the operation mode and following the appropriate protocol phases. Create or read the chatlog as needed, and be ready to maintain it throughout the conversation if requested.
