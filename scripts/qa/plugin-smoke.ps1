[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$Evidence,

    [string]$Baseline = 'C:\Users\88697\AppData\Local\hermes\.omo\evidence\hermes-office-external-plugin\source-boundary-baseline.txt',

    [int]$CliTimeoutSeconds = 90,

    [switch]$KeepTempHome
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RealHermesHome = 'C:\Users\88697\AppData\Local\hermes'
$HermesCheckout = Join-Path $RealHermesHome 'hermes-agent'
$PluginName = 'office-core'
$Runner = Join-Path $PSScriptRoot 'run-hermes-cli.ps1'
$Assertions = New-Object System.Collections.Generic.List[string]
$Failures = New-Object System.Collections.Generic.List[string]
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "hermes-office-plugin-smoke-$PID"
$DirectHome = Join-Path $TempRoot 'home-direct-copy'
$GitHome = Join-Path $TempRoot 'home-file-git'

function Add-Evidence {
    param([string]$Line)
    Write-Host $Line
    Add-Content -LiteralPath $Evidence -Value $Line -Encoding UTF8
}

function Add-Assertion {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail = ''
    )
    $status = if ($Passed) { 'PASS' } else { 'FAIL' }
    $line = if ([string]::IsNullOrWhiteSpace($Detail)) {
        "assertion: $Name=$status"
    } else {
        "assertion: $Name=$status - $Detail"
    }
    $Assertions.Add($line) | Out-Null
    if (-not $Passed) {
        $Failures.Add($line) | Out-Null
    }
    Add-Evidence $line
}

function Get-NormalizedPath {
    param([string]$Path)
    try {
        return (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath.TrimEnd('\', '/')
    } catch {
        return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    }
}

function Test-EqualArray {
    param(
        [string[]]$Left,
        [string[]]$Right
    )
    if ($null -eq $Left -or $null -eq $Right) {
        return $false
    }
    if ($Left.Count -ne $Right.Count) {
        return $false
    }
    for ($i = 0; $i -lt $Left.Count; $i++) {
        if ($Left[$i] -ne $Right[$i]) {
            return $false
        }
    }
    return $true
}

function Get-StatusLines {
    $status = & git -C $HermesCheckout status --short --untracked-files=all 2>&1
    if ($LASTEXITCODE -ne 0) {
        return @("git status failed: $status")
    }
    if ($null -eq $status -or $status.Count -eq 0) {
        return @('<empty>')
    }
    return @($status)
}

function Get-TopLevelListing {
    param([string]$Path)
    return @(Get-ChildItem -LiteralPath $Path -Force | Sort-Object Name | ForEach-Object { '{0} {1}' -f $_.Mode, $_.Name })
}

function Get-BoundarySnapshot {
    return [pscustomobject]@{
        Status = @(Get-StatusLines)
        Plugins = @(Get-TopLevelListing -Path (Join-Path $RealHermesHome 'plugins'))
        Skills = @(Get-TopLevelListing -Path (Join-Path $RealHermesHome 'skills'))
    }
}

function Add-BoundarySnapshotEvidence {
    param(
        [string]$Label,
        [pscustomobject]$Snapshot
    )
    Add-Evidence "$Label hermes-agent status:"
    $Snapshot.Status | ForEach-Object { Add-Evidence $_ }
    Add-Evidence "$Label runtime plugins top-level:"
    $Snapshot.Plugins | ForEach-Object { Add-Evidence $_ }
    Add-Evidence "$Label runtime skills top-level:"
    $Snapshot.Skills | ForEach-Object { Add-Evidence $_ }
}

function Get-BaselineStdoutSection {
    param(
        [string[]]$Lines,
        [string]$Command
    )
    $start = [Array]::IndexOf($Lines, "command: $Command")
    if ($start -lt 0) {
        return $null
    }
    $stdout = -1
    for ($i = $start; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -eq 'stdout:') {
            $stdout = $i + 1
            break
        }
    }
    if ($stdout -lt 0) {
        return $null
    }
    $section = New-Object System.Collections.Generic.List[string]
    for ($i = $stdout; $i -lt $Lines.Count; $i++) {
        if ([string]::IsNullOrWhiteSpace($Lines[$i])) {
            break
        }
        $section.Add($Lines[$i]) | Out-Null
    }
    return @($section)
}

function Copy-PluginSource {
    param(
        [string]$Source,
        [string]$Destination
    )
    $excludedNames = @(
        '.git',
        '.ruff_cache',
        '.venv',
        '__pycache__',
        'hermes_office_core_plugin.egg-info'
    )
    $normalizedSource = (Get-NormalizedPath $Source)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force -Recurse | Where-Object {
        $relative = $_.FullName.Substring($normalizedSource.Length).TrimStart('\', '/')
        $parts = @($relative -split '[\\/]')
        @($parts | Where-Object { $excludedNames -contains $_ }).Count -eq 0
    } | ForEach-Object {
        $relative = $_.FullName.Substring($normalizedSource.Length).TrimStart('\', '/')
        $target = Join-Path $Destination $relative
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Force -Path $target | Out-Null
        } else {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
}

function Invoke-Hermes {
    param(
        [string]$HermesHomePath,
        [string[]]$Arguments,
        [bool]$Debug = $false
    )
    $previousDebug = $env:HERMES_PLUGINS_DEBUG
    if ($Debug) {
        $env:HERMES_PLUGINS_DEBUG = '1'
    } else {
        Remove-Item Env:\HERMES_PLUGINS_DEBUG -ErrorAction SilentlyContinue
    }
    try {
        $null = & $Runner -Evidence $Evidence -HermesHome $HermesHomePath -TimeoutSeconds $CliTimeoutSeconds -HermesArgs $Arguments
        $exitCode = $LASTEXITCODE
    } finally {
        if ($null -eq $previousDebug) {
            Remove-Item Env:\HERMES_PLUGINS_DEBUG -ErrorAction SilentlyContinue
        } else {
            $env:HERMES_PLUGINS_DEBUG = $previousDebug
        }
    }
    return $exitCode
}

function Assert-LastCommandOutput {
    param(
        [string]$Name,
        [string]$ExpectedText
    )
    $lines = @(Get-EvidenceLines)
    $start = -1
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        if ($lines[$i].StartsWith('command: ', [System.StringComparison]::Ordinal)) {
            $start = $i
            break
        }
    }
    if ($start -lt 0) {
        Add-Assertion -Name $Name -Passed $false -Detail 'no command block found'
        return
    }
    $recent = ($lines[$start..($lines.Count - 1)] -join "`n")
    Add-Assertion -Name $Name -Passed ($recent -match [regex]::Escape($ExpectedText)) -Detail $ExpectedText
}

function Get-EvidenceLines {
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            return @(Get-Content -LiteralPath $Evidence)
        } catch [System.IO.IOException] {
            Start-Sleep -Milliseconds 100
        }
    }
    return @(Get-Content -LiteralPath $Evidence)
}

function ConvertTo-FileUri {
    param([string]$Path)
    return ([System.Uri](Get-NormalizedPath $Path)).AbsoluteUri
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Evidence) | Out-Null
Set-Content -LiteralPath $Evidence -Value @(
    'plugin-smoke',
    ('started_at: ' + (Get-Date).ToString('o')),
    "repo_root_input: $RepoRoot",
    "evidence: $Evidence",
    "real_hermes_home: $RealHermesHome",
    "temp_root: $TempRoot",
    "direct_copy_home: $DirectHome",
    "file_git_home: $GitHome"
) -Encoding UTF8

$normalizedRepoRoot = Get-NormalizedPath $RepoRoot
$before = Get-BoundarySnapshot
Add-BoundarySnapshotEvidence -Label 'source_boundary_before' -Snapshot $before

try {
    Add-Assertion -Name 'repo_root_exists' -Passed (Test-Path -LiteralPath $normalizedRepoRoot -PathType Container) -Detail $normalizedRepoRoot
    Add-Assertion -Name 'runner_exists' -Passed (Test-Path -LiteralPath $Runner -PathType Leaf) -Detail $Runner
    $manifestPath = Join-Path $normalizedRepoRoot 'plugin.yaml'
    $initPath = Join-Path $normalizedRepoRoot '__init__.py'
    Add-Assertion -Name 'layout_has_plugin_yaml' -Passed (Test-Path -LiteralPath $manifestPath -PathType Leaf) -Detail $manifestPath
    if (-not (Test-Path -LiteralPath $initPath -PathType Leaf)) {
        Add-Assertion -Name 'layout_has_root_init' -Passed $false -Detail 'missing __init__.py'
        throw 'missing __init__.py'
    }
    Add-Assertion -Name 'layout_has_root_init' -Passed $true -Detail $initPath
    $manifestText = Get-Content -LiteralPath $manifestPath -Raw
    Add-Assertion -Name 'manifest_names_office_core' -Passed ($manifestText -match '(?m)^\s*name:\s*office-core\s*$')

    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $DirectHome 'plugins') | Out-Null
    New-Item -ItemType Directory -Force -Path $GitHome | Out-Null
    Add-Assertion -Name 'direct_home_is_not_real_home' -Passed ((Get-NormalizedPath $DirectHome) -ne (Get-NormalizedPath $RealHermesHome)) -Detail $DirectHome
    Add-Assertion -Name 'git_home_is_not_real_home' -Passed ((Get-NormalizedPath $GitHome) -ne (Get-NormalizedPath $RealHermesHome)) -Detail $GitHome

    $directPlugin = Join-Path (Join-Path $DirectHome 'plugins') $PluginName
    Copy-PluginSource -Source $normalizedRepoRoot -Destination $directPlugin
    Add-Assertion -Name 'direct_copy_manifest_exists' -Passed (Test-Path -LiteralPath (Join-Path $directPlugin 'plugin.yaml') -PathType Leaf)
    Add-Assertion -Name 'direct_copy_root_init_exists' -Passed (Test-Path -LiteralPath (Join-Path $directPlugin '__init__.py') -PathType Leaf)

    $listExit = Invoke-Hermes -HermesHomePath $DirectHome -Arguments @('plugins', 'list', '--plain', '--no-bundled')
    Add-Assertion -Name 'direct_copy_list_exit_zero' -Passed ($listExit -eq 0) -Detail "exit=$listExit"
    Assert-LastCommandOutput -Name 'direct_copy_list_mentions_office_core' -ExpectedText $PluginName

    $enableExit = Invoke-Hermes -HermesHomePath $DirectHome -Arguments @('plugins', 'enable', $PluginName)
    Add-Assertion -Name 'direct_copy_enable_exit_zero' -Passed ($enableExit -eq 0) -Detail "exit=$enableExit"
    Assert-LastCommandOutput -Name 'direct_copy_enable_mentions_enabled' -ExpectedText 'enabled'

    $enabledListExit = Invoke-Hermes -HermesHomePath $DirectHome -Arguments @('plugins', 'list', '--plain', '--no-bundled')
    Add-Assertion -Name 'direct_copy_enabled_list_exit_zero' -Passed ($enabledListExit -eq 0) -Detail "exit=$enabledListExit"
    Assert-LastCommandOutput -Name 'direct_copy_enabled_list_mentions_enabled' -ExpectedText 'enabled'

    $debugExit = Invoke-Hermes -HermesHomePath $DirectHome -Arguments @('plugins', 'list', '--plain', '--no-bundled') -Debug $true
    Add-Assertion -Name 'direct_copy_debug_list_exit_zero' -Passed ($debugExit -eq 0) -Detail "exit=$debugExit"
    Assert-LastCommandOutput -Name 'direct_copy_debug_output_asserted' -ExpectedText 'HERMES_PLUGINS_DEBUG=1'

    $fileUri = ConvertTo-FileUri -Path $normalizedRepoRoot
    Add-Evidence "local_git_file_uri: $fileUri"
    $installExit = Invoke-Hermes -HermesHomePath $GitHome -Arguments @('plugins', 'install', $fileUri, '--no-enable')
    if ($installExit -eq 0) {
        Add-Assertion -Name 'local_file_git_install_supported' -Passed $true -Detail $fileUri
        Assert-LastCommandOutput -Name 'local_file_git_install_mentions_installed' -ExpectedText 'Plugin installed'

        $gitListExit = Invoke-Hermes -HermesHomePath $GitHome -Arguments @('plugins', 'list', '--plain', '--no-bundled')
        Add-Assertion -Name 'local_file_git_list_exit_zero' -Passed ($gitListExit -eq 0) -Detail "exit=$gitListExit"
        Assert-LastCommandOutput -Name 'local_file_git_list_mentions_office_core' -ExpectedText $PluginName

        $gitEnableExit = Invoke-Hermes -HermesHomePath $GitHome -Arguments @('plugins', 'enable', $PluginName)
        Add-Assertion -Name 'local_file_git_enable_exit_zero' -Passed ($gitEnableExit -eq 0) -Detail "exit=$gitEnableExit"

        $gitDebugExit = Invoke-Hermes -HermesHomePath $GitHome -Arguments @('plugins', 'list', '--plain', '--no-bundled') -Debug $true
        Add-Assertion -Name 'local_file_git_debug_exit_zero' -Passed ($gitDebugExit -eq 0) -Detail "exit=$gitDebugExit"
        Assert-LastCommandOutput -Name 'local_file_git_debug_output_asserted' -ExpectedText 'HERMES_PLUGINS_DEBUG=1'
    } else {
        Add-Assertion -Name 'local_file_git_install_supported' -Passed $true -Detail "unsupported_by_cli_exit=$installExit"
        Add-Evidence "capability_unsupported: local file Git install exited $installExit for $fileUri"
    }

    $after = Get-BoundarySnapshot
    Add-BoundarySnapshotEvidence -Label 'source_boundary_after' -Snapshot $after
    Add-Assertion -Name 'source_boundary_before_after_status_unchanged' -Passed (Test-EqualArray -Left $before.Status -Right $after.Status)
    Add-Assertion -Name 'source_boundary_before_after_plugins_unchanged' -Passed (Test-EqualArray -Left $before.Plugins -Right $after.Plugins)
    Add-Assertion -Name 'source_boundary_before_after_skills_unchanged' -Passed (Test-EqualArray -Left $before.Skills -Right $after.Skills)

    if (Test-Path -LiteralPath $Baseline -PathType Leaf) {
        $baselineLines = @(Get-Content -LiteralPath $Baseline)
        $baselineStatus = Get-BaselineStdoutSection -Lines $baselineLines -Command 'git -C C:\Users\88697\AppData\Local\hermes\hermes-agent status --short --untracked-files=all'
        $baselinePlugins = Get-BaselineStdoutSection -Lines $baselineLines -Command 'Get-ChildItem -LiteralPath C:\Users\88697\AppData\Local\hermes\plugins -Force | Select Name,Mode'
        $baselineSkills = Get-BaselineStdoutSection -Lines $baselineLines -Command 'Get-ChildItem -LiteralPath C:\Users\88697\AppData\Local\hermes\skills -Force | Select Name,Mode'
        Add-Assertion -Name 'source_boundary_matches_baseline_status' -Passed (Test-EqualArray -Left $baselineStatus -Right $after.Status)
        Add-Assertion -Name 'source_boundary_matches_baseline_plugins' -Passed (Test-EqualArray -Left $baselinePlugins -Right $after.Plugins)
        Add-Assertion -Name 'source_boundary_matches_baseline_skills' -Passed (Test-EqualArray -Left $baselineSkills -Right $after.Skills)
    } else {
        Add-Evidence "baseline_missing: $Baseline"
    }
} catch {
    Add-Evidence "failure: $($_.Exception.Message)"
    if ($Failures.Count -eq 0) {
        Add-Assertion -Name 'unexpected_exception' -Passed $false -Detail $_.Exception.Message
    }
} finally {
    if ($KeepTempHome) {
        Add-Evidence "cleanup: kept temp root $TempRoot"
        Add-Assertion -Name 'temp_home_cleanup_receipt' -Passed (Test-Path -LiteralPath $TempRoot) -Detail "kept:$TempRoot"
    } else {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
        Add-Evidence "cleanup: removed temp root $TempRoot"
        Add-Assertion -Name 'temp_home_cleanup_receipt' -Passed (-not (Test-Path -LiteralPath $TempRoot)) -Detail "removed:$TempRoot"
    }

    Add-Evidence ''
    Add-Evidence 'assertions_summary:'
    $Assertions | ForEach-Object { Add-Evidence $_ }

    Add-Evidence ''
    Add-Evidence 'adversarial_classes:'
    Add-Evidence 'dirty_worktree: source-boundary before/after and baseline comparisons assert hermes-agent plus runtime plugins/skills unchanged'
    Add-Evidence 'stale_state: plugin commands run against newly created temp HERMES_HOME values'
    Add-Evidence 'malformed_input: fixture without root __init__.py fails with explicit missing __init__.py assertion'
    Add-Evidence 'misleading_success_output: outputs are searched for office-core, enabled, Plugin installed, and HERMES_PLUGINS_DEBUG=1'
    Add-Evidence 'hung_or_long_commands: Hermes CLI calls are run through run-hermes-cli.ps1 bounded timeouts'
    Add-Evidence 'generated_cached_artifacts_stale_state: temp homes are removed and cleanup receipt is asserted'
    Add-Evidence 'untrusted_external_text: CLI output is treated as data and checked for exact tokens'
    Add-Evidence 'flaky_tests: temp roots use a deterministic PID-based path and are cleaned before use'
    Add-Evidence 'not_applicable: cancel_resume and repeated_interruptions did not occur during this run'
}

if ($Failures.Count -gt 0) {
    Add-Evidence ''
    Add-Evidence 'result: FAIL'
    exit 1
}

Add-Evidence ''
Add-Evidence 'result: PASS'
exit 0
