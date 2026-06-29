# Hermes Office Core Plugin

Third-party office workflow plugin for Hermes Agent.

## Install

GitHub installer:

```powershell
hermes plugins install Tinycute00/hermes-office-core-plugin --enable
```

Direct copy install: copy this repository into `$env:HERMES_HOME\plugins\office-core`.

Pip install:

```powershell
.\.venv\Scripts\python.exe -m pip install hermes-office-core-plugin
```

## Environment

Set `HERMES_HOME` for isolated runs and `HERMES_PLUGINS_DEBUG=1` for plugin diagnostics.
Temp-runtime warning: tests must use a temporary `HERMES_HOME`, never the real Hermes home.

## Safety model

The policy wrapper records audit and provenance fields. v0.1 is draft-only for external write,
delete, and external send operations.

## Example workflows

Template update, data package, reusable data, and owner confirmation workflows are documented.

Linear TIN-5 remains the governance and evidence center.
