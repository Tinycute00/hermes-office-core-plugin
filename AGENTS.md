# Office OS development guidance

Treat this repository as one Codex plugin, not as a Python package or a Hermes compatibility layer.

Keep `skills/office-os/SKILL.md` as the single executable workflow. Put detailed behavior in the directly linked reference file that owns it. Preserve the first-line intent contract, source-file immutability, stable `Office OS Output` publishing, bounded backups, bounded Stop continuation, and bounded workspace state.

Use standard-library Python in hooks and the local state core. Office authoring belongs to Codex's bundled spreadsheet, document, presentation, and PDF capabilities; the local core indexes, coordinates, validates candidates, and publishes outputs.

After changes, run the unit tests, the skill validator, and the plugin validator. Simulate all three hook events with JSON input. Keep user-facing language nontechnical and efficiency-first.
