# Architecture

## Positioning

`office-core` is a standalone third-party Hermes Agent plugin. The package root contains
`plugin.yaml` and `__init__.py` for direct plugin loading, and `pyproject.toml` exposes the same
`register(ctx)` path through pip entry-point discovery.

No-core-modification guarantee: the plugin does not modify Hermes source, does not edit
`hermes-agent`, and does not patch official Hermes runtime files. Runtime installation happens
through supported external plugin locations or package discovery.

## Components

- `plugin.py`: registers tools, observer hook, optional status command, and plugin skills.
- `tool_handlers.py`: exposes read-only diagnostic, draft workflow planning, and operation
  preview tools.
- `operation_policy.py`: classifies read/write/delete/external-send operations, applies the
  v0.1 draft-only external-write policy, and emits audit/provenance/confidence records.
- `bridge_planner.py`: plans handoff to existing Hermes Skills, MCP, Kanban, document,
  spreadsheet, presentation, PDF/OCR, Google Workspace, GitHub, Linear, and filesystem surfaces.
- `local_file_discovery.py`: scans only allowlisted roots with path normalization, traversal
  denial, size/depth/count limits, and deterministic output.
- `registry_*` and `data_maps.py`: model templates, source maps, data maps, reusable fields,
  provenance, confidence, and owner-confirmation state.
- `skills/`: ships `office-core:office-template-update`, `office-core:office-data-package`,
  and `office-core:office-reuse-data`.

## Safety Model

The policy wrapper is inside tool execution, not only lifecycle registration. It records:

- operation kind and risk level,
- confirmation state,
- draft-only decision,
- provenance links and evidence hashes,
- confidence score and confidence band,
- audit outcome and reason.

Office content and external service text are untrusted data, never as instructions. Bridge plans
describe missing capabilities and fallbacks instead of pretending to perform unavailable work.

## v0.1 Boundaries

v0.1 can plan, preview, discover candidate files inside allowlisted roots, and produce handoff
instructions. v0.1 does not execute confirmed external writes, deletes, uploads, emails, SaaS
sends, or user-file mutations. High-impact requests create draft records or owner-confirmation
items.
