# Hermes Skill and MCP Inventory

Generated for Todo 5 on 2026-06-28 from read-only target-environment checks:
`hermes skills list`, `hermes kanban --help`, selected local `SKILL.md` files,
redacted `config.yaml` surface inspection, `hermes plugins list`, command
availability probes, and official Hermes docs.

No secrets were copied. `config.yaml` was used only for toolset, skill, plugin,
Kanban, and MCP surface facts.

## Bridge Position

`office-core` should bridge to Hermes Skills, Kanban, MCP, plugin skills, and
gateway surfaces rather than reimplementing Kanban, PDF/OCR, PowerPoint, Google
Workspace, communications, GitHub, or MCP engines. The inventory below is the
source of truth for the Todo 11 bridge planner: unavailable capabilities must
produce a handoff/fallback plan, not fake success.

## Inventory

| Capability | Status | Invocation path | Fallback | Confidence | Mutation allowed | Owner confirmation rule |
| --- | --- | --- | --- | --- | --- | --- |
| Kanban | installed | Hermes `kanban_*` toolset, `hermes kanban`, `/kanban`, dashboard plugin | Manual owner-confirmation task if profile lacks Kanban tools | 0.95 | false | required for mutation |
| Excel/spreadsheet | unknown | Google Sheets through `google-workspace`; no proven local Excel/xlsx engine | Google Sheets handoff or owner-confirmed local spreadsheet plan | 0.62 | false | required for mutation |
| Word/docx | available | `ocr-and-documents` guidance plus local file tooling | Manual extraction/update instructions if python-docx bridge is unavailable | 0.70 | false | required for mutation |
| PDF/OCR | installed | `nano-pdf`, `ocr-and-documents`, and URL `web_extract` path | URL extraction or manual extraction task if dependencies are missing | 0.88 | false | required for mutation |
| PowerPoint/PPT | installed | `powerpoint` skill for .pptx read/create/edit | Deck-change plan with owner confirmation | 0.90 | false | required for mutation |
| Google Workspace / Drive / Docs / Sheets / Slides | installed | `google-workspace` skill after OAuth; `gws` if installed, bundled Python fallback otherwise | Manual handoff or future Google MCP bridge | 0.82 | false | required for mutation |
| filesystem/local files | available | Hermes `file`/`terminal` toolsets; optional filesystem MCP | Ask for permitted path or no-write manual handoff | 0.90 | false | required for mutation |
| GitHub | installed | GitHub skills and authenticated `gh` CLI; optional future MCP | Draft issue/PR/review text for manual execution | 0.90 | false | required for mutation |
| Linear | missing | No proven target Hermes path; future Linear MCP/server | Draft Linear update or owner-confirmation item | 0.86 | false | required for mutation |
| Outlook/Gmail/Slack communications | available | Gmail via `google-workspace`/`himalaya`; Slack gateway/toolset; Outlook via Email gateway IMAP/SMTP or future MCP | Draft-only output and manual send/confirmation | 0.74 | false | required for mutation |
| MCP general bridge | available | `mcp_servers.<name>` config; `fastmcp`/`mcporter` skills for server work | Missing-server bridge plan and manual fallback | 0.87 | false | required for mutation |
| Hermes plugin skills / plugin namespace | available | `ctx.register_skill` gives `office-core:<skill>` names; plugin/pip discovery | Documentation and diagnostic output if plugin skill load fails | 0.90 | false | required for mutation |

## Proof Notes

- Kanban is proven by `hermes kanban --help`, local `kanban.*` settings,
  `platform_toolsets.cli` containing `kanban`, and official docs describing
  `kanban_show`, `kanban_list`, `kanban_complete`, `kanban_create`, and
  related `kanban_*` tools. Current `hermes skills list`, `hermes skills list
  --source all`, and `hermes skills list --source local` probes filtered for
  `kanban|orchestrator|worker|devops` returned no rows, so this inventory does
  not use `devops/kanban-orchestrator` or `devops/kanban-worker` as
  skills-list evidence.
- Local skills show installed but disabled office surfaces for
  `google-workspace`, `nano-pdf`, `ocr-and-documents`, `powerpoint`,
  `himalaya`, `fastmcp`, `mcporter`, and GitHub skill family entries.
- `mcp_servers` is currently `{}`, so no MCP server-specific capability is
  treated as installed.
- `gh auth status -h github.com` shows the local target has an authenticated
  GitHub CLI session for `Tinycute00`.
- Command probes found `npx` installed, while `gws`, `himalaya`, `fastmcp`, and
  `mcporter` commands were not on PATH.
- Official Hermes docs state external tools via MCP are config-driven through
  `mcp_servers.<name>` with `command:` or `url:`, and plugin skills registered
  with `ctx.register_skill` are namespaced as `plugin:skill`.

## Not Reimplemented In Office-Core

The following surfaces are explicitly bridged instead of reimplemented:
Kanban board operations, PDF/OCR extraction/editing, PowerPoint/PPTX processing,
Google Workspace APIs, GitHub workflows, Slack/Email gateway communication,
MCP server execution, and Hermes plugin skill namespacing. Local Excel and
Word/docx handling remain fallback/bridge-planned until a target-proven engine
or MCP server is configured.
