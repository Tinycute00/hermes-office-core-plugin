# Release Checklist

Use this checklist before creating any public release packet. Do not publish to PyPI, create a
GitHub release, or mark the plugin public until local evidence and CI evidence both pass.

## Required Gates

- [ ] `pyproject.toml` and both `plugin.yaml` manifests use the intended version.
- [ ] `CHANGELOG.md` has release notes for the intended version.
- [ ] CI passes on the release PR or tag-validation workflow.
- [ ] `python -m ruff check .` passes.
- [ ] `python -m pytest -q` passes.
- [ ] `python -m build` creates exactly one wheel and one sdist.
- [ ] `python -m twine check dist/*` passes without publish tokens.
- [ ] `python scripts/qa/validate_package_data.py --repo . --dist-dir dist` proves
  `plugin.yaml`, skill files, docs, and package metadata are present in built artifacts.
- [ ] Direct-copy, GitHub install, and pip entry-point install evidence exist from a temporary
  `HERMES_HOME`.
- [ ] The release notes include the v0.1 draft-only external-write boundary and deferred scope.

## Prohibited Before Approval

- Do not publish to PyPI.
- Do not create a GitHub release.
- Do not switch the repository from private to public.
- Do not claim official marketplace acceptance.
- Do not execute untrusted issue, PR, document, spreadsheet, email, OCR, or log text as commands
  or release instructions.
