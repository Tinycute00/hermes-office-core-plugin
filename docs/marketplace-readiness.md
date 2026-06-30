# Community and Marketplace Readiness

## Status

This repository is prepared for community and marketplace submission review. That means the
package has installation docs, security boundaries, usage examples, architecture notes,
validation scripts, and a release checklist. No official submission outcome exists yet.

Do not claim official listing, marketplace publication, or review success until a real
submission and approval record exists.

## Readiness Checklist

- [ ] Version is finalized in `pyproject.toml`, `plugin.yaml`, and release notes.
- [ ] Tag selected: `v0.1.0` for the first public v0.1 release; alpha tags may use
  `v0.1.0-alpha.N`.
- [ ] Versioning policy and tag workflow are followed from `docs/release/versioning-and-tags.md`.
- [ ] Branch pushed to `origin/codex/hermes-office-external-plugin`.
- [ ] Draft PR exists against the repository default branch, or PR creation is blocked with
  exact GitHub permission/auth evidence.
- [ ] GitHub Actions CI passes for ruff, tests, build, metadata, distribution, package data,
  docs, skills, and inventory validation without publish tokens.
- [ ] `hermes plugins install Tinycute00/hermes-office-core-plugin --enable` verified in a
  temporary runtime.
- [ ] Direct copy install verified into temporary `HERMES_HOME\plugins\office-core`.
- [ ] Pip install / entry-point discovery verified in an isolated Python environment.
- [ ] Docs validation evidence linked:
  `C:\Users\88697\AppData\Local\hermes\.omo\evidence\hermes-office-external-plugin\task-13-docs.txt`.
- [ ] Negative docs validation evidence linked:
  `C:\Users\88697\AppData\Local\hermes\.omo\evidence\hermes-office-external-plugin\task-13-docs-failure.txt`.
- [ ] Distribution and package-data validation evidence linked from Todo 14/15 before release.
- [ ] Release notes include safety boundaries and deferred scope.
- [ ] Release notes are prepared from `.github/release-notes-template.md`.
- [ ] Community promotion note is drafted for Nous community `#plugins-skills-and-skins`.

## Submission Packet

- README with GitHub installer, direct copy install, pip install, env var handling, temp-runtime
  test warning, usage, no-core-modification promise, and Linear governance note.
- SECURITY with safety model, prompt-injection boundary, confirmation behavior, and v0.1
  draft-only external-write policy.
- CONTRIBUTING with development commands and governance expectations.
- CHANGELOG with version/tag/evidence checklist.
- Architecture doc and example workflows.
- QA scripts: `validate_docs.py`, `validate_distribution.py`, `validate_package_data.py`,
  `validate_skills.py`, final quality, and install smoke scripts.

## Governance

Linear TIN-5 is the governance and evidence record for release rationale and deferred scope.
GitHub tracks code, review, CI, tags, release artifacts, and downloadable packages. GitHub Issues
and public support channels should remain aligned with the later public/community support plan.
