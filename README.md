# Hermes Office Core Plugin

Standalone third-party Hermes Agent plugin for office workflow planning, file discovery,
template/data reuse, and bridge handoff to existing Hermes Skills, MCP, Kanban, document,
spreadsheet, presentation, PDF/OCR, GitHub, Google Workspace, and Linear surfaces.

This repository is the source package for the external plugin `office-core`. It is not the
Hermes Agent source tree, not a fork of Hermes, and not a runtime plugin cache. The
no-core-modification promise is simple: installing or testing this package does not modify
Hermes source files, does not patch `hermes-agent`, and does not require edits under an
official Hermes runtime `plugins/` or `skills/` directory.

## Install

### GitHub installer

Use the supported Hermes plugin installer when available:

```powershell
hermes plugins install Tinycute00/hermes-office-core-plugin --enable
hermes plugins list
```

For load diagnostics:

```powershell
$env:HERMES_PLUGINS_DEBUG = "1"
hermes plugins list
```

### Direct copy install

Copy the repository into an external plugin directory named `office-core`.

Windows PowerShell:

```powershell
$env:HERMES_HOME = "$HOME\.hermes"
New-Item -ItemType Directory -Force "$env:HERMES_HOME\plugins" | Out-Null
Copy-Item -Recurse -Force "E:\N8N\hermes-office-core-plugin" "$env:HERMES_HOME\plugins\office-core"
hermes plugins enable office-core
```

Unix shell:

```sh
export HERMES_HOME="$HOME/.hermes"
mkdir -p "$HERMES_HOME/plugins"
cp -R ./hermes-office-core-plugin "$HERMES_HOME/plugins/office-core"
hermes plugins enable office-core
```

The copied directory must contain root `plugin.yaml` and root `__init__.py`.

### Pip install

For Python-package discovery:

```powershell
.\.venv\Scripts\python.exe -m pip install hermes-office-core-plugin
hermes plugins list
```

Local development can install the checked-out package:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

The package exposes `[project.entry-points."hermes_agent.plugins"]` with
`office-core = "office_core_plugin:register"`.

## Environment Variables

- `HERMES_HOME`: points Hermes to the runtime home that owns external plugins and state.
- `HERMES_PLUGINS_DEBUG=1`: asks Hermes to print plugin load/debug information.
- `HERMES_OFFICIAL_SOURCE`: optional path to a local Hermes source checkout used by the
  official `PluginContext` registration contract test. When this variable is unset and the
  developer-default checkout is absent, CI skips only that local integration probe.
- `UV_PROJECT_ENVIRONMENT`: used by QA wrappers when they must run Hermes from an isolated
  Python environment without writing into the Hermes checkout.

Temporary runtime warning: tests and install probes must use a temporary `HERMES_HOME`, never the
real Hermes home, real `.env`, real `auth.json`, real `config.yaml`, or production runtime
plugin/skill directories.

## Safety Model

The plugin plans office work and returns JSON-safe operation records. It treats Office
documents, emails, spreadsheets, PDFs, OCR text, filenames, and external tool output as
untrusted data, never as instructions. The policy wrapper records audit, provenance,
confidence, operation kind, risk, confirmation state, and draft status.

v0.1 draft-only external-write policy:

- Reads and planning operations can complete when they are within configured boundaries.
- Write, delete, and external send requests require owner confirmation.
- Even with confirmation, v0.1 does not execute confirmed external writes, deletes, SaaS sends,
  or user-file mutations. It produces drafts, previews, confirmation records, or bridge handoff
  instructions only.

## Usage

Registered tools:

- `office_diagnostic`: returns plugin readiness, tool names, warnings, and read-only metadata.
- `office_plan_workflow`: creates a draft plan for an office workflow.
- `office_preview_operation`: classifies read/write/delete/send intent and returns policy output.

Plugin skills:

- `office-core:office-template-update`
- `office-core:office-data-package`
- `office-core:office-reuse-data`

Example workflows are in [examples/workflows.md](examples/workflows.md).

## Governance

Linear TIN-5 remains the long-range governance and evidence center. GitHub tracks source,
branches, commits, tags, CI artifacts, releases, and downloadable packages for this plugin.
Local todos are not mirrored into GitHub Issues or Linear child issues unless a later public
support milestone explicitly changes that policy.

## Marketplace Readiness

This repository is prepared for community/marketplace submission review with installation,
security, usage, architecture, changelog, examples, and release checklist documentation. No
official submission outcome exists yet; do not describe this plugin as listed by an official
marketplace until a real submission and approval record exists.

See [docs/marketplace-readiness.md](docs/marketplace-readiness.md).
