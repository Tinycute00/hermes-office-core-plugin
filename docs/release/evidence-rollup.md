# Evidence Rollup

This rollup covers Todo 16 for the standalone external Hermes plugin repository.

- Product repo: `https://github.com/Tinycute00/hermes-office-core-plugin`
- Branch: `codex/hermes-office-external-plugin`
- Local source root: `E:\N8N\hermes-office-core-plugin`
- Evidence root: `C:\Users\88697\AppData\Local\hermes\.omo\evidence\hermes-office-external-plugin`
- Linear issue: `TIN-5`

## Release Readiness

Current status: ready for governance handoff, not public release publication.

Evidence supports the v0.1 standalone plugin readiness gates:

- Repository boundary: `task-02-repo.txt` proves `Tinycute00/hermes-office-core-plugin`, branch tracking, private repository metadata, topics, and GitHub Issues/Wiki disabled.
- Source boundary: `source-boundary-baseline.txt` records the official Hermes checkout and runtime plugin/skill baseline used for later no-mutation comparisons.
- Distribution: `task-03-distribution.txt` and `task-14-build.txt` prove root `plugin.yaml`, Python package metadata, `office-core` entry point, package build, and package data.
- Install and load: `task-04-install-smoke.txt`, `task-15-direct-copy-install.txt`, and `task-15-final-install-e2e-remote-merge.txt` prove direct-copy install, local Git install, `hermes plugins enable office-core`, debug listing, GitHub owner/repo install, and pip entry-point discovery in isolated temp homes.
- Runtime behavior: `task-06-handler-contract.txt`, `task-08-policy-audit.txt`, and `task-15-e2e-remote-merge.txt` prove JSON handler envelopes, policy gates inside tool execution, owner confirmation, denied high-impact operations, and representative office workflows.
- Docs and governance: `task-13-docs.txt`, `task-14-final-quality-remediation.txt`, and this `docs/release/evidence-rollup.md` document install modes, security model, deferred scope, release checklist, and Linear governance handoff.

Release constraints still apply:

- Do not publish to PyPI.
- Do not create a GitHub release.
- Do not switch the repository public.
- Do not claim official marketplace acceptance.
- v0.1 has no confirmed external writes. Confirmed external writes, deletes, SaaS sends, and user-file mutations remain deferred; v0.1 returns drafts, previews, confirmation records, or bridge handoff instructions only.

## Install Commands

Hermes installer:

```powershell
hermes plugins install Tinycute00/hermes-office-core-plugin --enable
hermes plugins list
$env:HERMES_PLUGINS_DEBUG = "1"
hermes plugins list
```

Direct copy:

```powershell
$env:HERMES_HOME = "$HOME\.hermes"
New-Item -ItemType Directory -Force "$env:HERMES_HOME\plugins" | Out-Null
Copy-Item -Recurse -Force "E:\N8N\hermes-office-core-plugin" "$env:HERMES_HOME\plugins\office-core"
hermes plugins enable office-core
```

Pip entry point:

```powershell
.\.venv\Scripts\python.exe -m pip install hermes-office-core-plugin
hermes plugins list
```

## Deferred Scope

Deferred scope for v0.1:

- Public repository flip, public support process, GitHub Issues enablement, and Wiki enablement.
- PyPI publication, GitHub release creation, and official marketplace/community acceptance claims.
- Full Google/Microsoft/Feishu OAuth adapters, SaaS writes, email sends, deletes, uploads, and user-file mutation execution.
- UI dashboard, large database/vector index, and replacement Kanban/Excel/Word/PDF/PPT engines.
- Linear issue status changes for parent or children. Status remains evidence-led and governance-only in TIN-5.

## Linear And Milestone Status

Linear TIN-5 remains the governance/evidence center. GitHub tracks code, branches, commits, CI, PRs, tags, release notes, and package artifacts only.

Observed Linear status before this handoff:

- Parent: `TIN-5` is `Backlog`.
- Children: `TIN-6` through `TIN-21` are all `Backlog`.
- Matching Linear project milestones: none returned by Linear project search for `Hermes Office`.
- Local plan waves 0 through 5 have task evidence captured through Todo 15. No Linear issue status was changed from this rollup.

## Canonical Acceptance Evidence

| Gate | Scenario | Invocation | Binary observable | Captured artifact |
| --- | --- | --- | --- | --- |
| Repo boundary | Standalone external repo, correct remote, GitHub metadata | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\validate_repo_boundary.ps1 ...` | exit 0, expected repo/branch/upstream, Issues/Wiki disabled | `task-02-repo.txt` |
| Distribution | Plugin root and pip entry point | `.\.venv\Scripts\python.exe scripts\qa\validate_distribution.py --repo .` | exit 0, `office-core` manifest and entry point | `task-03-distribution.txt` |
| Install smoke | Direct copy and local Git temp runtime | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\plugin-smoke.ps1 ...` | exit 0, `office-core` list/enable/debug, source boundary unchanged | `task-04-install-smoke.txt` |
| Handler contract | JSON envelopes and no handler raises | `.\.venv\Scripts\python.exe -m pytest tests/test_handler_contract.py -q` | exit 0, JSON strings for success/failure/confirmation paths | `task-06-handler-contract.txt` |
| Policy/audit | Operation wrapper, confirmation, denied high-impact actions | `.\.venv\Scripts\python.exe -m pytest tests/test_operation_policy.py -q` | exit 0, draft-only write/send/delete handling | `task-08-policy-audit.txt` |
| Registry/data maps | Template registry, source map, data map | `.\.venv\Scripts\python.exe -m pytest tests/test_registry_models.py tests/test_data_maps.py -q` | exit 0, owner confirmation for low confidence | `task-09-registries.txt` |
| Local file adapter | Candidate discovery under allowlisted roots | `.\.venv\Scripts\python.exe -m pytest tests/test_local_files_adapter.py -q` | exit 0, traversal and escape rejection | `task-10-local-files.txt` |
| Bridge planner | Skill/MCP/Kanban/document handoff | `.\.venv\Scripts\python.exe -m pytest tests/test_skill_bridge.py -q` | exit 0, missing capability fallback instead of fake success | `task-11-bridge.txt` |
| Plugin skills | Shipped office workflow skills | `.\.venv\Scripts\python.exe scripts\qa\validate_skills.py --repo .` | exit 0, qualified `office-core:*` skill files validated | `task-12-skills.txt` |
| Docs | Install, security, usage, marketplace readiness | `.\.venv\Scripts\python.exe scripts\qa\validate_docs.py --repo .` | exit 0, all required docs assertions pass | `task-13-docs.txt` |
| Build/CI/package | Ruff, build, package data, remote branch | `.\.venv\Scripts\python.exe -m ruff check .; .\.venv\Scripts\python.exe -m build; ...` | exit 0, wheel/sdist include plugin data | `task-14-build.txt` |
| Final quality | Full quality gate | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\final-quality.ps1 ...` | exit 0, lint/tests/build/docs/skills/inventory/distribution pass | `task-14-final-quality-remediation.txt` |
| E2E workflows | Representative office workflows | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\e2e-office-workflows.ps1 ...` | exit 0, artifacts for template/data/source/bridge/denied operation | `task-15-e2e-remote-merge.txt` |
| Install E2E | GitHub install and pip entry point | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\final-install-e2e.ps1 ...` | exit 0, owner/repo install, `office-core` manifest, pip entry point | `task-15-final-install-e2e-remote-merge.txt` |
| Linear handoff | Governance/evidence comment proof | `mcp__codex_apps__linear._list_comments({"issueId":"TIN-5","limit":50,"orderBy":"updatedAt"})` | newest proof includes `evidence-rollup`, `hermes plugins install`, and `deferred scope` | `task-16-linear-comments.json` |
| Scope fidelity | Final standalone scope gate | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\final-scope-fidelity.ps1 ...` | exit 0, evidence/Linear/GitHub/scope assertions pass, local `HEAD` equals upstream | `task-16-linear-rollup.md` |
| Todo 16 remediation | Final rollup blocker remediation proof | `git diff --check` and product/official Hermes cleanliness probes | exit 0, focused formatting check, product `HEAD` equals upstream, official Hermes tracked/staged state clean | `task-16-remediation-formatting.txt`, `task-16-remediation-proof.txt` |
| Todo 16 remediation rerun | E2E, static, smoke, scope, remote-list, upstream, and cleanliness remediation proof | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\e2e-office-workflows.ps1 ...`; `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\plugin-smoke.ps1 ...`; `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qa\final-scope-fidelity.ps1 ...`; `git diff --check`; product and official Hermes git probes | exit 0 for the rerun E2E/smoke/static/scope checks, current-command stdout assertion proof, product `HEAD` equals upstream, official Hermes tracked/staged state clean | `task-16-e2e-remediation-full.txt`, `task-16-e2e-assertion-remediation.txt`, `task-16-plugin-smoke-remediation.txt`, `task-16-static-remediation-checks.txt`, `task-16-final-scope-remediation.txt`, `task-16-remote-list-remediation-summary.txt`, `task-16-upstream-and-cleanliness-remediation.txt`, `task-16-gate-blocker-remediation.txt` |

## Full Task Evidence Inventory

All paths below are relative to the evidence root.

- Todo 1: `task-01-done-claim.json`, `task-01-linear-supersession.md` - Linear supersession and standalone external plugin governance correction.
- Todo 2: `task-02-done-claim.json`, `task-02-repo.txt` - standalone local/GitHub repository boundary and metadata.
- Todo 3: `task-03-bootstrap.txt`, `task-03-distribution.txt`, `task-03-distribution-failure.txt`, `task-03-done-claim.json` - Python bootstrap, distribution contract, and negative fixture.
- Todo 4: `task-04-done-claim.json`, `task-04-install-smoke.txt`, `task-04-install-smoke-failure.txt` - isolated Hermes install smoke and bad-layout failure path.
- Todo 5: `task-05-done-claim.json`, `task-05-inventory.txt`, `task-05-inventory-failure.txt`, `task-05-remediation-done-claim.json` - skill/MCP inventory and missing fallback validation.
- Todo 6: `task-06-code-review.txt`, `task-06-done-claim.json`, `task-06-fifth-remediation-done-claim.json`, `task-06-fourth-remediation-done-claim.json`, `task-06-gate-review.md`, `task-06-handler-contract.txt`, `task-06-manual-qa-matrix.md`, `task-06-remediation-done-claim.json`, `task-06-second-remediation-done-claim.json`, `task-06-third-remediation-done-claim.json`, `task-06-third-remediation-verification.txt` - handler JSON contract, no-raise behavior, redaction, review, and remediation evidence.
- Todo 7: `task-07-actual-host-remediation-done-claim.json`, `task-07-code-review.txt`, `task-07-done-claim.json`, `task-07-evidence-remediation-done-claim.json`, `task-07-gate-review.md`, `task-07-manual-qa.txt`, `task-07-manual-qa-matrix.md`, `task-07-notepad.md`, `task-07-real-host-remediation-done-claim.json`, `task-07-register.txt`, `task-07-remediation-done-claim.json`, `task-07-strict-remediation-done-claim.json` - register flow, host rollback/fail-closed behavior, manual QA, and review evidence.
- Todo 8: `task-08-code-review.txt`, `task-08-done-claim.json`, `task-08-evidence-remediation-done-claim.json`, `task-08-evidence-remediation-verification.txt`, `task-08-gate-review.md`, `task-08-kind-stale-remediation.txt`, `task-08-kind-stale-remediation-done-claim.json`, `task-08-kind-stale-remediation-probe.json`, `task-08-manual-probe.json`, `task-08-manual-qa-matrix.md`, `task-08-notepad.md`, `task-08-policy-audit.txt` - policy wrapper, audit model, draft-only high-impact handling, stale/kind remediation, and review evidence.
- Todo 9: `task-09-code-review.txt`, `task-09-done-claim.json`, `task-09-gate-review.md`, `task-09-manual-probe.json`, `task-09-manual-qa-matrix.md`, `task-09-notepad.md`, `task-09-registries.txt`, `task-09-remediation.txt`, `task-09-remediation-done-claim.json` - registry models, data/source maps, owner confirmation, manual probe, and review evidence.
- Todo 10: `task-10-code-review.txt`, `task-10-done-claim.json`, `task-10-gate-review.md`, `task-10-local-files.txt`, `task-10-manual-probe.json`, `task-10-manual-qa-matrix.md`, `task-10-notepad.md`, `task-10-remediation.txt`, `task-10-remediation-done-claim.json` - safe candidate file discovery and path/secret redaction remediation.
- Todo 11: `task-11-bridge.txt`, `task-11-code-review.txt`, `task-11-done-claim.json`, `task-11-gate-review.md`, `task-11-manual-probe.json`, `task-11-manual-qa-matrix.md`, `task-11-notepad.md`, `task-11-redaction-done-claim.json`, `task-11-redaction-manual-probe.json`, `task-11-redaction-remediation.txt` - bridge planner, missing capability fallback, redaction, and review evidence.
- Todo 12: `task-12-baseline.txt`, `task-12-code-review.txt`, `task-12-done-claim.json`, `task-12-evidence-remediation-done-claim.json`, `task-12-gate-review.md`, `task-12-manual-probe.json`, `task-12-manual-qa-matrix.md`, `task-12-notepad.md`, `task-12-skills.txt`, `task-12-skills-failure.txt`, `task-12-verification.txt` - plugin-shipped skill files, skill validator pass/fail evidence, and review evidence.
- Todo 13: `task-13-code-review.txt`, `task-13-docs.txt`, `task-13-docs-failure.txt`, `task-13-done-claim.json`, `task-13-gate-review.md`, `task-13-manual-probe.json`, `task-13-manual-qa-matrix.md`, `task-13-notepad.md`, `task-13-standard-verification.txt` - documentation validator pass/fail, marketplace truthfulness, and review evidence.
- Todo 14: `task-14-bootstrap.txt`, `task-14-build.txt`, `task-14-ci-rerun.txt`, `task-14-code-review.txt`, `task-14-done-claim.json`, `task-14-final-quality.txt`, `task-14-final-quality-debug.txt`, `task-14-final-quality-remediation.txt`, `task-14-gate-review.md`, `task-14-github-pr.txt`, `task-14-manual-probe.json`, `task-14-manual-qa-matrix.md`, `task-14-notepad.md`, `task-14-package-data-failure.txt`, `task-14-pr-body.md`, `task-14-remediation.txt`, `task-14-remediation-done-claim.json`, `task-14-package-data-omission-fixture\missing-plugin\__init__.py`, `task-14-package-data-omission-fixture\missing-plugin\pyproject.toml`, `task-14-package-data-omission-fixture\missing-plugin\office_core_plugin\__init__.py` - CI/build/package checks, PR proof, final quality, package-data negative fixture, and review evidence.
- Todo 15: `task-15-ambiguous-probe.json`, `task-15-code-review.txt`, `task-15-direct-copy-install.txt`, `task-15-done-claim.json`, `task-15-e2e.txt`, `task-15-e2e-failure.txt`, `task-15-e2e-failure-remediation.txt`, `task-15-e2e-remediation.txt`, `task-15-e2e-remote-merge.txt`, `task-15-e2e-remote-merge-ambiguous.txt`, `task-15-final-install-e2e.txt`, `task-15-final-install-e2e-remediation.txt`, `task-15-final-install-e2e-remote-merge.txt`, `task-15-gate-review.md`, `task-15-manual-probe.json`, `task-15-manual-probe-remote-merge.json`, `task-15-manual-qa-matrix.md`, `task-15-notepad.md`, `task-15-remediation.txt`, `task-15-remediation-done-claim.json`, `task-15-remote-install-resolution.txt`, `task-15-remote-merge-done-claim.json`, `task-15-gate-rerun\task-15-ambiguous-probe.json`, `task-15-gate-rerun\task-15-direct-copy-install.txt`, `task-15-gate-rerun\task-15-gate-e2e.txt`, `task-15-gate-rerun\task-15-gate-e2e-ambiguous.txt`, `task-15-gate-rerun\task-15-gate-final-install-e2e.txt`, `task-15-gate-rerun\task-15-manual-probe.json`, `task-15-workflow-artifacts\workflow-probe.json`, `task-15-workflow-artifacts\state\audit-log.jsonl`, `task-15-workflow-artifacts\state\data-map.json`, `task-15-workflow-artifacts\state\source-map.json`, `task-15-workflow-artifacts\state\template-registry.json` - representative E2E workflows, ambiguous latest/main failure path, direct-copy install, GitHub owner/repo install after default-branch merge, pip entry point, workflow artifacts, and review evidence.
- Todo 16: `task-16-linear-comments.json`, `task-16-code-review.txt`, `task-16-manual-qa-matrix.md`, `task-16-notepad.md`, `task-16-linear-rollup.md`, `task-16-formatting.txt`, `task-16-remediation-formatting.txt`, `task-16-remediation-proof.txt`, `task-16-e2e-remediation-full.txt`, `task-16-e2e-assertion-remediation.txt`, `task-16-plugin-smoke-remediation.txt`, `task-16-static-remediation-checks.txt`, `task-16-final-scope-remediation.txt`, `task-16-remote-list-remediation-summary.txt`, `task-16-upstream-and-cleanliness-remediation.txt`, `task-16-gate-blocker-remediation.txt` - Linear governance/evidence handoff proof, review bundle, final scope-fidelity gate, remediation notes, focused formatting/syntax checks, full E2E/smoke/static/scope reruns, current-command stdout assertion proof, remote list remediation summary, upstream/cleanliness proof, and final blocker remediation proof. All listed Todo 16 remediation artifacts exist under the evidence root and were checked as non-empty during the Todo 16 gate-blocker remediation.

## Adversarial Self-Check

- stale_state: this rollup cites current evidence files and Todo 16 validates live git, GitHub metadata, upstream tracking equality, and Linear proof.
- dirty_worktree: Todo 16 final script and git-master commit flow check product and official Hermes worktree state.
- misleading_success_output: final-scope-fidelity asserts file existence, non-empty artifacts, exact strings, parsed GitHub JSON, and exit codes.
- overfit_slop: release readiness uses multiple independent artifacts across repo boundary, docs, build, install, E2E, and Linear proof.
- prompt_injection: evidence and Linear bodies are treated as data; no untrusted text is executed.
- malformed_input: missing required evidence files and wrong repo paths fail nonzero.
- hung_or_long_commands: QA scripts use bounded subprocesses where they invoke external commands.
