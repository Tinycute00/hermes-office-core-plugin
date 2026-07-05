# Codebase Review Tasks

This review proposes one focused follow-up task in each requested category.

## Spelling task

Fix user-facing spelling and phrasing inconsistencies in setup documentation, starting with the
README runtime warning. The warning now uses "Temporary runtime" consistently instead of the
hyphenated shorthand, and future copy edits should keep install guidance plain and searchable.

## Bug-fix task

Treat explicitly requested but missing local files as denials. A requested path that resolves
inside an allowed root should not silently produce a successful empty result when the file does
not exist; callers need a denial reason they can surface to users.

## Comment or documentation drift task

Keep README tool listings synchronized with the plugin's registered tool definitions. When a tool
is added, renamed, or removed in `office_core_plugin/tool_handlers.py`, update the Registered tools
section in the same change so users do not follow stale documentation.

## Test-improvement task

Add regression coverage for requested local-file paths that resolve inside an allowed root but do
not exist. The test should assert a failed discovery result, no candidates, and a stable
`path_unavailable` denial reason.
