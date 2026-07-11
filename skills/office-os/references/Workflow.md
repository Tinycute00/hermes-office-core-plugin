# Workflow and decision system

## Contents

1. Runtime interfaces
2. State machine
3. Three loops
4. Object and QA routing
5. Publishing
6. Scheduling
7. Hook coordination
8. OMO design influence

## Runtime interfaces

Use these shapes as contracts between the skill, hook, and local core. Fields may be stored as JSON or SQLite rows, but their meaning stays stable.

### IntentEnvelope

    {
      intent: lookup | analyze | review | create | update | integrate | schedule,
      object: excel | word | powerpoint | pdf | cross-file,
      permission: read-only | fixed-output-write | scheduled-overwrite,
      qa: fast | enhanced | full
    }

The visible Chinese banner is a rendering of this envelope. The UserPromptSubmit hook may suggest invocation, but the skill owns final classification.

### OfficeTaskContract

    {
      task_key: stable normalized identifier,
      sources: canonical local paths,
      requested_result: short outcome,
      audience: optional,
      output_target: stable path under Office OS Output,
      business_rules: explicit owner rules,
      intent: IntentEnvelope,
      source_fingerprints: path plus size, mtime, and sha256
    }

### ChunkLocator

    {
      object: excel | word | powerpoint | pdf,
      file: canonical path,
      container: sheet | heading | slide | page,
      unit: range | topic | shape | section,
      dependency_keys: formula family, relationship, style, or cross-file join keys
    }

### RunState

    {
      run_id: unique id,
      task_key: stable id,
      status: grounding | interviewing | agreed | executing | validating | publishing | awaiting_user | complete | failed,
      total_units: integer,
      remaining_units: integer,
      progress_marker: content-derived changing value,
      continuation_count: 0..2,
      waiting_for_user: boolean,
      latest_summary: one replaceable record
    }

### ScheduleAuthorization

    {
      automation_id: stable existing id when present,
      task_key: stable id,
      prompt: begins with exact $office-os,
      local_project: canonical folder,
      cadence: user-approved schedule,
      permission: scheduled-overwrite,
      output_target: fixed path,
      qa: fast | enhanced | full
    }

## State machine

Use this transition sequence:

    detect → ground → interview ↔ clarify → agree
           → execute ↔ validate → publish
           → accept ↔ revise → schedule-offer → complete

Branches:

| Condition | Transition |
| --- | --- |
| Request is complete and read-only | ground → execute |
| One owner decision is missing | ground → interview |
| Candidate fails local validation | validate → execute for affected chunk |
| Source fingerprint changed mid-run | validate → ground |
| User requests a revision | accept → revise → execute |
| User declines scheduling | schedule-offer → complete |
| User accepts scheduling | schedule-offer → schedule authorization → complete |
| Unsupported writable format | ground → conversion request; preserve useful read-only branch |

Each state transition replaces the active state record; it does not append an event log forever.

## Three loops

Keep the full workflow understandable as three reusable loops. The same object skill may appear in more than one loop.

### 1. Clarification loop

Inspect first, identify the single highest-impact unknown, ask one question, incorporate the answer, and repeat only if another owner decision remains. Exit when OfficeTaskContract is checkable.

Completion criterion: sources, desired result, output identity, permission, and QA are all determined.

### 2. Chunk execution loop

Select the next dependency-safe ChunkLocator, perform the change or analysis, validate that unit, record a new progress marker, and select the next unit. Re-enter an object capability whenever a dependent chunk needs it.

Completion criterion: remaining_units is zero and every changed or flagged unit passes the selected QA.

### 3. Acceptance and recurrence loop

Publish the candidate, present results, receive acceptance or revision, and update the same stable output for revisions. Once accepted, offer scheduling once. If accepted, install or update one automation and close.

Completion criterion: the output is accepted or the read-only answer is delivered, scheduling is decided, active events are pruned, and latest_summary is the only retained run summary.

## Object and QA routing

| Signal | Object route | Minimum QA |
| --- | --- | --- |
| Cells, formulas, tables, workbook, reconciliation | Excel | Fast |
| Formula dependency, chart, pivot, cross-sheet totals | Excel | Enhanced |
| Headings, contract, report, tracked structure | Word | Fast |
| Sections, headers/footers, pagination or global styles | Word | Enhanced |
| Slides, deck, speaker notes, shape layout | PowerPoint | Fast |
| Master/layout/theme or cross-slide consistency | PowerPoint | Enhanced |
| Extract, summarize, compare PDF | PDF read-only | Fast |
| Multiple source files or formats | Cross-file plus each object reference | Enhanced when joins affect output |

Full QA requires an explicit request, a high-risk deliverable, or repeated anomalies after enhanced QA.

## Publishing

Build the candidate in the target directory or on the same volume. Validate before replacing. On Windows, the core prefers ReplaceFileW when replacing an existing target and can ask it to create .bak.1 during a scheduled publish. Elsewhere it uses same-filesystem os.replace.

Rotate scheduled backups before replacement:

    .bak.2 → .bak.3
    .bak.1 → .bak.2
    previous target → .bak.1

Delete the old .bak.3 before rotation. Manual publishing removes no source and creates no history. A source hash equal to the last successful scheduled fingerprint returns unchanged and performs no replacement or rotation.

Acquire one workspace single-flight lock before publish. A concurrent scheduled run returns overlap_skipped rather than waiting and then duplicating work.

## Scheduling

Offer scheduling after acceptance, never before the user sees the first result. Use Codex scheduled-task tooling.

The automation prompt must:

1. begin with exact $office-os;
2. name the stable sources and task key;
3. identify the fixed output and selected QA;
4. authorize scheduled overwrite only for that target;
5. require fingerprint no-op and overlap skip;
6. keep at most three fixed backups.

Search for an existing automation identity for the task key. Update it when found. Create one only when no matching identity exists. Local schedules require the ChatGPT desktop app and machine to remain running and the folder to be a local Codex project.

## Hook coordination

SessionStart injects a small pointer and active-state summary for startup, resume, clear, and compact. This includes the compact source so the design does not depend on PostCompact context injection.

UserPromptSubmit reads the current prompt directly because its matcher is ignored. It strips fenced code, uses high-precision Office signals, and stores a bounded dedup key from session id, turn id, and prompt hash.

Stop continues only an active executing, validating, or publishing run that has remaining work, is not waiting for the user, has new progress since the prior stop, and has used fewer than two continuations. One stop with no new progress ends automatic continuation.

Hooks guide behavior; they do not enforce source immutability. The skill contract, output core, and validation provide the actual workflow boundary.

## OMO design influence

Office OS borrows four ideas from OMO and changes their domain:

- Classify intent before orchestration, but expose the classification as a plain Chinese first line.
- Detect trigger language in the current prompt and ignore code blocks.
- Keep role behavior separate from routing and domain references.
- Continue unfinished work only with evidence of progress and a hard bound.

Unlike OMO's software-engineering orchestration, Office OS uses Office object chunks, stable derived outputs, local knowledge-map retrieval, and acceptance-to-schedule closure.

Pinned research revision: fb74d777b7cc9051996569918d6269fd3173b21f.

- [OMO roadmap](https://github.com/code-yeongyu/oh-my-openagent/blob/fb74d777b7cc9051996569918d6269fd3173b21f/ROADMAP.md)
- [OMO orchestration guide](https://github.com/code-yeongyu/oh-my-openagent/blob/fb74d777b7cc9051996569918d6269fd3173b21f/docs/guide/orchestration.md)
- [Codex ultrawork hook](https://github.com/code-yeongyu/oh-my-openagent/blob/fb74d777b7cc9051996569918d6269fd3173b21f/packages/omo-codex/plugin/components/ultrawork/src/codex-hook.ts)
- [Keyword detector](https://github.com/code-yeongyu/oh-my-openagent/blob/fb74d777b7cc9051996569918d6269fd3173b21f/packages/omo-opencode/src/hooks/keyword-detector/detector.ts)
- [Dynamic Sisyphus role](https://github.com/code-yeongyu/oh-my-openagent/blob/fb74d777b7cc9051996569918d6269fd3173b21f/packages/omo-opencode/src/agents/sisyphus-dynamic-prompt-role.ts)
- [OpenAI: Scheduled tasks](https://learn.chatgpt.com/docs/automations)
