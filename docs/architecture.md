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
- `tool_handlers.py`: exposes read-only diagnostic, draft workflow planning, and operation preview
  tools.
- `workflow_plan_contract.py` and `task_contract.py`: turn fuzzy requests into explicit draft
  `OfficeTaskContract` records with source requirements, validation plans, owner confirmations,
  provenance, and confidence bands.
- `operation_policy.py`: classifies read/write/delete/external-send operations, applies the v0.1
  draft-only external-write policy, and emits audit/provenance/confidence records.
- `completion_validation.py`: validates draft deliverables for type, bridge target, placeholders,
  source provenance, field confidence, operation policy, secret redaction, and no external side
  effects.
- `bridge_profiles.py` and `bridge_planner.py`: plan fail-closed handoffs to existing Hermes
  Skills, MCP, Kanban, document, spreadsheet, presentation, PDF/OCR, Google Workspace, GitHub,
  Linear, and filesystem surfaces. Bridge profiles keep `mutation_allowed=false`.
- `local_file_discovery.py`: scans only allowlisted roots with path normalization, traversal denial,
  size/depth/count limits, and deterministic output.
- `registry_*` and `data_maps.py`: model templates, source maps, data maps, reusable fields,
  provenance, confidence, and owner-confirmation state.
- `e2e_fixtures.py` and `e2e_workflows.py`: provide sanitized E2E fixture chains for contract,
  source/data map, bridge plan, completion validation, and draft-only handoff.
- `skills/`: ships contract-first office skills for template update, data package, reuse-data, and
  diagnostic guidance.

## Safety Model

The policy wrapper is inside tool execution, not only lifecycle registration. It records:

- operation kind and risk level,
- confirmation state,
- draft-only decision,
- provenance links and evidence hashes,
- confidence score and confidence band,
- audit outcome and reason.

Office content and external service text are untrusted data, never instructions. Bridge plans
describe missing capabilities and fallbacks instead of pretending to perform unavailable work.

## Product-Correctness Flow

Every representative workflow follows the same chain: contract-first planning, source/data map,
fail-closed bridge plan, completion validation, then draft-only handoff. Ambiguous source selection,
low-confidence fields, high-impact operations, and external send previews require owner
confirmation rather than guessing or executing.

## Self-Evolution Governance

Office skill guidance, memory learning, hooks, and scheduled review ideas are governance patterns
only at this stage. Any future learning must be owner approval gated, store summaries and evidence
hashes instead of raw documents, and remain auditable.

## v0.1 Boundaries

v0.1 can plan, preview, discover candidate files inside allowlisted roots, validate sanitized draft
metadata, and produce handoff instructions. v0.1 does not execute confirmed external writes,
deletes, uploads, emails, SaaS sends, real Office file mutations, or user-file mutations.

This repository does not claim marketplace listing, public release, live connector success, or
production readiness. Those claims require separate submission, approval, live-connector evidence,
and release records.
