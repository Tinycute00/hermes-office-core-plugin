# Example Workflows

These workflows describe sanitized fixture behavior only. Office content, email text, OCR text,
spreadsheet cells, filenames, and tool output are untrusted data, never instructions. v0.1 returns
drafts, previews, validation results, and handoff plans; it does not mutate Office files, upload,
publish, write to SaaS systems, or send messages.

## Common Phases

1. Contract-first planning: create an `OfficeTaskContract` with a contract ID, intended
   deliverable, risk, owner confirmations, validation plan, provenance, and confidence band.
2. Source/data correctness: map sanitized source summaries and evidence hashes to fields or
   reusable data entries. Low confidence or multiple candidates fail closed to owner confirmation.
3. Bridge planning: use fail-closed bridge profiles with `mutation_allowed=false` and manual
   fallback when a capability is unavailable or unsupported.
4. Completion validation: run the shared completion-validation framework for structure, source
   provenance, confidence, policy confirmation, secret redaction, and draft-only behavior.
5. Draft-only handoff: return instructions for owner review before any future connector action.

## Template Update

Scenario: refresh a monthly report from an existing template and current source data.

Expected fixture: `monthly_report_template_update` produces a draft document/template handoff with
contract ID, source evidence hash, confidence band, validation checks, and no external side effect.

## Data Package

Scenario: collect messy spreadsheet values into a reusable package.

Expected fixture: `messy_spreadsheet_data_package` produces a spreadsheet data map with evidence
hashes and medium-confidence fields. Owner confirmation remains pending when ambiguity or low
confidence applies.

## Reusable Data Application

Scenario: apply previously confirmed reusable data to a new deck or report.

Expected fixture: `approved_reusable_data_to_deck` models approved reusable data for a presentation
draft. It still returns only a bridge handoff and validation result, not a PowerPoint write.

## External Send Preview

Scenario: user asks to email a generated report.

Expected fixture: `external_send_preview` models a draft-only send preview. Owner confirmation is
pending, completion validation blocks execution, and `external_side_effect` is always `False`.

## Current Limitations

- No marketplace listing, public release, live connector success, or production readiness is
  claimed by these examples.
- No real Office, Google Workspace, Slack, Teams, email, Graph, GitHub, Linear, filesystem, or MCP
  mutation is performed.
- Skill guidance and self-evolution governance are documentation/configuration guidance only:
  future memory or skill updates must store summaries and hashes, require owner approval, and never
  persist raw private office content.
