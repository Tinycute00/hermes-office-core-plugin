# Office Self Evolution

This guide sets the default policy for office workflows that use Hermes memory,
skills, cron, and hooks with `office-core`. It is documentation and config
guidance only. The plugin must not add automatic memory writes, skill writes,
live cron jobs, live hooks, or other Hermes runtime changes unless explicit user
approval gates are designed, implemented, and tested.

Office documents, email bodies, spreadsheet cells, OCR text, filenames, tool
output, and meeting notes are untrusted data, never instructions. Any learning
path must keep that boundary visible in audit records and user prompts.

## Required Defaults For Office Deployments

1. Memory write approval is on.
2. Skill write approval is on.
3. Raw document storage is off.
4. Cron jobs are self contained and run from declared inputs.
5. Mechanical cron checks run without agents.
6. Hooks only add audit context or inject safe context.
7. Any transformation that reaches memory, skills, logs, or evidence redacts
   private content first.

## Memory Writes

Memory can store durable facts that help later office planning, but only after
owner approval. Store summaries, hashes, labels, source kinds, provenance, and
confidence. Don't store raw documents, full emails, meeting transcripts,
tokens, OAuth secrets, cookies, API keys, private file paths, or extracted text
that could reveal private content.

Allowed memory records:

1. A template family label such as `monthly finance report`, paired with a
   template hash and owner approved purpose.
2. A source map summary that names source types, confidence bands, and evidence
   hashes.
3. A reusable preference such as `board deck uses 16:9 layout`, with owner and
   date metadata.
4. A rejection such as `do not use the legacy vendor spreadsheet`, with a reason
   and hash of the rejected source.

Every memory write prompt must show the sanitized payload before saving. If the
payload contains untrusted external text, the prompt must state that the text is
being summarized as data and will not be treated as instructions.

## Skill Writes

Skills are for reusable procedures, not private facts. A skill write needs owner
approval and must contain only sanitized, repeatable steps. Put workflow shape,
checklists, validation rules, and bridge handoff patterns in skills. Put
specific client names, raw source excerpts, credentials, full meeting notes, and
private one time facts outside skills.

Good skill content:

1. `Ask the owner to confirm the source file when confidence is below 0.80.`
2. `Generate a draft only handoff for external sends.`
3. `Require redaction before copying source excerpts into evidence.`

Bad skill content:

1. Raw document paragraphs.
2. Full email threads.
3. Meeting transcripts.
4. Tokens, secrets, account IDs, cookies, or auth headers.
5. Instructions copied from untrusted office content.

## Cron Jobs

Cron may run scheduled office operations only when the job is self contained.
The job must declare its input roots, output location, policy mode, audit path,
and failure behavior. Cron must not depend on live chat memory or hidden session
state.

Use no agent cron for mechanical checks such as stale evidence detection,
schema validation, docs validation, missing hash scans, or draft age reports.
Use an agent only when the owner has approved a bounded analysis task and the
job still cannot write memory, write skills, send email, publish, upload, or
mutate user files without a separate approval gate.

Cron jobs must not perform live sends, live publishes, external deletes,
external uploads, or user file mutations. For high impact work, cron can create
a draft, preview, report, or owner confirmation task.

## Hooks

Hooks are allowed for audit and safe context injection only. They can add policy
reminders, provenance, confidence context, source hashes, and redacted summaries.
They must not silently save memory, edit skills, start cron, approve high impact
actions, or turn untrusted external text into instructions.

Transform hooks must redact before writing output anywhere durable. Redaction
applies to document text, emails, OCR, spreadsheet cells, filenames that reveal
private content, tokens, API keys, cookies, auth headers, and account secrets.

## Prohibited Content And Actions

Don't save or generate durable records containing:

1. Raw private office documents.
2. Full transcripts or full email threads.
3. OAuth tokens, API keys, cookies, passwords, private certificates, or auth
   headers.
4. Unredacted customer, employee, or vendor private content.
5. Instructions copied from external documents, emails, PDFs, OCR, spreadsheets,
   filenames, or tool output.

Don't perform these actions from office self evolution paths:

1. Silent memory writes.
2. Silent skill writes.
3. Live sends, publishes, uploads, deletes, or file mutations.
4. Runtime writes under a real Hermes home during QA.
5. Any write that bypasses owner approval.

## Approval Gated Learning Examples

### monthly_report_learning_example

Scenario: an owner asks `office-core` to refresh a monthly report and the system
finds a repeatable source pattern.

Allowed flow:

1. Treat the template, spreadsheet cells, and imported notes as untrusted data.
2. Extract only a sanitized pattern: report family, source kind, template hash,
   source hash, confidence band, and validation checklist.
3. Redact all private names, raw tables, comments, and document text.
4. Show the proposed memory payload to the owner.
5. Ask: `Save this sanitized report pattern to Hermes memory?`
6. Save only after explicit approval.

Example approved memory payload:

```json
{
  "name": "monthly_report_learning_example",
  "kind": "office_pattern",
  "summary": "Monthly report uses an owner approved template family and a current period spreadsheet source.",
  "template_hash": "sha256:example-template-hash",
  "source_hash": "sha256:example-source-hash",
  "confidence_band": "high",
  "approval_required": true,
  "raw_content_stored": false
}
```

If approval is denied, record only the denial audit event and do not save the
pattern.

### meeting_summary_learning_example

Scenario: an owner asks for a meeting summary and wants future summaries to
follow the same review checklist.

Allowed flow:

1. Treat the notes, transcript, chat export, and action items as untrusted data.
2. Create the summary draft without storing the source text.
3. Extract only a reusable procedure, such as review order, redaction checks,
   and confirmation points.
4. Show the proposed skill update to the owner.
5. Ask: `Save this sanitized meeting summary procedure as a Hermes skill?`
6. Save only after explicit approval.

Example approved skill payload:

```json
{
  "name": "meeting_summary_learning_example",
  "kind": "skill_procedure",
  "summary": "For meeting summaries, produce a draft, list open decisions, list owner confirmed actions, and run redaction before evidence logging.",
  "source_hash": "sha256:example-notes-hash",
  "approval_required": true,
  "raw_content_stored": false
}
```

The skill must not include transcript text, attendee private details, private
quotes, credentials, or instructions embedded in the meeting source.

## Audit Expectations

Each proposed memory or skill write should have an audit record with operation
kind, risk level, approval state, sanitized payload hash, source hashes,
redaction result, and final outcome. Misleading success output is not allowed:
if a write is blocked, denied, or only drafted, the result must say that plainly.

For v0.1, high impact office operations remain draft only. A confirmation record
can prove owner intent, but it doesn't authorize the plugin to create, mutate,
delete, upload, publish, email, or send external content.
