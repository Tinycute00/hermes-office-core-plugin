# Office OS development guidance

Treat this repository as one Codex plugin, not as a Python package or a Hermes compatibility layer.

Keep `skills/office-os/SKILL.md` as the single executable workflow. Put detailed behavior in the directly linked reference file that owns it. Preserve the first-line intent contract, source-file immutability, stable `Office OS Output` publishing, bounded backups, bounded Stop continuation, and bounded workspace state.

Use standard-library Python in hooks and the local state core. Office authoring belongs to the managed local OfficeCLI adapter when available or Codex's bundled spreadsheet, document, presentation, and PDF capabilities as fallback. Both paths may touch candidates only. The local core indexes, coordinates, validates candidates, and publishes outputs.

Runtime ownership covers `.mcp.json`, `vendor/officecli.lock.json`, `scripts/officecli_manager.py`, `scripts/officecli_runtime.py`, `scripts/officecli-mcp.cjs`, `scripts/officecli-mcp/**`, and `tests/test_officecli.py`. Workflow and release-contract ownership covers `skills/office-os/**`, `README.md`, this file, `.codex-plugin/plugin.json`, and `tests/test_contracts.py`.

Keep `.mcp.json` in canonical top-level `mcpServers` form with one `officecli` server, exact Node entrypoint arguments, and repository-root cwd. Never expose a second tool, a string-valued command, an upstream MCP process, an unverified executable call, caller-selected candidate/output roots, `raw` or `batch`, hidden installation, or user OfficeCLI configuration mutation. Document updater suppression only as a child-command environment control.

After changes, run the unit tests with Node available, the skill validator, and the plugin validator. Simulate all three hook events with JSON input. Keep user-facing language nontechnical and efficiency-first; do not skip a required gate.
