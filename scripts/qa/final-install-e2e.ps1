[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$Evidence
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Runner = Join-Path $PSScriptRoot 'e2e-office-workflows.ps1'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Evidence) | Out-Null
Set-Content -LiteralPath $Evidence -Encoding UTF8 -Value @(
    'final-install-e2e',
    ('started_at: ' + (Get-Date).ToString('o')),
    "wrapper: delegates to $Runner",
    "repo_root_input: $RepoRoot"
)

& $Runner -RepoRoot $RepoRoot -Evidence $Evidence
exit $LASTEXITCODE
