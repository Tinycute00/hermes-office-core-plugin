# Safe recovery and maintainer repair

Use this reference only after a recognized Office error. Normal Office work stays in the normal workflow. First run the read-only doctor with the installed plugin, data-root, hooks-config, and Codex-config values; then follow the recovery action for the observed signal. `--record-latest` is the only explicit doctor persistence switch.

| Code | Observable safe signal | Safe operator recovery | Maintainer repair gate |
| --- | --- | --- | --- |
| `config_trust` | Doctor finds absent or mismatched registration or trusted hash. | Review the installed hook configuration and trust prompt. | Inspect registration, trusted hashes, and config without self-trusting changes. |
| `launcher_environment` | A recognized route lacks the injected plugin-data environment. | Restart or repair the installed plugin before writable work. | Verify the injected plugin-data environment; do not invent a root. |
| `event_protocol` | A recognized call reports a structural protocol rejection. | Correct the supported invocation and retry only after review. | Reproduce the documented event shape without retaining payload text. |
| `state_safety` | A private root or leaf is linked, hard-linked, or reparse-pointed. | Stop and preserve the existing state. | Inspect linked or malformed private state; never follow or recreate an unsafe target. |
| `consent_policy` | Core reports missing or incompatible confirmation. | Preserve normal approval and ask the owner if needed. | Check the Core confirmation boundary; never grant consent from a hook. |
| `runtime_integrity` | Pinned runtime verification or checksum fails. | Use the supported local fallback. | Run read-only status checks; download or install only after owner approval. |
| `process_timeout` | The bounded runner reports timeout or stream overflow termination. | Do not retry a mutation automatically. | Reproduce with bounded process evidence and safe fallback. |
| `candidate_validation` | Adapter or Core rejects a candidate or contained target. | Use a Core-owned candidate, never the source. | Check the adapter/Core boundary without bypassing containment. |
| `publish_recovery` | Core reports bounded post-commit recovery work. | Keep the prior output intact. | Investigate the Core recovery state; never auto-publish or retry. |
| `final_reply` | The completion contract reports a required final reply. | Return the required bounded final response. | Reproduce the completion contract without changing Office data. |

Use a three-hypothesis loop: state three plausible causes, make a minimal repro, add or keep a focused TDD regression, run real-QA through the supported local adapter or fallback, and clean temporary evidence/state. Do not auto-repair production configuration, self-trust hooks, download a runtime, append a journal, or turn an ordinary user into a debugger.

Doctor and static validators cannot prove live host hook dispatch; they cannot prove persisted hook trust; live trusted QA remains required after registration and review.

The only retained receipt is the replace-only `PLUGIN_DATA/latest_hook_diagnostic.json`. A controlled recognized failure may atomically replace it; it expires after 24 hours and is removed on the next hook or doctor cleanup. It contains bounded identifiers only: never retain raw prompt, Office content, credential, absolute path, command, tool response, PID, user name, stdout/stderr, or history. Do not create a debug log or retain arbitrary error text.
