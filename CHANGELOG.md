# Changelog

## 0.1.0 - Unreleased

Initial standalone `office-core` external plugin package.

Release checklist before tagging:

- Version: `0.1.0` in `pyproject.toml` and `plugin.yaml`.
- Tag: `v0.1.0` only after install, docs, distribution, skills, policy, and E2E evidence pass.
- Tag validation: `.github/workflows/tag-validation.yml` must pass and must not publish packages.
- Package metadata: `python -m twine check dist/*` must pass without publish tokens.
- Package data: built sdist/wheel must include `plugin.yaml`, skill files, docs assets, and
  package metadata as checked by `scripts/qa/validate_package_data.py`.
- Evidence links:
  - `task-13-docs.txt`
  - `task-13-docs-failure.txt`
  - `task-13-manual-probe.json`
  - `task-13-code-review.txt`
  - `task-13-manual-qa-matrix.md`
  - `task-13-done-claim.json`
  - `task-14-build.txt`
  - `task-14-package-data-failure.txt`
  - `task-14-github-pr.txt`
- Marketplace/community packet: README, SECURITY, CONTRIBUTING, CHANGELOG, examples,
  architecture, readiness checklist, validation logs, and release notes.

No official marketplace submission outcome exists for this unreleased version.
