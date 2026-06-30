[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$Evidence,

    [ValidateSet('Happy', 'Ambiguous')]
    [string]$Scenario = 'Happy',

    [int]$TimeoutSeconds = 300,

    [switch]$KeepTemp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$PluginName = 'office-core'
$RemoteSpec = 'Tinycute00/hermes-office-core-plugin'
$RemoteSubdirSpec = "$RemoteSpec/office_core_plugin"
$RemoteBranchTreeSpec = 'https://github.com/Tinycute00/hermes-office-core-plugin/tree/codex/hermes-office-external-plugin/office_core_plugin'
$HermesCheckout = 'C:\Users\88697\AppData\Local\hermes\hermes-agent'
$Runner = Join-Path $PSScriptRoot 'run-hermes-cli.ps1'
$PluginSmoke = Join-Path $PSScriptRoot 'plugin-smoke.ps1'
$Assertions = New-Object System.Collections.Generic.List[string]
$Failures = New-Object System.Collections.Generic.List[string]
$EvidenceRoot = Split-Path -Parent $Evidence
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "hermes-office-e2e-$PID"
$ArtifactRoot = Join-Path $TempRoot 'artifacts'
$StateRoot = Join-Path $TempRoot 'state'
$FixtureRoot = Join-Path $TempRoot 'fixtures'
$CloneHome = Join-Path $TempRoot 'home-github-clone'
$PipVenv = Join-Path $TempRoot 'pip-entrypoint-venv'
$ManualProbe = Join-Path $EvidenceRoot 'task-15-manual-probe.json'
$PersistedArtifactRoot = Join-Path $EvidenceRoot 'task-15-workflow-artifacts'

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

function Join-ProcessArguments {
    param([string[]]$Arguments)
    $quoted = foreach ($argument in $Arguments) {
        if ($null -eq $argument) {
            '""'
        } elseif ($argument -match '[\s"]') {
            '"' + ($argument -replace '\\', '\\' -replace '"', '\"') + '"'
        } else {
            $argument
        }
    }
    return ($quoted -join ' ')
}

function Invoke-LoggedProcess {
    param(
        [string]$File,
        [string[]]$Arguments,
        [string]$Cwd,
        [int]$LimitSeconds = $TimeoutSeconds
    )
    $commandText = ($File + ' ' + (Join-ProcessArguments $Arguments)).Trim()
    Add-Evidence ''
    Add-Evidence "command: $commandText"
    Add-Evidence "cwd: $Cwd"
    Add-Evidence "timeout_seconds: $LimitSeconds"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $psi.Arguments = Join-ProcessArguments $Arguments
    $psi.WorkingDirectory = $Cwd
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    try {
        $process = [System.Diagnostics.Process]::Start($psi)
    } catch {
        Add-Evidence 'exit: 127'
        Add-Evidence 'stdout:'
        Add-Evidence '<empty>'
        Add-Evidence 'stderr:'
        Add-Evidence $_.Exception.Message
        return [pscustomobject]@{ ExitCode = 127; Stdout = ''; Stderr = $_.Exception.Message }
    }

    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $completed = $process.WaitForExit($LimitSeconds * 1000)
    if (-not $completed) {
        $process.Kill()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        Add-Evidence 'exit: 124'
        Add-ProcessOutput -Label 'stdout' -Text $stdout
        Add-ProcessOutput -Label 'stderr' -Text $stderr
        return [pscustomobject]@{ ExitCode = 124; Stdout = $stdout; Stderr = $stderr }
    }

    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    Add-Evidence "exit: $($process.ExitCode)"
    Add-ProcessOutput -Label 'stdout' -Text $stdout
    Add-ProcessOutput -Label 'stderr' -Text $stderr
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $stdout; Stderr = $stderr }
}

function Add-ProcessOutput {
    param(
        [string]$Label,
        [string]$Text
    )
    Add-Evidence "${Label}:"
    if ([string]::IsNullOrWhiteSpace($Text)) {
        Add-Evidence '<empty>'
        return
    }
    $Text.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ }
}

function Invoke-Hermes {
    param(
        [string]$HermesHomePath,
        [string[]]$Arguments,
        [int]$LimitSeconds = 180
    )
    Add-Evidence ''
    Add-Evidence (
        'command: & {0} -Evidence {1} -HermesHome {2} -TimeoutSeconds {3} -HermesArgs {4}' -f
        $Runner,
        $Evidence,
        $HermesHomePath,
        $LimitSeconds,
        (Join-ProcessArguments $Arguments)
    )
    Add-Evidence "cwd: $RepoRoot"
    Push-Location -LiteralPath $RepoRoot
    try {
        $output = & $Runner `
            -Evidence $Evidence `
            -HermesHome $HermesHomePath `
            -TimeoutSeconds $LimitSeconds `
            -HermesArgs $Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Stdout = ($output -join "`n")
        Stderr = ''
    }
}

function Get-JsonProbe {
    param([string]$Path)
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Test-HasProperty {
    param(
        [pscustomobject]$Value,
        [string]$Name
    )
    return $null -ne $Value.PSObject.Properties[$Name]
}

function Test-TextContains {
    param(
        [string]$Text,
        [string]$Pattern
    )
    return $Text -match $Pattern
}

function Add-InstallProofs {
    param(
        [pscustomobject]$Probe,
        [pscustomobject]$DirectCopy,
        [pscustomobject]$GitHubRemote,
        [pscustomobject]$PipEntryPoint,
        [pscustomobject]$Safety
    )
    $Probe | Add-Member -Force -NotePropertyName install_proofs -NotePropertyValue ([pscustomobject]@{
        direct_copy_install = $DirectCopy
        github_remote_install = $GitHubRemote
        pip_entry_point_discovery = $PipEntryPoint
    })
    $Probe | Add-Member -Force -NotePropertyName official_hermes_safety -NotePropertyValue $Safety
    $Probe | Add-Member -Force -NotePropertyName persisted_artifacts -NotePropertyValue (Copy-ProbeArtifacts -Probe $Probe)
    $Probe | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath $ManualProbe -Encoding UTF8
}

function Copy-ProbeArtifacts {
    param([pscustomobject]$Probe)
    Remove-Item -LiteralPath $PersistedArtifactRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $PersistedArtifactRoot | Out-Null
    $receipts = @()
    $persistedDraftPath = $null
    foreach ($property in $Probe.artifact_paths.PSObject.Properties) {
        $source = [string]$property.Value
        $target = Join-Path $PersistedArtifactRoot (Split-Path -Leaf $source)
        Copy-Item -LiteralPath $source -Destination $target -Force
        $property.Value = $target
        if ($property.Name -eq 'draft_document') {
            $persistedDraftPath = $target
        }
        $receipts += [pscustomobject]@{
            name = $property.Name
            source = $source
            evidence_path = $target
        }
    }
    if ($persistedDraftPath) {
        $Probe.template_update.draft_artifact = $persistedDraftPath
    }
    return $receipts
}

function Invoke-WorkflowProbe {
    param(
        [string]$ProbeScenario,
        [string]$ProbePath
    )
    $python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    return Invoke-LoggedProcess `
        -File $python `
        -Arguments @(
            '-m',
            'office_core_plugin.e2e_workflows',
            '--scenario',
            $ProbeScenario,
            '--artifact-root',
            $ArtifactRoot,
            '--state-root',
            $StateRoot,
            '--fixture-root',
            $FixtureRoot,
            '--artifact',
            $ProbePath
        ) `
        -Cwd $RepoRoot
}

function Invoke-DirectCopyProof {
    $directEvidence = Join-Path $EvidenceRoot 'task-15-direct-copy-install.txt'
    $result = Invoke-LoggedProcess `
        -File 'powershell' `
        -Arguments @(
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            $PluginSmoke,
            '-RepoRoot',
            $RepoRoot,
            '-Evidence',
            $directEvidence
        ) `
        -Cwd $RepoRoot `
        -LimitSeconds 420
    Add-Assertion -Name 'direct_copy_install_proof_exit_zero' -Passed ($result.ExitCode -eq 0) -Detail $directEvidence
    return [pscustomobject]@{
        success = $result.ExitCode -eq 0
        evidence = $directEvidence
        observable = 'plugin-smoke direct copy list/enable/debug in temp HERMES_HOME'
    }
}

function Invoke-GitHubRemoteProof {
    $attempts = @(
        [pscustomobject]@{
            name = 'owner_repo_default_branch'
            spec = $RemoteSpec
            home = Join-Path $TempRoot 'home-github-owner-repo'
        },
        [pscustomobject]@{
            name = 'owner_repo_subdir_default_branch'
            spec = $RemoteSubdirSpec
            home = Join-Path $TempRoot 'home-github-subdir'
        },
        [pscustomobject]@{
            name = 'github_tree_branch_subdir'
            spec = $RemoteBranchTreeSpec
            home = Join-Path $TempRoot 'home-github-tree'
        }
    )
    $receipts = @()
    foreach ($attempt in $attempts) {
        New-Item -ItemType Directory -Force -Path $attempt.home | Out-Null
        $install = Invoke-Hermes `
            -HermesHomePath $attempt.home `
            -Arguments @('plugins', 'install', $attempt.spec, '--enable') `
            -LimitSeconds 240
        $list = Invoke-Hermes `
            -HermesHomePath $attempt.home `
            -Arguments @('plugins', 'list', '--plain', '--no-bundled') `
            -LimitSeconds 120
        $remotePluginManifest = Join-Path (Join-Path (Join-Path $attempt.home 'plugins') $PluginName) 'plugin.yaml'
        $listedOfficeCore = Test-TextContains `
            -Text $list.Stdout `
            -Pattern "(?m)^\s*(enabled|not enabled)\s+\S+\s+\S+\s+$([regex]::Escape($PluginName))\s*$"
        $passed = $install.ExitCode -eq 0 -and $list.ExitCode -eq 0 -and $listedOfficeCore -and (
            Test-Path -LiteralPath $remotePluginManifest -PathType Leaf
        )
        Add-Evidence "github_remote_attempt: $($attempt.name)"
        Add-Evidence "github_remote_attempt_spec: $($attempt.spec)"
        Add-Evidence "github_remote_attempt_install_exit: $($install.ExitCode)"
        Add-Evidence "github_remote_attempt_list_exit: $($list.ExitCode)"
        Add-Evidence "github_remote_attempt_office_core_manifest_exists: $(Test-Path -LiteralPath $remotePluginManifest -PathType Leaf)"
        Add-Evidence "github_remote_attempt_list_mentions_office_core: $listedOfficeCore"
        $receipts += [pscustomobject]@{
            name = $attempt.name
            spec = $attempt.spec
            install_exit = $install.ExitCode
            list_exit = $list.ExitCode
            office_core_manifest_exists = Test-Path -LiteralPath $remotePluginManifest -PathType Leaf
            list_mentions_office_core = $listedOfficeCore
            success = $passed
        }
        if ($passed) {
            Add-Assertion -Name 'github_remote_direct_install_office_core' -Passed $true -Detail $attempt.spec
            Add-Assertion -Name 'github_remote_direct_list_mentions_office_core' -Passed $true -Detail $attempt.spec
            Add-Assertion -Name 'github_remote_direct_manifest_exists' -Passed $true -Detail $remotePluginManifest
            return [pscustomobject]@{
                success = $true
                blocked = $false
                method = "hermes plugins install $($attempt.spec) --enable"
                home = $attempt.home
                attempts = $receipts
            }
        }
    }

    Add-Assertion `
        -Name 'github_remote_direct_install_office_core' `
        -Passed $false `
        -Detail 'owner/repo and supported subdirectory/tree forms did not install/list office-core from the current GitHub default branch'

    $clonePath = Join-Path $TempRoot 'github-remote-clone'
    $branchCloneFallback = Invoke-GitHubBranchCloneFallback -ClonePath $clonePath
    return [pscustomobject]@{
        success = $false
        blocked = $true
        method = 'blocked: direct GitHub remote install cannot address codex/hermes-office-external-plugin branch with slash while default branch lacks office_core_plugin package'
        attempts = $receipts
        branch_clone_file_uri_fallback = $branchCloneFallback
    }
}

function Invoke-GitHubBranchCloneFallback {
    param([string]$ClonePath)
    $clone = Invoke-LoggedProcess `
        -File 'git' `
        -Arguments @(
            'clone',
            '--depth',
            '1',
            '--branch',
            'codex/hermes-office-external-plugin',
            'https://github.com/Tinycute00/hermes-office-core-plugin.git',
            $ClonePath
        ) `
        -Cwd $TempRoot `
        -LimitSeconds 240
    $cloneInstall = Invoke-Hermes `
        -HermesHomePath $CloneHome `
        -Arguments @(
            'plugins',
            'install',
            ([System.Uri](Resolve-Path -LiteralPath $ClonePath).ProviderPath).AbsoluteUri,
            '--enable'
        ) `
        -LimitSeconds 180
    $cloneList = Invoke-Hermes `
        -HermesHomePath $CloneHome `
        -Arguments @('plugins', 'list', '--plain', '--no-bundled') `
        -LimitSeconds 120
    $clonePluginManifest = Join-Path (Join-Path (Join-Path $CloneHome 'plugins') $PluginName) 'plugin.yaml'
    $clonePassed = $clone.ExitCode -eq 0 -and $cloneInstall.ExitCode -eq 0 -and $cloneList.ExitCode -eq 0 -and (
        Test-Path -LiteralPath $clonePluginManifest -PathType Leaf
    )
    Add-Assertion -Name 'github_branch_clone_file_uri_fallback_labeled' -Passed $clonePassed -Detail $ClonePath
    return [pscustomobject]@{
        success = $clonePassed
        method = 'branch clone fallback only: git clone --branch codex/hermes-office-external-plugin then hermes plugins install file URI'
        home = $CloneHome
        clone = $ClonePath
    }
}

function Invoke-PipEntryPointProof {
    $python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    $create = Invoke-LoggedProcess -File $python -Arguments @('-m', 'venv', $PipVenv) -Cwd $RepoRoot
    $pipPython = Join-Path $PipVenv 'Scripts\python.exe'
    $install = Invoke-LoggedProcess `
        -File $pipPython `
        -Arguments @('-m', 'pip', 'install', '--no-input', '--disable-pip-version-check', $RepoRoot) `
        -Cwd $RepoRoot `
        -LimitSeconds 240
    $code = @"
import importlib.metadata as metadata
entry_points = metadata.entry_points(group='hermes_agent.plugins')
matches = [entry for entry in entry_points if entry.name == 'office-core']
print(matches[0].value if matches else '<missing>')
print(callable(matches[0].load()) if matches else False)
"@
    $discover = Invoke-LoggedProcess -File $pipPython -Arguments @('-c', $code) -Cwd $RepoRoot
    $passed = $create.ExitCode -eq 0 -and $install.ExitCode -eq 0 -and $discover.Stdout -match 'office_core_plugin:register' -and $discover.Stdout -match 'True'
    Add-Assertion -Name 'pip_entry_point_discovery' -Passed $passed -Detail $PipVenv
    return [pscustomobject]@{
        success = $passed
        venv = $PipVenv
        observable = $discover.Stdout.Trim()
    }
}

function Get-OfficialSafetyProof {
    $diff = Invoke-LoggedProcess `
        -File 'git' `
        -Arguments @('-C', $HermesCheckout, 'diff', '--name-only') `
        -Cwd $RepoRoot
    $cached = Invoke-LoggedProcess `
        -File 'git' `
        -Arguments @('-C', $HermesCheckout, 'diff', '--cached', '--name-only') `
        -Cwd $RepoRoot
    $passed = $diff.ExitCode -eq 0 -and $cached.ExitCode -eq 0 -and [string]::IsNullOrWhiteSpace($diff.Stdout) -and [string]::IsNullOrWhiteSpace($cached.Stdout)
    Add-Assertion -Name 'official_hermes_diff_clean' -Passed $passed -Detail $HermesCheckout
    return [pscustomobject]@{
        diff_name_only = $diff.Stdout.Trim()
        cached_name_only = $cached.Stdout.Trim()
        clean = $passed
    }
}

function Assert-HappyProbe {
    param([pscustomobject]$Probe)
    Add-Assertion -Name 'probe_has_operation_records' -Passed ($Probe.operation_records.Count -ge 2)
    Add-Assertion -Name 'probe_has_template_artifact' -Passed (Test-HasProperty $Probe.artifact_paths 'template_registry')
    Add-Assertion -Name 'probe_has_data_artifact' -Passed (Test-HasProperty $Probe.artifact_paths 'data_dictionary')
    Add-Assertion -Name 'probe_has_source_artifact' -Passed (Test-HasProperty $Probe.messy_data_package 'source_selection')
    Add-Assertion -Name 'probe_has_bridge_handoff_plan' -Passed (Test-HasProperty $Probe 'bridge_handoff_plan')
    Add-Assertion -Name 'probe_has_denied_high_impact_operation' -Passed ($Probe.policy_denied_operation.audit[0].event_type -eq 'policy_denied')
    Add-Assertion -Name 'probe_has_local_candidate_search' -Passed ($Probe.local_candidate_file_search.candidates.Count -ge 3)
    Add-Assertion -Name 'probe_has_owner_confirmation_questions' -Passed ($Probe.owner_confirmation_workflow.owner_confirmation_questions.Count -ge 1)
    Add-Assertion -Name 'probe_no_real_runtime_mutation_flag' -Passed ($Probe.no_real_runtime_mutation -eq $true)
}

function Assert-AmbiguousProbe {
    param([pscustomobject]$Probe)
    $selection = $Probe.source_selection
    Add-Assertion -Name 'ambiguous_needs_owner_confirmation' -Passed ($selection.status -eq 'needs_owner_confirmation')
    Add-Assertion -Name 'ambiguous_does_not_auto_select' -Passed ($null -eq $selection.selected_record)
    Add-Assertion -Name 'ambiguous_has_owner_questions' -Passed ($selection.owner_confirmation_questions.Count -ge 1)
}

New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null
Set-Content -LiteralPath $Evidence -Encoding UTF8 -Value @(
    'e2e-office-workflows',
    ('started_at: ' + (Get-Date).ToString('o')),
    "repo_root_input: $RepoRoot",
    "scenario: $Scenario",
    "evidence: $Evidence",
    "temp_root: $TempRoot"
)

try {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $ArtifactRoot, $StateRoot, $FixtureRoot | Out-Null
    $resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath
    Add-Assertion -Name 'repo_root_exists' -Passed (Test-Path -LiteralPath $resolvedRepo -PathType Container) -Detail $resolvedRepo
    Add-Assertion -Name 'runner_exists' -Passed (Test-Path -LiteralPath $Runner -PathType Leaf) -Detail $Runner

    if ($Scenario -eq 'Ambiguous') {
        $failureProbe = Join-Path $EvidenceRoot 'task-15-ambiguous-probe.json'
        $run = Invoke-WorkflowProbe -ProbeScenario 'ambiguous' -ProbePath $failureProbe
        Add-Assertion -Name 'ambiguous_probe_exit_zero' -Passed ($run.ExitCode -eq 0)
        Assert-AmbiguousProbe -Probe (Get-JsonProbe -Path $failureProbe)
    } else {
        $probePath = Join-Path $TempRoot 'workflow-probe.json'
        $run = Invoke-WorkflowProbe -ProbeScenario 'happy' -ProbePath $probePath
        Add-Assertion -Name 'happy_probe_exit_zero' -Passed ($run.ExitCode -eq 0)
        $probe = Get-JsonProbe -Path $probePath
        Assert-HappyProbe -Probe $probe
        $directCopy = Invoke-DirectCopyProof
        $githubRemote = Invoke-GitHubRemoteProof
        $pipEntryPoint = Invoke-PipEntryPointProof
        $safety = Get-OfficialSafetyProof
        Add-InstallProofs `
            -Probe $probe `
            -DirectCopy $directCopy `
            -GitHubRemote $githubRemote `
            -PipEntryPoint $pipEntryPoint `
            -Safety $safety
        Add-Assertion -Name 'manual_probe_written' -Passed (Test-Path -LiteralPath $ManualProbe -PathType Leaf) -Detail $ManualProbe
        $persistedProbe = Get-JsonProbe -Path $ManualProbe
        $persistedPathsExist = $true
        foreach ($property in $persistedProbe.artifact_paths.PSObject.Properties) {
            if (-not (Test-Path -LiteralPath ([string]$property.Value) -PathType Leaf)) {
                $persistedPathsExist = $false
            }
        }
        $draftArtifactPath = [string]$persistedProbe.template_update.draft_artifact
        Add-Assertion -Name 'manual_probe_artifact_paths_persist' -Passed $persistedPathsExist -Detail $PersistedArtifactRoot
        Add-Assertion `
            -Name 'manual_probe_template_update_draft_artifact_persists' `
            -Passed (Test-Path -LiteralPath $draftArtifactPath -PathType Leaf) `
            -Detail $draftArtifactPath
        Add-Assertion -Name 'manual_probe_direct_copy_success' -Passed ($directCopy.success -eq $true)
        Add-Assertion -Name 'manual_probe_github_remote_success' -Passed ($githubRemote.success -eq $true)
        Add-Assertion -Name 'manual_probe_pip_entrypoint_success' -Passed ($pipEntryPoint.success -eq $true)
        Add-Assertion -Name 'manual_probe_safety_clean' -Passed ($safety.clean -eq $true)
    }
} finally {
    if ($KeepTemp) {
        Add-Evidence "cleanup: kept temp root $TempRoot"
        Add-Assertion -Name 'temp_cleanup_receipt' -Passed (Test-Path -LiteralPath $TempRoot) -Detail "kept:$TempRoot"
    } else {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
        Add-Evidence "cleanup: removed temp root $TempRoot"
        Add-Assertion -Name 'temp_cleanup_receipt' -Passed (-not (Test-Path -LiteralPath $TempRoot)) -Detail "removed:$TempRoot"
    }

    Add-Evidence ''
    Add-Evidence 'assertions_summary:'
    $Assertions | ForEach-Object { Add-Evidence $_ }
    Add-Evidence ''
    Add-Evidence 'adversarial_classes:'
    Add-Evidence 'malformed_input: ambiguous latest/main fixture asserts owner-confirmation questions and no selected record'
    Add-Evidence 'prompt_injection: fixture document contains instruction-like text but only metadata is scanned'
    Add-Evidence 'stale_state: temp HERMES_HOME, venv, clone, state, and fixtures are recreated per run'
    Add-Evidence 'dirty_worktree: official Hermes diff and cached diff are captured and asserted clean'
    Add-Evidence 'hung_or_long_commands: all subprocesses are bounded by timeout_seconds'
    Add-Evidence 'misleading_success_output: generated JSON artifacts are parsed and asserted directly'
    Add-Evidence 'overfit_slop: workflows cover template, data package, reuse, bridge, file search, denied send, install, and entry point proofs'
}

if ($Failures.Count -gt 0) {
    Add-Evidence ''
    Add-Evidence 'result: FAIL'
    exit 1
}

Add-Evidence ''
Add-Evidence 'result: PASS'
exit 0
