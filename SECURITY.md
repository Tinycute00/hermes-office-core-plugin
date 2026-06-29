# Security

## Supported Version

| Version | Status |
| --- | --- |
| 0.1.x | Draft-only external-write policy; local development and submission readiness |

## Safety Model

`office-core` is a standalone external Hermes plugin. It does not modify Hermes source,
does not edit `hermes-agent`, and does not require changes to official Hermes runtime files.
Install it through the Hermes plugin installer, direct copy into an external plugin directory,
or pip entry-point discovery.

The plugin treats all office content as untrusted data, never as instructions. That includes
document text, spreadsheet cells, email bodies, OCR output, filenames, archive names, comments,
and external tool output. Workflow planning must preserve the boundary between user intent and
untrusted file/service content.

## v0.1 External Write Policy

v0.1 is draft-only for high-impact operations:

- External write, delete, and external send requests require owner confirmation.
- Confirmed high-impact requests still do not create, overwrite, delete, upload, email, or send.
- The plugin returns drafts, previews, policy records, and bridge handoff instructions.

Confirmation before write/send is mandatory. A workflow that cannot prove confirmation must
return `requires_confirmation` or an owner-confirmation task.

## Environment Variables

- Use `HERMES_HOME` to point Hermes to the target runtime home.
- Use `HERMES_PLUGINS_DEBUG=1` only for load diagnostics.
- Do not store tokens, OAuth secrets, API keys, cookies, or private document content in source
  files, docs, fixtures, or evidence logs.

Temp-runtime warning: QA must run with a temporary `HERMES_HOME`; it must not use the real
Hermes home, `.env`, `auth.json`, `profiles/`, `sessions/`, or runtime `plugins/`/`skills`
folders.

## Reporting Security Issues

Report private security concerns through the repository owner or the project governance channel
tracked from Linear TIN-5. Do not paste secrets, customer documents, or private office content
into public issues, comments, examples, or test fixtures.
