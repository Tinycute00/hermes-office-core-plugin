# Versioning And Tags

## Version Policy

`office-core` uses SemVer after the first public v0.1 release. Until then, `0.1.0` remains the
unreleased package version and alpha tags may be used only for private validation packets.

- `v0.1.0-alpha.N`: private alpha validation tag after package, docs, and smoke evidence pass.
- `v0.1.0`: first v0.1 release candidate tag after E2E install evidence and CI pass.
- Patch tags: bug fixes that do not change the public tool or skill contract.
- Minor tags: new plugin tools, skills, install surfaces, or user-visible workflow capability.

## Tag Workflow

1. Open a draft PR against the repository default branch.
2. Attach local evidence for ruff, tests, build, `twine check`, package-data validation, and
   temporary install/smoke checks.
3. Wait for GitHub Actions CI to pass without secrets or publish tokens.
4. Update release notes from `.github/release-notes-template.md`.
5. Create the tag only after the PR is merged and release evidence is current.
6. Let `.github/workflows/tag-validation.yml` validate the tag. The workflow does not publish
   packages or create GitHub releases.

GitHub tracks source, branches, PRs, CI artifacts, tags, release notes, and downloadable package
artifacts. Linear TIN-5 remains the governance and evidence record.
