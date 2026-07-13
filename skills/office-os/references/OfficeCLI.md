# OfficeCLI managed adapter

Office OS optionally uses a local adapter around a checksum-pinned OfficeCLI executable. The adapter, not OfficeCLI's own MCP mode, owns the JSON-RPC boundary, path policy, process limits, and screenshot lifecycle.

## Consent and activation

Use the authoritative `PLUGIN_DATA` path injected by the Office OS hook for every core and manager child process. Do not infer the fallback path or persist the variable globally; a missing injected value means the installed plugin must be restarted or repaired before writable work. Resolve the plugin root from `SKILL.md`, then run the read-only manager check in that child environment:

    python "<plugin-root>/scripts/officecli_manager.py" status

The status command creates nothing. If the runtime is absent, ask the owner one short question before any download. After approval, run:

    python "<plugin-root>/scripts/officecli_manager.py" install --accept-download

The manager selects OfficeCLI `1.0.135` at source commit `d2d9c60f44537004c3e1f46680c24ea38d9659c2`, downloads the matching release asset, verifies its locked SHA-256 and version, and keeps one verified version under plugin data. Repeated installation is a no-op; a verified new version prunes only ordinary old-version siblings. It does not add the executable to `PATH`, install skills, register another MCP server, or change user OfficeCLI configuration.

## Authority boundary

The adapter exposes exactly one tool, `officecli`, with one required input: `command: string[]` containing 1 to 128 tokens. Never send a shell command, an alternate argument field, or an extra tool parameter.

Its sole staging root is `PLUGIN_DATA/officecli-candidates`, derived internally. Each successful `office_os.py begin` reserves and returns one run-specific `candidate_directory` below that root. A manual fixed-output run remains `awaiting_confirmation` until the owner explicitly approves the compact task agreement through `office_os.py confirm`; before that transition, do not copy a source, create a candidate, call the adapter, record progress, or publish. Before a tool call after confirmation:

Manual candidate mutation requires Core confirmation. The Node adapter validates structured command grammar and candidate containment; it does not inspect or replace the Core's owner-confirmation state.

1. fingerprint every source through the Office OS core;
2. create a new candidate or copy the source into the returned `candidate_directory`;
3. pass only the staged candidate and contained file-bearing property values;
4. validate and render the candidate through the adapter;
5. publish that exact run-contained candidate with `office_os.py publish`, which rejects sibling-run, workspace-scratch, linked, and hard-linked candidates before rechecking source fingerprints and write authority.

Never pass an original source path. The adapter rejects lexical, canonical, real-path, closest-existing-parent, symlink, junction, reparse-point, hard-link, and prefix-boundary escapes. The only excluded threat is a malicious concurrent local link swap after validation.

The staging root is production-bounded, not a history folder. `begin` registers the run directory before authoring starts, so the active draft is preserved across another workspace's cleanup before its first publish. Successful publish, completion, and explicit failure remove the run directory and its managed candidates. `begin` and `cleanup` reclaim unreserved files older than 24 hours and then keep at most 32 ordinary files totaling at most 2 GiB. Malformed active-run inventory and linked staging roots fail closed; nested link objects are removed without following or touching their targets.

## Allowed command grammar

Use only `validate`, `get`, `query`, `view`, `set`, `add`, `remove`, `move`, and `swap`, with an optional final `--json` token:

- `validate <file>`
- `get <file> <dom-path> [--depth 0..8]`; never request `selected` or saving
- `query <file> <selector> [--find <text>] [--compact] [--fields <csv>]`
- `view <file> (text|annotated|outline)` with bounded start/end, line, column, and range selectors
- `view <file> stats`
- `view <file> issues [--type <known-subtype>] [--limit 1..500]`
- `view <file> screenshot` with an optional page or range and width `320..4096` and height `240..4096`
- `set <file> <dom-path>` with properties and an optional paired find/replace
- `add <file> <parent>` from a type or DOM path, one position selector, and optional properties
- `remove <file> <dom-path>` with an optional left/up shift and property keys
- `move <file> <dom-path>` with an optional parent, one position selector, and optional properties
- `swap <file> <dom-path-1> <dom-path-2>`

Selectors, text, and property tokens are limited to 4096 UTF-8 bytes. Field, column, and type tokens are limited to 1024 bytes. Indices are `0..1000000`; starts, ends, pages, and similar positive positions are bounded by the adapter. File-bearing properties named `src`, `file`, `image`, `template`, `ole`, `video`, or `audio` must resolve inside the staging root.

The adapter denies configuration, installation, MCP registration, skills, plugins, resident, watch, and server operations. It also denies `create`, `import`, `merge`, `raw`, `raw-set`, `add-part`, `refresh`, `save`, `close`, `open`, `goto`, `mark`, `dump`, and `batch`; file-or-stdin forms; caller output or render modes; browser/force/grid/page-count flags; HTML, SVG, PDF, and forms modes; response files; a literal option terminator; NUL; unknown grammar; and options that imply filesystem output.

## Execution and screenshots

Normal commands have a 60 seconds deadline. Screenshots have 120 seconds. Standard output and standard error are each limited to 8 MiB. Timeout or overflow terminates the child process tree.

For a screenshot, the adapter appends a controlled PNG destination and fixed HTML rendering mode, verifies the PNG signature and 16 MiB size limit, returns MCP `image/png` content, and removes the temporary image in `finally`. Callers never choose the screenshot destination.

Every allowlisted child command receives exactly:

    OFFICECLI_SKIP_UPDATE=1
    OFFICECLI_NO_AUTO_INSTALL=1
    OFFICECLI_NO_AUTO_RESIDENT=1

These variables are child-command environment only. They do not mutate persistent OfficeCLI settings and make no promise about execution modes outside this adapter. The adapter re-verifies the pinned executable immediately before every call and never launches OfficeCLI's upstream MCP mode.

## Failure and fallback

Missing, unsupported, linked, corrupt, tampered, timed-out, overflowing, or invalid adapter calls fail closed. They do not authorize a direct executable call. Continue through Codex's installed spreadsheet, document, presentation, or PDF capability, still keeping the source immutable, authoring a candidate, validating it, and publishing through the Office OS core. PDF remains read-only.
