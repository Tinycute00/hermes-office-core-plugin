# Hook boundaries

Office OS uses these six event-specific hooks. They are advisory context and recovery helpers; the Core and local adapter retain authority for candidate containment, confirmation, publishing, runtime checks, and policy enforcement.

| Event | Scope and input | Safe output and state boundary |
| --- | --- | --- |
| `SessionStart` | Recovery context for a session. | May clean stale private temporary state and report compact context; never creates a fresh workspace or reconstructs a task. |
| `UserPromptSubmit` | Source-free or named-source intake. | Emits bounded intake context and manages one private marker; never reads Office data before a source is named. |
| `PreToolUse` | Recognized Office tool call before execution. | Supplies context or denies a structural unsafe Office call; generic work returns `{}` with no state. |
| `PermissionRequest` | Recognized Office approval request. | Supplies neutral context or `{}`; it must never return `allow` or replace ordinary Codex approval. |
| `PostToolUse` | Recognized Office tool outcome. | Supplies bounded recovery context only for deterministic failures; ordinary success and unrelated tools create no diagnostic receipt. |
| `Stop` | Source-free final reply and active-run completion. | Is the sole bounded correction/continuation owner; correction is bounded and continuation remains capped. |

The tool-event outer matcher is exactly `^(Bash|mcp__officecli__officecli)$`. Inside that scope, only the exact OfficeCLI MCP route and bounded installed Core/manager Bash invocations are recognized. Generic Bash, MCP, file, and web work returns `{}` successfully before state access.

Hooks are not hard enforcement or a second OfficeCLI implementation. They do not duplicate command grammar, path containment, confirmation, publish enforcement, runtime verification, or approval authority. PermissionRequest never allows; PreToolUse can only provide context or deny an unambiguously structural unsafe Office call; PostToolUse cannot roll back an executed action. The Core and local adapter retain authority.
