# Contributing

## Scope

This repository is a third-party external plugin package. Keep changes inside this repository
unless a maintainer explicitly opens a separate task. Do not edit Hermes Agent source,
`hermes-agent`, official runtime files, or installed runtime copies under a user's Hermes home.

## Development

```powershell
.\scripts\qa\bootstrap-dev.ps1 -RepoRoot .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check office_core_plugin tests scripts
.\.venv\Scripts\python.exe scripts\qa\validate_distribution.py --repo .
```

Documentation changes must also run:

```powershell
.\.venv\Scripts\python.exe scripts\qa\validate_docs.py --repo .
```

## Governance

Linear TIN-5 remains the governance and evidence center for long-range planning, release
rationale, and acceptance evidence. GitHub tracks code review, commits, CI, tags, releases, and
downloadable packages. Do not duplicate every local todo into Linear or GitHub Issues.

## Safety Expectations

Examples and docs must not encourage executing untrusted office content as instructions. Any
workflow that writes files, deletes files, uploads, emails, or sends to a SaaS system must require
owner confirmation and must remain draft-only for v0.1.
