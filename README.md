# Office OS

Office OS is a local-first Codex plugin for ordinary office work. A user can ask in plain language to find, review, create, update, combine, or schedule work involving Excel, Word, PowerPoint, and PDF files. The plugin classifies the request, asks one question at a time only when an owner decision is missing, completes the work in useful chunks, and keeps the original files unchanged.

Office OS begins an Office turn with a compact intent classification. When a local source is named, its formal reply uses an intent envelope such as:

```text
意圖：更新｜物件：Excel｜權限：固定輸出覆寫｜檢查：快速
```

When the user has not supplied a local source, Office OS asks for the source before it inspects or alters Office data. To make that final question deterministic, the hook keeps one private intake marker per session: it never stores the raw prompt, only separate SHA-256 hashes of session and session-plus-turn, the derived canonical reply, and creation time; it caps live markers at 128 globally, expires them after one hour on the next intake or Stop, and consumes the matching marker at Stop. Every later UserPromptSubmit in the same session clears an older marker before correction, non-Office, or named-source routing; a newer source-free turn in the same session replaces the older marker atomically. Stop can consume the same-session marker when the host advances the turn id, but only within that source-free response cycle.

## File support

| Format | Read | Write | Notes |
| --- | --- | --- | --- |
| `.xlsx` | Yes | Yes | Excel workbooks |
| `.docx` | Yes | Yes | Word documents |
| `.pptx` | Yes | Yes | PowerPoint decks |
| `.pdf` | Yes | No | Review and extraction only |
| `.xls`, `.doc`, `.ppt` | Detect | No | Convert before editing |
| `.xlsm`, `.docm`, `.pptm` | Metadata | No | Convert before editing; macros are not rewritten |

## Output behavior

Source files are never overwritten. Results go to a non-linked sibling folder named `Office OS Output`. Repeating the same task replaces the same collision-safe stable output instead of creating timestamped or caller-selected copies. Manual work keeps no history. Scheduled work keeps at most `.bak.1`, `.bak.2`, and `.bak.3`; unchanged sources are a no-op.

## Local knowledge map

Each workspace gets a private SQLite FTS5 index under Codex's plugin data directory.
Indexing is metadata-only until the owner explicitly grants persistent full-text
access to a workspace root; index paths outside the active workspace are rejected.
Confidential, restricted, encrypted, signed, or macro-enabled files remain
metadata-only unless the exact file is explicitly allowed for that indexing
operation. Deleted files are removed from the index, and completed-run events are
pruned.

## Managed OfficeCLI adapter

Office OS includes an optional local adapter for structured Word, Excel, and PowerPoint edits, validation, and headless PNG review. It exposes exactly one `officecli` tool with array-only commands and uses a plugin-managed OfficeCLI `1.0.135` executable pinned to source commit `d2d9c60f44537004c3e1f46680c24ea38d9659c2` and locked release SHA-256 values.

No executable is downloaded until the owner approves it. After approval, the skill runs the idempotent manager once:

```powershell
$env:PLUGIN_DATA = "<exact value injected by the Office OS hook>"
python scripts/officecli_manager.py install --accept-download
```

The hook supplies the authoritative plugin-data root to the workflow. Every local core and manager command receives that exact value in its child environment, so its candidates, state, and runtime match the MCP server's root; the workflow never infers a second root. The manager keeps one verified version under plugin data and prunes only ordinary old-version siblings after successful verification. It does not place the executable on `PATH`, install another skill, register another MCP server, or mutate user OfficeCLI configuration.

The adapter derives the fixed `PLUGIN_DATA/officecli-candidates` staging root itself. `office_os.py begin` reserves and returns one run-specific `candidate_directory`; copy a source there, operate only on that candidate, then validate and publish through the Office OS core to the stable sibling `Office OS Output` target. The core preserves that active directory across workspaces before the first publish, removes completed/closed candidates, reclaims unreserved candidates older than 24 hours, and caps staging at 32 files and 2 GiB. Malformed active-run inventory and linked roots fail closed; hard-linked files are rejected, and nested link objects are removed without following their targets. Callers cannot choose the candidate root, output identity, or screenshot destination. Normal commands are limited to 60 seconds, screenshots to 120 seconds, and both process streams and returned PNG data are bounded.

Each allowlisted child command receives update, installation, and resident suppression in its child-command environment only. The adapter never starts OfficeCLI's upstream MCP mode or calls an unverified executable directly, and it rechecks the pinned SHA before every call. These controls do not change persistent user settings or promise behavior outside the adapter.

If installation is declined, or the runtime/adapter is missing, unsupported, corrupt, linked, tampered, or unsuitable for a document, the call fails closed and the workflow uses Codex's installed spreadsheet, document, presentation, or PDF capabilities. The candidate/source split, stable output, manual no-history rule, three scheduled backups, and PDF read-only rule remain unchanged.

OfficeCLI is Apache-2.0 software maintained by iOfficeAI. The pinned [license](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/LICENSE) and required [NOTICE](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/NOTICE) remain linked in the runtime lock.

## Install for local testing

Clone or download this repository, then run the installer from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_office_os.ps1
```

That command copies the checked-out plugin into the personal Codex marketplace source at `~/plugins/office-os`, runs `codex plugin add office-os@personal --json`, and registers plus activates the six managed hook groups in the user's config layer. It is intended for a local agent as well as a human operator. It refuses unexpected install roots, excludes repository-only state such as `.git` and `.omo`, and prints a JSON result with the installed version, plugin-data root, hook activation state, and OfficeCLI runtime status.

No OfficeCLI executable is downloaded by default. If the owner has approved the pinned runtime download, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_office_os.ps1 -AcceptOfficeCliDownload
```

The registry writes only Office OS's six managed hook groups — `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, and `Stop` — to `~/.codex/hooks.json`. It owns only its marker-tagged groups, gives them one fixed plugin root and plugin-data root, preserves unrelated user hook groups, and replaces rather than duplicates its own entries. To remove only those managed entries later, run `python scripts/office_hook_registry.py uninstall`. Open `/hooks` once to review and trust the exact hook definitions; changed hooks require review again.

The plugin is Windows-first. Hook logic is shared Python, with a PowerShell bootstrap that locates either system Python or Codex's bundled Python. macOS and Linux use `python3`.

## Scheduling

After the result is accepted, Office OS offers to create a recurring local Codex task. The computer and ChatGPT desktop app must be running, and the folder must be opened as a local Codex project. A schedule reuses its existing automation identity instead of creating duplicates.

## Development

```powershell
$env:PLUGIN_DATA = Join-Path $env:TEMP "office-os-dev-data" # isolated development only
python -m unittest discover -s tests -v
python skills/office-os/scripts/office_os.py doctor
python scripts/officecli_manager.py status
```

The implementation follows the current OpenAI plugin, skill, hook, and scheduled-task documentation and borrows OMO's intent-routing and bounded-continuation ideas while adapting them to local office files.
