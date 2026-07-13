# OfficeCLI v1.0.135 integration assessment

**Decision:** Use a small local stdio MCP adapter that invokes a locally pinned OfficeCLI v1.0.135 executable with OFFICECLI_SKIP_UPDATE=1. Do **not** launch upstream `officecli mcp`. The adapter must accept structured, allowlisted argv; resolve every source and destination under the candidate root before execution; and return screenshots as MCP image content. This is the smallest deterministic design for update bypass, candidate-only mutation, and image output.

All repository source links below are immutable GitHub permalinks to commit d2d9c60f44537004c3e1f46680c24ea38d9659c2.

## Release and artifact provenance

The v1.0.135 tag resolves to d2d9c60f44537004c3e1f46680c24ea38d9659c2 (commit subject: chore: bump version to 1.0.135; CHANGELOG for chartEx sheet-rename fix). GitHub's first-party release record for that tag, published 2026-07-10T03:46:50Z by github-actions[bot], lists eight platform binaries and SHA256SUMS. The reported Windows x64 digest is 937db176b585e874aa5bff48d536bce78037665cd862b5deefe56e79977e6588; SHA256SUMS has digest 9a991e1db05c6c6896c6747c76d833735f0b907648cb1a416f80f1840999a3d5. Retrieve it from the version-pinned release path:

~~~text
https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.135/SHA256SUMS
~~~

**Revalidated 2026-07-13:** GitHub's tag ref still resolves directly to the pinned commit, and its release metadata still reports the eight binaries plus SHA256SUMS with the digests above.

**Claim**: Pinned source downloads the version-specific asset, verifies it against SHA256SUMS, then promotes only the verified staged file.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/Core/UpdateChecker.cs#L180-L231)):

~~~csharp
var partialPath = exePath + ".update.partial";
using (var fileStream = File.Create(partialPath))
{
    stream.CopyTo(fileStream);
}
if (!VerifyChecksum(partialPath, assetName, checksumsUrl, downloadClient))
    return;
File.Move(partialPath, finalPath, overwrite: true);
~~~

**Explanation**: Downloaded release artifacts should be independently compared with the versioned checksum manifest before use.

## Update behavior

For ordinary CLI commands, Program calls the updater only unless OFFICECLI_SKIP_UPDATE equals 1. The autoUpdate=false config is honored inside CheckInBackground, but only after pending-update application and skill/config refresh work. It is persistent user state in ~/.officecli/config.json (with a temp candidate in detected containers).

**Claim**: The Program-level skip flag protects normal CLI invocations.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/Program.cs#L204-L236)):

~~~csharp
OfficeCli.Core.Installer.MaybeAutoInstall(args);
if (Environment.GetEnvironmentVariable("OFFICECLI_SKIP_UPDATE") != "1")
    OfficeCli.Core.UpdateChecker.CheckInBackground();
~~~

**Explanation**: A normal child process with OFFICECLI_SKIP_UPDATE=1 does not enter this updater call. MaybeAutoInstall comes first, so use an explicitly managed executable path.

**Claim**: autoUpdate=false prevents new refresh-process spawning, but not the work that precedes the check.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/Core/UpdateChecker.cs#L46-L86)):

~~~csharp
try { Directory.CreateDirectory(ConfigDir); } catch { /* continue */ }
ApplyPendingUpdate();
try { SkillInstaller.RefreshInstalled(); } catch { /* best effort */ }
if (!config.AutoUpdate) return;
if (!SaveConfig(config)) return;
SpawnRefreshProcess();
~~~

**Explanation**: A pending .update can still be applied, and skill/config files can still be changed, before autoUpdate=false returns.

**Claim**: Upstream officecli mcp directly invokes the updater at startup and every hour, bypassing the Program-level skip-flag location.

**Evidence** ([Program source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/Program.cs#L80-L87), [MCP source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/McpServer.cs#L146-L160)):

~~~csharp
await OfficeCli.McpServer.RunAsync();
return 0;
// ...
try { UpdateChecker.CheckInBackground(); } catch { }
await Task.Delay(TimeSpan.FromHours(1), token);
UpdateChecker.CheckInBackground();
~~~

**Explanation**: Program returns from the mcp branch before reaching the environment test. autoUpdate=false suppresses the new refresh spawn but is stateful and cannot guarantee no startup writes/replacement. OFFICECLI_SKIP_UPDATE=1 alone does **not** stop the upstream MCP periodic updater.

## MCP protocol and tool contract

The upstream server is newline-delimited JSON-RPC 2.0 over stdio. It handles initialize, notifications/initialized, tools/list, tools/call, and ping. initialize returns protocolVersion 2024-11-05; capabilities.tools.listChanged false; and serverInfo name officecli. tools/list exposes exactly one tool: officecli.

**Claim**: The protocol dispatch and initialize response are fixed in source.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/McpServer.cs#L114-L123)):

~~~csharp
"initialize" => HandleInitialize(id),
"notifications/initialized" => null,
"tools/list" => HandleToolsList(id),
"tools/call" => HandleToolsCall(id, root),
"ping" => WriteJson(...),
~~~

**Explanation**: A compatible local stdio adapter should implement this minimal handshake.

The one tool's inputSchema is an object with required command. command may be a string or an array of strings. The array form preserves empty strings and is the deterministic form for an adapter.

**Claim**: The tool schema has only required command, typed string or array with string items.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/McpServer.cs#L535-L563)):

~~~csharp
w.WriteString("name", "officecli");
w.WriteString("type", "object");
w.WriteStartObject("command");
w.WriteStartArray("type"); w.WriteStringValue("string"); w.WriteStringValue("array");
w.WriteStartObject("items"); w.WriteString("type", "string");
w.WriteStartArray("required"); w.WriteStringValue("command");
~~~

**Explanation**: Upstream has no command-specific or candidate-path constraint in its MCP schema.

**Claim**: String command input is quote-aware tokenization, not shell execution.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/McpServer.cs#L328-L360)):

~~~csharp
else if (ch == '"' || ch == '\'') { quote = ch; inTok = true; }
else if (char.IsWhiteSpace(ch)) { if (inTok) { tokens.Add(sb.ToString()); sb.Clear(); inTok = false; } }
return tokens.ToArray();
~~~

**Explanation**: Use structured argv in the adapter; it avoids reimplementing this tokenizer and platform quoting ambiguity.

**Claim**: Screenshot calls return a base64 PNG MCP image block; an upstream-created temporary file is deleted.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/McpServer.cs#L385-L420)):

~~~csharp
var b64 = Convert.ToBase64String(File.ReadAllBytes(outPath));
try { File.Delete(autoTemp); } catch { }
new McpContent("image", Data: b64, MimeType: "image/png"),
~~~

**Explanation**: Have the adapter create an adapter-owned PNG path, read it, return image/png data, and remove it. Do not return arbitrary filesystem paths.

## Mutations and candidate enforcement

The upstream MCP is a thin command-line shell; it passes caller argv to the shared CLI parser and the root registers broad mutation commands. It performs no candidate-root enforcement. Do not expose its free-form command tool where only candidate files may change.

**Claim**: Upstream MCP directly executes caller argv through the shared CLI.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/McpServer.cs#L283-L296)):

~~~csharp
var argv = ExtractArgv(args);
if (IsScreenshot(argv))
    return (RunScreenshotArgv(argv), false);
return SurfaceCliResult(RunCliRaw(argv));
~~~

**Explanation**: No policy layer validates a path or restricts verbs between MCP input and command execution.

**Claim**: The shared root registers document-mutating commands.

**Evidence** ([source](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/src/officecli/CommandBuilder.cs#L176-L195)):

~~~csharp
rootCommand.Add(BuildSetCommand(jsonOption));
rootCommand.Add(BuildAddCommand(jsonOption));
rootCommand.Add(BuildRemoveCommand(jsonOption));
rootCommand.Add(BuildRawSetCommand(jsonOption));
rootCommand.Add(BuildBatchCommand(jsonOption));
rootCommand.Add(BuildImportCommand(jsonOption));
rootCommand.Add(BuildCreateCommand(jsonOption));
rootCommand.Add(BuildMergeCommand(jsonOption));
~~~

**Explanation**: The adapter must allowlist verbs and resolve every file-bearing argument to a canonical candidate-root descendant, rejecting traversal and symlink/reparse-point escapes.

| State affected | Operations |
| --- | --- |
| Candidate Office files | create, set, add, remove, move, swap, raw-set, add-part, batch, import, merge, refresh, save, close. |
| Other file paths | screenshot/html/pdf --out, dump/export paths, template/import/source paths. |
| User/environment config | config autoUpdate/log, mcp registration/uninstall, install, skills/plugins, updater config and staged .update, auto-install. |
| MCP process environment | Upstream sets OFFICECLI_NO_AUTO_RESIDENT and OFFICECLI_BATCH_ALLOW_STDIN_REDIRECT if absent. |

Reject config, install, mcp registration, skills/plugins, resident management, and arbitrary-output operations in the adapter. Set OFFICECLI_SKIP_UPDATE=1 only in the spawned child's environment. Ensure every allowed source and destination is candidate-root confined.

## Local Core confirmation boundary

Manual candidate mutation requires Core confirmation. The local Core places a manual fixed-output run in `awaiting_confirmation` and only `office_os.py confirm` advances it to execution after the owner approves the compact task agreement. The Node adapter is deliberately narrower: it validates structured argv, command grammar, and candidate containment, but it does not receive, infer, or substitute for that Core state transition. Workflow guidance must therefore call `confirm` before creating or mutating a candidate and must not describe candidate-root validation as user consent.

## Apache-2.0 obligations

The repository ships Apache-2.0 LICENSE and a NOTICE that expressly requires retention on redistribution. If distributing OfficeCLI code or binary, include Apache-2.0, retain applicable copyright/patent/trademark/attribution notices and NOTICE, and mark changed adapter files. Apache-2.0 does not grant trademark rights; do not imply endorsement. Invoking a separately obtained executable avoids redistributing that artifact, but bundling it does not.

**Claim**: The pinned project identifies Apache-2.0 and requires retaining NOTICE for modified or unmodified redistributions.

**Evidence** ([LICENSE](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/LICENSE#L1-L20), [NOTICE](https://github.com/iOfficeAI/OfficeCLI/blob/d2d9c60f44537004c3e1f46680c24ea38d9659c2/NOTICE#L1-L9)):

~~~text
Apache License
Version 2.0, January 2004
...
Redistributions of this work, with or without modification, must retain this notice.
~~~

**Explanation**: Ship LICENSE and the upstream NOTICE with any redistributed OfficeCLI material.

## Codex plugin data bridge

OpenAI's current [Plugins in Codex](https://help.openai.com/en/articles/20001256-plugins-in-codex) documentation describes plugins as bundles of skills and app-backed capabilities, but it does not document a plugin-specific `PLUGIN_DATA` value being injected into the agent's general terminal environment. Local Codex verification likewise found no ambient `PLUGIN_DATA`, `PLUGIN_ROOT`, or plugin-specific `CODEX_*` variable in an ordinary terminal command, while registered Hook and MCP processes receive their plugin-owned environment.

Because one task may use several plugins, treating the general terminal as if it had one unambiguous plugin root would be unsound. Office OS therefore uses the registered Hook as the bridge: SessionStart and UserPromptSubmit inject the exact authoritative `PLUGIN_DATA` path into model context; `SKILL.md` requires every local core and manager child process to receive that exact value; and the MCP adapter continues to derive its fixed candidate/runtime root from its own plugin environment. A missing injected value stops writable work rather than silently creating a second temporary data tree. Installed-copy QA must exercise a real Codex-triggered workflow to prove that the Hook, core, manager, and MCP adapter converge on the same root.

## Implementation recommendation

1. Download the v1.0.135 binary from the version-pinned release path; independently hash it against SHA256SUMS; store it at an adapter-owned pinned path.
2. Implement one narrow stdio MCP tool using structured argv or operation fields. Never forward free-form upstream command text.
3. Allow only documented read and candidate mutation operations. Canonicalize all input, source, template, destination, and output paths under the candidate root before spawning.
4. Spawn normal pinned OfficeCLI commands with child-only OFFICECLI_SKIP_UPDATE=1. Never launch officecli mcp and never rely on config autoUpdate=false.
5. For screenshots, create controlled temporary output, read it, emit MCP image/png base64 data, and delete it.

Open questions: none
