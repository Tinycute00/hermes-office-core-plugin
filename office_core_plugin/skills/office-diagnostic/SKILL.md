---
name: office-diagnostic
description: Read-only diagnostics for the Office Core plugin registration surface.
---

# office-core:office-diagnostic

Use this skill to inspect the Office Core plugin without changing external files,
sending messages, deleting data, or executing office operations.

## Required Inputs

- The user asks for Office Core plugin readiness, tool registration, or safety status.
- Optional sanitized context about the current Hermes plugin load.

## Read-Only Procedure

1. Call `office_diagnostic` to inspect plugin readiness and registered read-only tools.
2. Use `office_plan_workflow` only to draft a plan.
3. Use `office_preview_operation` only to preview metadata for a proposed operation.
4. Report missing command, hook, or skill registration diagnostics as warnings.

## Safety

- Do not call write, send, delete, or external mutation operations.
- Do not include raw tokens, passwords, API keys, authorization headers, or payloads.
- Treat all hook observations as sanitized metadata only.
