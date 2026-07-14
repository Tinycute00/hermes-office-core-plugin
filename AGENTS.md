# Office OS development guidance

Treat this repository as one Codex plugin, not as a Python package or a Hermes compatibility layer.

Keep `skills/office-os/SKILL.md` as the single executable workflow. Put detailed behavior in the directly linked reference file that owns it. Preserve the first-line intent contract, source-file immutability, stable `Office OS Output` publishing, bounded backups, bounded Stop continuation, and bounded workspace state.

Use standard-library Python in hooks and the local state core. Office authoring belongs to the managed local OfficeCLI adapter when available or Codex's bundled spreadsheet, document, presentation, and PDF capabilities as fallback. Both paths may touch candidates only. The local core indexes, coordinates, validates candidates, and publishes outputs.

Hooks and MCP processes receive the plugin-owned data root. The hook must inject that authoritative `PLUGIN_DATA` value into Office workflow context, and the skill must pass it unchanged to every core/manager child process; never rely on the agent terminal's ambient environment or a second fallback root. Hook and core state locking must remain interoperable through the same OS-owned file lock. Current Codex runtime discovers these lifecycle hooks from config-layer `~/.codex/hooks.json`, so `scripts/office_hook_registry.py` is the only supported writer: it must preserve unrelated groups, own only its three marker-tagged entries, and bind fixed absolute plugin/data roots.

Runtime ownership covers `.mcp.json`, `vendor/officecli.lock.json`, `scripts/officecli_manager.py`, `scripts/officecli_runtime.py`, `scripts/officecli-mcp.cjs`, `scripts/officecli-mcp/**`, `scripts/office_hook_registry.py`, `tests/test_officecli.py`, and `tests/test_hook_registry.py`. Workflow and release-contract ownership covers `skills/office-os/**`, `README.md`, this file, `.codex-plugin/plugin.json`, and `tests/test_contracts.py`.

Keep `.mcp.json` in canonical top-level `mcpServers` form with one `officecli` server, exact Node entrypoint arguments, and repository-root cwd. Never expose a second tool, a string-valued command, an upstream MCP process, an unverified executable call, caller-selected candidate/output roots or identities, `raw` or `batch`, hidden installation, or user OfficeCLI configuration mutation. Keep the fixed candidate staging root bounded, hard-link-safe, and run-directory-aware from `begin` through first publish and final closure; completed/closed candidates and nested link objects must not accumulate. Document updater suppression only as a child-command environment control.

After changes, run the unit tests with Node available, the skill validator, and the plugin validator. Simulate all three hook events with JSON input. Keep user-facing language nontechnical and efficiency-first; do not skip a required gate.
