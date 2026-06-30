[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [string]$ExpectedRepo,
    [string]$ExpectedBranch,
    [string]$ExpectedUpstream,
    [string]$Evidence,
    [switch]$WriteBaseline,
    [string]$Baseline,
    [switch]$CompareBaseline,
    [switch]$ExpectFailure
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$HermesHome = 'C:\Users\88697\AppData\Local\hermes'
$ForbiddenRoots = @(
    (Join-Path $HermesHome 'hermes-agent'),
    (Join-Path $HermesHome 'plugins'),
    (Join-Path $HermesHome 'skills')
)
$RequiredDescription = 'Standalone Hermes Agent office workflow plugin'
$RequiredHomepage = 'https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins'
$RequiredTopics = @(
    'ai-agent',
    'hermes-agent',
    'hermes-plugin',
    'office-automation',
    'third-party-plugin'
)

$Assertions = New-Object System.Collections.Generic.List[string]
$Failures = New-Object System.Collections.Generic.List[string]

function Add-Evidence {
    param([string]$Line)
    Write-Host $Line
    if (-not [string]::IsNullOrWhiteSpace($Evidence)) {
        Add-Content -LiteralPath $Evidence -Value $Line -Encoding UTF8
    }
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
}

function Get-NormalizedPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ''
    }

    try {
        $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
    } catch {
        $resolved = [System.IO.Path]::GetFullPath($Path)
    }

    return $resolved.TrimEnd('\', '/')
}

function Test-IsSubPath {
    param(
        [string]$Child,
        [string]$Parent
    )
    $normalizedChild = (Get-NormalizedPath $Child)
    $normalizedParent = (Get-NormalizedPath $Parent)
    if ([string]::IsNullOrWhiteSpace($normalizedChild) -or [string]::IsNullOrWhiteSpace($normalizedParent)) {
        return $false
    }

    if ($normalizedChild.Equals($normalizedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    return $normalizedChild.StartsWith($normalizedParent + '\', [System.StringComparison]::OrdinalIgnoreCase)
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
        [int]$TimeoutSeconds = 30
    )

    $commandText = ($File + ' ' + (Join-ProcessArguments $Arguments)).Trim()
    Add-Evidence ''
    Add-Evidence "command: $commandText"
    Add-Evidence "cwd: $Cwd"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $psi.Arguments = Join-ProcessArguments $Arguments
    $psi.WorkingDirectory = $Cwd
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi

    $started = $process.Start()
    if (-not $started) {
        Add-Evidence 'exit: 125'
        Add-Evidence 'stderr: failed to start process'
        return [pscustomobject]@{
            ExitCode = 125
            Stdout = ''
            Stderr = 'failed to start process'
            TimedOut = $false
        }
    }

    $completed = $process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $completed) {
        try {
            $process.Kill()
        } catch {
        }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        Add-Evidence 'exit: 124'
        Add-Evidence "timeout_seconds: $TimeoutSeconds"
        Add-Evidence 'stdout:'
        if ([string]::IsNullOrWhiteSpace($stdout)) { Add-Evidence '<empty>' } else { $stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ } }
        Add-Evidence 'stderr:'
        if ([string]::IsNullOrWhiteSpace($stderr)) { Add-Evidence '<empty>' } else { $stderr.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ } }
        return [pscustomobject]@{
            ExitCode = 124
            Stdout = $stdout
            Stderr = $stderr
            TimedOut = $true
        }
    }

    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $exitCode = $process.ExitCode

    Add-Evidence "exit: $exitCode"
    Add-Evidence 'stdout:'
    if ([string]::IsNullOrWhiteSpace($stdout)) {
        Add-Evidence '<empty>'
    } else {
        $stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ }
    }
    Add-Evidence 'stderr:'
    if ([string]::IsNullOrWhiteSpace($stderr)) {
        Add-Evidence '<empty>'
    } else {
        $stderr.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ }
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Stdout = $stdout
        Stderr = $stderr
        TimedOut = $false
    }
}

function Get-BoundarySnapshot {
    $official = Join-Path $HermesHome 'hermes-agent'
    $plugins = Join-Path $HermesHome 'plugins'
    $skills = Join-Path $HermesHome 'skills'

    $status = Invoke-LoggedProcess -File 'git' -Arguments @('-C', $official, 'status', '--short', '--untracked-files=all') -Cwd $HermesHome
    $pluginList = Get-ChildItem -LiteralPath $plugins -Force -ErrorAction Stop | Sort-Object Name | ForEach-Object { '{0} {1}' -f $_.Mode, $_.Name }
    $skillList = Get-ChildItem -LiteralPath $skills -Force -ErrorAction Stop | Sort-Object Name | ForEach-Object { '{0} {1}' -f $_.Mode, $_.Name }

    return [pscustomobject]@{
        StatusExit = $status.ExitCode
        StatusLines = if ([string]::IsNullOrWhiteSpace($status.Stdout)) { @('<empty>') } else { @($status.Stdout.TrimEnd() -split "`r?`n") }
        PluginLines = @($pluginList)
        SkillLines = @($skillList)
    }
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

function Test-EqualArray {
    param(
        [string[]]$Left,
        [string[]]$Right
    )
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

Add-Evidence 'validate_repo_boundary'
Add-Evidence ('started_at: ' + (Get-Date).ToString('o'))
Add-Evidence "repo_root_input: $RepoRoot"
Add-Evidence "expect_failure: $ExpectFailure"

$normalizedRepoRoot = Get-NormalizedPath $RepoRoot
$pathRejected = $false
$rejectedBy = ''
foreach ($forbidden in $ForbiddenRoots) {
    if (Test-IsSubPath -Child $normalizedRepoRoot -Parent $forbidden) {
        $pathRejected = $true
        $rejectedBy = $forbidden
        break
    }
}

if ($ExpectFailure) {
    Add-Assertion -Name 'forbidden_repo_root_detected' -Passed $pathRejected -Detail $rejectedBy
    Add-Assertion -Name 'expect_failure_rejected_bad_path' -Passed $pathRejected -Detail 'ExpectFailure succeeds only for forbidden source paths'
    Add-Evidence ''
    Add-Evidence 'assertions:'
    $Assertions | ForEach-Object { Add-Evidence $_ }
    Add-Evidence ''
    Add-Evidence 'adversarial_classes:'
    Add-Evidence 'dirty_worktree: not evaluated for expected path rejection'
    Add-Evidence 'stale_state: live path normalization used'
    Add-Evidence 'malformed_input: forbidden path provided intentionally'
    Add-Evidence 'misleading_success_output: exit is based on explicit path assertion'
    Add-Evidence 'hung_or_long_commands: no external long-running command required'
    Add-Evidence 'untrusted_external_text: not applicable to expected path rejection'
    Add-Evidence 'not_applicable: cancel_resume, flaky_tests, repeated_interruptions'
    if ($pathRejected) {
        Add-Evidence ''
        Add-Evidence 'result: PASS'
        exit 0
    }
    Add-Evidence ''
    Add-Evidence 'result: FAIL'
    exit 1
}

Add-Assertion -Name 'repo_root_not_under_hermes_owned_paths' -Passed (-not $pathRejected) -Detail $(if ($pathRejected) { "RepoRoot is under $rejectedBy" } else { $normalizedRepoRoot })

Add-Assertion -Name 'malformed_input_required_expected_repo' -Passed (-not [string]::IsNullOrWhiteSpace($ExpectedRepo))
Add-Assertion -Name 'malformed_input_required_expected_branch' -Passed (-not [string]::IsNullOrWhiteSpace($ExpectedBranch))
Add-Assertion -Name 'malformed_input_required_expected_upstream' -Passed (-not [string]::IsNullOrWhiteSpace($ExpectedUpstream))

if ($Failures.Count -eq 0) {
    $repoExists = Test-Path -LiteralPath $normalizedRepoRoot -PathType Container
    Add-Assertion -Name 'repo_root_exists' -Passed $repoExists -Detail $normalizedRepoRoot
}

if ($Failures.Count -eq 0 -and $WriteBaseline) {
    Add-Evidence ''
    Add-Evidence 'boundary_snapshot_for_baseline:'
    $snapshot = Get-BoundarySnapshot
    Add-Assertion -Name 'write_baseline_status_command_succeeded' -Passed ($snapshot.StatusExit -eq 0)
    Add-Evidence 'boundary_hermes_agent_status:'
    $snapshot.StatusLines | ForEach-Object { Add-Evidence $_ }
    Add-Evidence 'boundary_runtime_plugins_top_level:'
    $snapshot.PluginLines | ForEach-Object { Add-Evidence $_ }
    Add-Evidence 'boundary_runtime_skills_top_level:'
    $snapshot.SkillLines | ForEach-Object { Add-Evidence $_ }
    if (-not [string]::IsNullOrWhiteSpace($Baseline)) {
        $baselineLines = @(
            'source-boundary-baseline',
            ('captured_at: ' + (Get-Date).ToString('o')),
            'note: Existing untracked local AGENTS.md entries are baseline facts, not automatic failures.',
            '',
            'command: git -C C:\Users\88697\AppData\Local\hermes\hermes-agent status --short --untracked-files=all',
            ('exit: ' + $snapshot.StatusExit),
            'stdout:'
        ) + $snapshot.StatusLines + @(
            '',
            'command: Get-ChildItem -LiteralPath C:\Users\88697\AppData\Local\hermes\plugins -Force | Select Name,Mode',
            'exit: 0',
            'stdout:'
        ) + $snapshot.PluginLines + @(
            '',
            'command: Get-ChildItem -LiteralPath C:\Users\88697\AppData\Local\hermes\skills -Force | Select Name,Mode',
            'exit: 0',
            'stdout:'
        ) + $snapshot.SkillLines
        Set-Content -LiteralPath $Baseline -Value $baselineLines -Encoding UTF8
        Add-Assertion -Name 'baseline_file_written' -Passed (Test-Path -LiteralPath $Baseline) -Detail $Baseline
    }
}

if ($Failures.Count -eq 0 -and $CompareBaseline) {
    Add-Assertion -Name 'compare_baseline_file_supplied' -Passed (-not [string]::IsNullOrWhiteSpace($Baseline))
    Add-Assertion -Name 'compare_baseline_file_exists' -Passed (Test-Path -LiteralPath $Baseline)
    if ($Failures.Count -eq 0) {
        $baselineLinesRaw = @(Get-Content -LiteralPath $Baseline)
        $current = Get-BoundarySnapshot
        $baselineStatus = Get-BaselineStdoutSection -Lines $baselineLinesRaw -Command 'git -C C:\Users\88697\AppData\Local\hermes\hermes-agent status --short --untracked-files=all'
        $baselinePlugins = Get-BaselineStdoutSection -Lines $baselineLinesRaw -Command 'Get-ChildItem -LiteralPath C:\Users\88697\AppData\Local\hermes\plugins -Force | Select Name,Mode'
        $baselineSkills = Get-BaselineStdoutSection -Lines $baselineLinesRaw -Command 'Get-ChildItem -LiteralPath C:\Users\88697\AppData\Local\hermes\skills -Force | Select Name,Mode'
        Add-Assertion -Name 'compare_baseline_hermes_agent_status_unchanged' -Passed (Test-EqualArray -Left $baselineStatus -Right $current.StatusLines)
        Add-Assertion -Name 'compare_baseline_runtime_plugins_unchanged' -Passed (Test-EqualArray -Left $baselinePlugins -Right $current.PluginLines)
        Add-Assertion -Name 'compare_baseline_runtime_skills_unchanged' -Passed (Test-EqualArray -Left $baselineSkills -Right $current.SkillLines)
    }
}

if ($Failures.Count -eq 0) {
    $gitStatus = Invoke-LoggedProcess -File 'git' -Arguments @('-C', $normalizedRepoRoot, 'status', '--short') -Cwd $normalizedRepoRoot
    Add-Assertion -Name 'git_status_command_succeeded' -Passed ($gitStatus.ExitCode -eq 0)
    Add-Assertion -Name 'dirty_worktree_clean' -Passed ([string]::IsNullOrWhiteSpace($gitStatus.Stdout)) -Detail 'git status --short must be empty after commit'
}

if ($Failures.Count -eq 0) {
    $branch = Invoke-LoggedProcess -File 'git' -Arguments @('-C', $normalizedRepoRoot, 'branch', '--show-current') -Cwd $normalizedRepoRoot
    $currentBranch = $branch.Stdout.Trim()
    Add-Assertion -Name 'expected_branch' -Passed (($branch.ExitCode -eq 0) -and ($currentBranch -eq $ExpectedBranch)) -Detail $currentBranch
}

if ($Failures.Count -eq 0) {
    $remote = Invoke-LoggedProcess -File 'git' -Arguments @('-C', $normalizedRepoRoot, 'remote', '-v') -Cwd $normalizedRepoRoot
    $remoteText = $remote.Stdout
    $repoSlug = $ExpectedRepo.TrimEnd('.git')
    $remoteMatches = ($remote.ExitCode -eq 0) -and ($remoteText -match [regex]::Escape($repoSlug))
    Add-Assertion -Name 'origin_remote_matches_expected_repo' -Passed $remoteMatches -Detail $repoSlug
}

if ($Failures.Count -eq 0) {
    $upstream = Invoke-LoggedProcess -File 'git' -Arguments @('-C', $normalizedRepoRoot, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}') -Cwd $normalizedRepoRoot
    $currentUpstream = $upstream.Stdout.Trim()
    Add-Assertion -Name 'expected_upstream' -Passed (($upstream.ExitCode -eq 0) -and ($currentUpstream -eq $ExpectedUpstream)) -Detail $currentUpstream
}

if ($Failures.Count -eq 0) {
    $repoView = Invoke-LoggedProcess -File 'gh' -Arguments @('repo', 'view', $ExpectedRepo, '--json', 'name,owner,visibility,url,description,homepageUrl,repositoryTopics,hasIssuesEnabled,hasWikiEnabled') -Cwd $normalizedRepoRoot
    Add-Assertion -Name 'gh_repo_view_succeeded' -Passed ($repoView.ExitCode -eq 0)
    if ($repoView.ExitCode -eq 0) {
        try {
            $metadata = $repoView.Stdout | ConvertFrom-Json
            $owner = if ($metadata.owner.login) { $metadata.owner.login } else { $metadata.owner.name }
            $expectedOwner, $expectedName = $ExpectedRepo -split '/', 2
            $topics = @($metadata.repositoryTopics | ForEach-Object {
                if ($_.name) { $_.name } else { [string]$_ }
            } | Sort-Object)
            $requiredTopicsSorted = @($RequiredTopics | Sort-Object)
            Add-Assertion -Name 'gh_owner_matches_expected' -Passed ($owner -eq $expectedOwner) -Detail $owner
            Add-Assertion -Name 'gh_name_matches_expected' -Passed ($metadata.name -eq $expectedName) -Detail $metadata.name
            Add-Assertion -Name 'gh_visibility_private' -Passed ($metadata.visibility -eq 'PRIVATE') -Detail $metadata.visibility
            Add-Assertion -Name 'gh_description_matches' -Passed ($metadata.description -eq $RequiredDescription) -Detail $metadata.description
            Add-Assertion -Name 'gh_homepage_matches' -Passed ($metadata.homepageUrl -eq $RequiredHomepage) -Detail $metadata.homepageUrl
            Add-Assertion -Name 'gh_issues_disabled' -Passed (-not [bool]$metadata.hasIssuesEnabled)
            Add-Assertion -Name 'gh_wiki_disabled' -Passed (-not [bool]$metadata.hasWikiEnabled)
            Add-Assertion -Name 'gh_topics_exact_match' -Passed (Test-EqualArray -Left $topics -Right $requiredTopicsSorted) -Detail (($topics -join ','))
        } catch {
            Add-Assertion -Name 'gh_metadata_json_parse' -Passed $false -Detail $_.Exception.Message
        }
    }
}

Add-Evidence ''
Add-Evidence 'assertions:'
$Assertions | ForEach-Object { Add-Evidence $_ }

Add-Evidence ''
Add-Evidence 'adversarial_classes:'
Add-Evidence 'dirty_worktree: checked with git status --short and asserted empty'
Add-Evidence 'stale_state: branch, upstream, remote, and GitHub metadata read live during validation'
Add-Evidence 'malformed_input: required parameters and forbidden source paths are explicit assertions'
Add-Evidence 'misleading_success_output: GitHub output is parsed as JSON and exact fields are asserted'
Add-Evidence 'hung_or_long_commands: external commands are run through a bounded process timeout'
Add-Evidence 'untrusted_external_text: repo metadata is treated as data, not trusted prose'
Add-Evidence 'not_applicable: cancel_resume, flaky_tests, repeated_interruptions'

if ($Failures.Count -gt 0) {
    Add-Evidence ''
    Add-Evidence 'result: FAIL'
    exit 1
}

Add-Evidence ''
Add-Evidence 'result: PASS'
exit 0
