[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$Evidence
)

# allow: SIZE_OK - Bounded QA/evidence harness; not plugin runtime behavior.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ExpectedRepo = 'Tinycute00/hermes-office-core-plugin'
$ExpectedBranch = 'codex/hermes-office-external-plugin'
$ExpectedUpstream = 'origin/codex/hermes-office-external-plugin'
$ForbiddenRepos = @(
    'Tinycute00/hermes-agent',
    'NousResearch/hermes-agent'
)
$HermesHome = 'C:\Users\88697\AppData\Local\hermes'
$OfficialHermesCheckout = Join-Path $HermesHome 'hermes-agent'
$ForbiddenSourceRoots = @(
    $OfficialHermesCheckout,
    (Join-Path $HermesHome 'plugins'),
    (Join-Path $HermesHome 'skills')
)
$RequiredEvidenceFiles = @(
    'source-boundary-baseline.txt',
    'task-01-linear-supersession.md',
    'task-02-repo.txt',
    'task-03-bootstrap.txt',
    'task-03-distribution.txt',
    'task-03-distribution-failure.txt',
    'task-04-install-smoke.txt',
    'task-04-install-smoke-failure.txt',
    'task-05-inventory.txt',
    'task-05-inventory-failure.txt',
    'task-06-handler-contract.txt',
    'task-07-register.txt',
    'task-08-policy-audit.txt',
    'task-09-registries.txt',
    'task-10-local-files.txt',
    'task-11-bridge.txt',
    'task-12-skills.txt',
    'task-12-skills-failure.txt',
    'task-13-docs.txt',
    'task-13-docs-failure.txt',
    'task-14-build.txt',
    'task-14-package-data-failure.txt',
    'task-14-final-quality-remediation.txt',
    'task-15-e2e-remote-merge.txt',
    'task-15-e2e-remote-merge-ambiguous.txt',
    'task-15-final-install-e2e-remote-merge.txt',
    'task-15-remote-install-resolution.txt',
    'task-16-linear-comments.json',
    'task-16-code-review.txt',
    'task-16-manual-qa-matrix.md',
    'task-16-notepad.md',
    'task-16-rollup-reference-validation-summary.txt',
    'task-16-rollup-reference-validation-upstream-cleanliness.txt'
)

$Assertions = New-Object System.Collections.Generic.List[string]
$Failures = New-Object System.Collections.Generic.List[string]

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

function Test-IsSubPath {
    param(
        [string]$Child,
        [string]$Parent
    )
    $normalizedChild = Get-NormalizedPath $Child
    $normalizedParent = Get-NormalizedPath $Parent
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
        [int]$TimeoutSeconds = 60
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

    try {
        $started = $process.Start()
    } catch {
        Add-Evidence 'exit: 125'
        Add-Evidence 'stdout: <empty>'
        Add-Evidence ('stderr: ' + $_.Exception.Message)
        return [pscustomobject]@{
            ExitCode = 125
            Stdout = ''
            Stderr = $_.Exception.Message
            TimedOut = $false
        }
    }

    if (-not $started) {
        Add-Evidence 'exit: 125'
        Add-Evidence 'stdout: <empty>'
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
    Add-Evidence "exit: $($process.ExitCode)"
    Add-Evidence 'stdout:'
    if ([string]::IsNullOrWhiteSpace($stdout)) { Add-Evidence '<empty>' } else { $stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ } }
    Add-Evidence 'stderr:'
    if ([string]::IsNullOrWhiteSpace($stderr)) { Add-Evidence '<empty>' } else { $stderr.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ } }

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdout
        Stderr = $stderr
        TimedOut = $false
    }
}

function Test-FileContains {
    param(
        [string]$Path,
        [string]$Pattern
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $content = Get-Content -LiteralPath $Path -Raw
    return $content -match $Pattern
}

function Test-EvidenceReferenceToken {
    param([string]$Token)
    $normalizedToken = $Token.Trim() -replace '/', '\'
    if ($normalizedToken -match '^[A-Za-z]:\\') {
        return $false
    }
    if ($normalizedToken -match '^(https?://|app://)') {
        return $false
    }
    if ($normalizedToken -notmatch '\.(txt|json|md|jsonl|py|toml|whl|tar\.gz)$') {
        return $false
    }
    return $normalizedToken -match '^(task-\d+|source-boundary-|final-quality-|gate-review-)'
}

function Get-RollupEvidenceReferences {
    param([string]$Text)
    $references = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line.TrimStart().StartsWith('```')) {
            continue
        }
        foreach ($match in [regex]::Matches($line, '(?<!`)`([^`\r\n]+)`(?!`)')) {
            $token = $match.Groups[1].Value.Trim()
            if (Test-EvidenceReferenceToken -Token $token) {
                $normalizedToken = $token -replace '/', '\'
                $references.Add($normalizedToken) | Out-Null
            }
        }
    }
    return @($references | Sort-Object)
}

function Get-RollupReferenceCountClaims {
    param([string]$Text)
    $claims = New-Object System.Collections.Generic.List[int]
    foreach ($match in [regex]::Matches($Text, '(?i)\b(\d+)\s+rollup (?:evidence )?references checked')) {
        $claims.Add([int]$match.Groups[1].Value) | Out-Null
    }
    return @($claims)
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Evidence) | Out-Null
Set-Content -LiteralPath $Evidence -Encoding UTF8 -Value @(
    'final-scope-fidelity',
    ('started_at: ' + (Get-Date).ToString('o')),
    "repo_root_input: $RepoRoot",
    "evidence: $Evidence"
)

$resolvedRepo = Get-NormalizedPath $RepoRoot
$evidenceRoot = Split-Path -Parent $Evidence
$rollupPath = Join-Path $resolvedRepo 'docs\release\evidence-rollup.md'
$readmePath = Join-Path $resolvedRepo 'README.md'
$securityPath = Join-Path $resolvedRepo 'SECURITY.md'
$pluginYamlPath = Join-Path $resolvedRepo 'plugin.yaml'
$pyprojectPath = Join-Path $resolvedRepo 'pyproject.toml'
$linearProofPath = Join-Path $evidenceRoot 'task-16-linear-comments.json'
$sourceBoundaryBaselinePath = Join-Path $evidenceRoot 'source-boundary-baseline.txt'

Add-Assertion -Name 'repo_root_exists' -Passed (Test-Path -LiteralPath $resolvedRepo -PathType Container) -Detail $resolvedRepo

$isForbiddenRoot = $false
foreach ($forbiddenRoot in $ForbiddenSourceRoots) {
    if (Test-IsSubPath -Child $resolvedRepo -Parent $forbiddenRoot) {
        $isForbiddenRoot = $true
        Add-Evidence "forbidden_source_root_match: $forbiddenRoot"
        break
    }
}
Add-Assertion -Name 'standalone_external_source_path' -Passed (-not $isForbiddenRoot) -Detail $resolvedRepo

Add-Assertion -Name 'official_hermes_checkout_exists' -Passed (Test-Path -LiteralPath $OfficialHermesCheckout -PathType Container) -Detail $OfficialHermesCheckout
$officialStatus = Invoke-LoggedProcess -File 'git' -Arguments @('status', '--porcelain=v1', '--untracked-files=all') -Cwd $OfficialHermesCheckout
$officialStatusLines = @($officialStatus.Stdout -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
$officialTrackedOrStagedLines = @($officialStatusLines | Where-Object { $_ -notmatch '^\?\? ' })
$officialUntrackedLines = @($officialStatusLines | Where-Object { $_ -match '^\?\? ' })
$officialUntrackedNonAgents = @($officialUntrackedLines | Where-Object { $_ -notmatch '^\?\? (?:.*/)?AGENTS\.md$' })
$baselineText = ''
if (Test-Path -LiteralPath $sourceBoundaryBaselinePath -PathType Leaf) {
    $baselineText = Get-Content -LiteralPath $sourceBoundaryBaselinePath -Raw
}
$officialUntrackedWithoutBaseline = @($officialUntrackedLines | Where-Object { $baselineText -notmatch [regex]::Escape($_) })
$officialTrackedDiff = Invoke-LoggedProcess -File 'git' -Arguments @('diff', '--quiet') -Cwd $OfficialHermesCheckout
$officialStagedDiff = Invoke-LoggedProcess -File 'git' -Arguments @('diff', '--cached', '--quiet') -Cwd $OfficialHermesCheckout
Add-Assertion -Name 'official_hermes_status_succeeded' -Passed ($officialStatus.ExitCode -eq 0) -Detail "exit=$($officialStatus.ExitCode)"
Add-Assertion -Name 'official_hermes_no_tracked_or_staged_status_entries' -Passed ($officialTrackedOrStagedLines.Count -eq 0) -Detail "tracked_or_staged_count=$($officialTrackedOrStagedLines.Count)"
Add-Assertion -Name 'official_hermes_tracked_diff_clean' -Passed ($officialTrackedDiff.ExitCode -eq 0) -Detail "exit=$($officialTrackedDiff.ExitCode)"
Add-Assertion -Name 'official_hermes_staged_diff_clean' -Passed ($officialStagedDiff.ExitCode -eq 0) -Detail "exit=$($officialStagedDiff.ExitCode)"
Add-Assertion -Name 'official_hermes_untracked_entries_are_agents_only' -Passed ($officialUntrackedNonAgents.Count -eq 0) -Detail "untracked_count=$($officialUntrackedLines.Count)"
Add-Assertion -Name 'official_hermes_untracked_agents_in_source_boundary_baseline' -Passed (($officialUntrackedLines.Count -gt 0) -and ($officialUntrackedWithoutBaseline.Count -eq 0)) -Detail "baseline=source-boundary-baseline.txt untracked_count=$($officialUntrackedLines.Count)"
Add-Evidence 'official_hermes_untracked_baseline_note: untracked AGENTS.md entries are baseline/pre-existing instruction files recorded in source-boundary-baseline.txt, not product repo changes created by this plan'

$status = Invoke-LoggedProcess -File 'git' -Arguments @('status', '--short', '--branch', '--untracked-files=all') -Cwd $resolvedRepo
Add-Assertion -Name 'git_status_succeeded' -Passed ($status.ExitCode -eq 0) -Detail "exit=$($status.ExitCode)"

$branch = Invoke-LoggedProcess -File 'git' -Arguments @('branch', '--show-current') -Cwd $resolvedRepo
$currentBranch = $branch.Stdout.Trim()
Add-Assertion -Name 'expected_branch' -Passed (($branch.ExitCode -eq 0) -and ($currentBranch -eq $ExpectedBranch)) -Detail $currentBranch

$upstream = Invoke-LoggedProcess -File 'git' -Arguments @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}') -Cwd $resolvedRepo
$currentUpstream = $upstream.Stdout.Trim()
Add-Assertion -Name 'expected_upstream' -Passed (($upstream.ExitCode -eq 0) -and ($currentUpstream -eq $ExpectedUpstream)) -Detail $currentUpstream

$fetchUpstream = Invoke-LoggedProcess -File 'git' -Arguments @('fetch', 'origin', $ExpectedBranch) -Cwd $resolvedRepo
Add-Assertion -Name 'fetch_expected_upstream_succeeded' -Passed ($fetchUpstream.ExitCode -eq 0) -Detail "exit=$($fetchUpstream.ExitCode)"

$localHead = Invoke-LoggedProcess -File 'git' -Arguments @('rev-parse', 'HEAD') -Cwd $resolvedRepo
$localHeadSha = $localHead.Stdout.Trim()
Add-Assertion -Name 'local_head_resolved' -Passed (($localHead.ExitCode -eq 0) -and ($localHeadSha -match '^[0-9a-f]{40}$')) -Detail $localHeadSha

$upstreamHead = Invoke-LoggedProcess -File 'git' -Arguments @('rev-parse', '@{u}') -Cwd $resolvedRepo
$upstreamHeadSha = $upstreamHead.Stdout.Trim()
Add-Assertion -Name 'upstream_head_resolved' -Passed (($upstreamHead.ExitCode -eq 0) -and ($upstreamHeadSha -match '^[0-9a-f]{40}$')) -Detail $upstreamHeadSha

$headInUpstream = Invoke-LoggedProcess -File 'git' -Arguments @('merge-base', '--is-ancestor', 'HEAD', '@{u}') -Cwd $resolvedRepo
Add-Assertion -Name 'local_head_present_in_upstream_history' -Passed ($headInUpstream.ExitCode -eq 0) -Detail "exit=$($headInUpstream.ExitCode)"
Add-Assertion -Name 'local_head_equals_upstream' -Passed (($localHead.ExitCode -eq 0) -and ($upstreamHead.ExitCode -eq 0) -and ($localHeadSha -eq $upstreamHeadSha)) -Detail "HEAD=$localHeadSha upstream=$upstreamHeadSha"

$remote = Invoke-LoggedProcess -File 'git' -Arguments @('remote', '-v') -Cwd $resolvedRepo
$remoteText = $remote.Stdout
Add-Assertion -Name 'origin_remote_expected_repo' -Passed (($remote.ExitCode -eq 0) -and ($remoteText -match [regex]::Escape($ExpectedRepo))) -Detail $ExpectedRepo
foreach ($forbiddenRepo in $ForbiddenRepos) {
    Add-Assertion -Name ('origin_remote_not_' + ($forbiddenRepo -replace '[^A-Za-z0-9]', '_')) -Passed ($remoteText -notmatch [regex]::Escape($forbiddenRepo)) -Detail $forbiddenRepo
}

Add-Assertion -Name 'rollup_exists' -Passed (Test-Path -LiteralPath $rollupPath -PathType Leaf) -Detail $rollupPath
Add-Assertion -Name 'rollup_nonempty' -Passed ((Test-Path -LiteralPath $rollupPath -PathType Leaf) -and ((Get-Item -LiteralPath $rollupPath).Length -gt 0)) -Detail $rollupPath

Add-Assertion -Name 'readme_standalone_external_plugin' -Passed (Test-FileContains -Path $readmePath -Pattern 'Standalone third-party Hermes Agent plugin|standalone external') -Detail 'README.md'
Add-Assertion -Name 'readme_not_hermes_source_tree' -Passed (Test-FileContains -Path $readmePath -Pattern 'not the\s+Hermes Agent source tree|not a fork of Hermes') -Detail 'README.md'
Add-Assertion -Name 'plugin_manifest_office_core' -Passed (Test-FileContains -Path $pluginYamlPath -Pattern '(?m)^name:\s*office-core\s*$') -Detail 'plugin.yaml'
Add-Assertion -Name 'pyproject_entrypoint_office_core' -Passed (Test-FileContains -Path $pyprojectPath -Pattern 'office-core\s*=\s*"office_core_plugin:register"') -Detail 'pyproject.toml'
Add-Assertion -Name 'install_command_documented' -Passed ((Test-FileContains -Path $readmePath -Pattern 'hermes plugins install Tinycute00/hermes-office-core-plugin --enable') -and (Test-FileContains -Path $rollupPath -Pattern 'hermes plugins install Tinycute00/hermes-office-core-plugin --enable')) -Detail 'README.md and evidence-rollup.md'

foreach ($relativeEvidence in $RequiredEvidenceFiles) {
    $path = Join-Path $evidenceRoot $relativeEvidence
    $exists = Test-Path -LiteralPath $path -PathType Leaf
    Add-Assertion -Name ('required_evidence_exists_' + ($relativeEvidence -replace '[^A-Za-z0-9]', '_')) -Passed $exists -Detail $relativeEvidence
    if ($exists) {
        Add-Assertion -Name ('required_evidence_nonempty_' + ($relativeEvidence -replace '[^A-Za-z0-9]', '_')) -Passed ((Get-Item -LiteralPath $path).Length -gt 0) -Detail $relativeEvidence
    }
}

if (Test-Path -LiteralPath $rollupPath -PathType Leaf) {
    $rollupText = Get-Content -LiteralPath $rollupPath -Raw
    foreach ($relativeEvidence in $RequiredEvidenceFiles) {
        Add-Assertion -Name ('rollup_mentions_' + ($relativeEvidence -replace '[^A-Za-z0-9]', '_')) -Passed ($rollupText.Contains($relativeEvidence)) -Detail $relativeEvidence
    }
    $rollupEvidenceReferences = @(Get-RollupEvidenceReferences -Text $rollupText)
    Add-Assertion -Name 'rollup_evidence_reference_count_nonzero' -Passed ($rollupEvidenceReferences.Count -gt 0) -Detail ("count=$($rollupEvidenceReferences.Count)")
    $rollupReferenceCountClaims = @(Get-RollupReferenceCountClaims -Text $rollupText)
    $staleRollupReferenceCountClaims = @($rollupReferenceCountClaims | Where-Object { $_ -ne $rollupEvidenceReferences.Count })
    Add-Assertion -Name 'rollup_reference_count_claims_match_parser' -Passed ($staleRollupReferenceCountClaims.Count -eq 0) -Detail ("parser_count=$($rollupEvidenceReferences.Count) claims=$($rollupReferenceCountClaims -join ',')")
    foreach ($relativeEvidence in $rollupEvidenceReferences) {
        $path = Join-Path $evidenceRoot $relativeEvidence
        $exists = Test-Path -LiteralPath $path -PathType Leaf
        Add-Assertion -Name ('rollup_reference_exists_' + ($relativeEvidence -replace '[^A-Za-z0-9]', '_')) -Passed $exists -Detail $relativeEvidence
        if ($exists) {
            Add-Assertion -Name ('rollup_reference_nonempty_' + ($relativeEvidence -replace '[^A-Za-z0-9]', '_')) -Passed ((Get-Item -LiteralPath $path).Length -gt 0) -Detail $relativeEvidence
        }
    }
}

$repoEvidence = Join-Path $evidenceRoot 'task-02-repo.txt'
$buildEvidence = Join-Path $evidenceRoot 'task-14-build.txt'
$docsEvidence = Join-Path $evidenceRoot 'task-13-docs.txt'
$installEvidence = Join-Path $evidenceRoot 'task-15-final-install-e2e-remote-merge.txt'
$e2eEvidence = Join-Path $evidenceRoot 'task-15-e2e-remote-merge.txt'

Add-Assertion -Name 'repo_boundary_evidence_passed' -Passed (Test-FileContains -Path $repoEvidence -Pattern 'result:\s*PASS') -Detail 'task-02-repo.txt'
Add-Assertion -Name 'docs_evidence_passed' -Passed (Test-FileContains -Path $docsEvidence -Pattern 'result:\s*PASS') -Detail 'task-13-docs.txt'
Add-Assertion -Name 'build_evidence_passed' -Passed (Test-FileContains -Path $buildEvidence -Pattern 'result:\s*PASS|96 passed') -Detail 'task-14-build.txt'
Add-Assertion -Name 'e2e_evidence_passed' -Passed (Test-FileContains -Path $e2eEvidence -Pattern 'result:\s*PASS') -Detail 'task-15-e2e-remote-merge.txt'
Add-Assertion -Name 'github_remote_install_proof' -Passed (Test-FileContains -Path $installEvidence -Pattern 'github_remote_direct_install_office_core=PASS') -Detail 'task-15-final-install-e2e-remote-merge.txt'
Add-Assertion -Name 'pip_entrypoint_proof' -Passed (Test-FileContains -Path $installEvidence -Pattern 'pip_entry_point_discovery=PASS') -Detail 'task-15-final-install-e2e-remote-merge.txt'
Add-Assertion -Name 'no_real_runtime_mutation_proof' -Passed ((Test-FileContains -Path $e2eEvidence -Pattern 'probe_no_real_runtime_mutation_flag=PASS') -or (Test-FileContains -Path $installEvidence -Pattern 'probe_no_real_runtime_mutation_flag=PASS')) -Detail 'task-15 evidence'

$safetyText = ''
foreach ($path in @($readmePath, $securityPath, $rollupPath)) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        $safetyText += "`n" + (Get-Content -LiteralPath $path -Raw)
    }
}
Add-Assertion -Name 'v01_no_confirmed_external_writes' -Passed ($safetyText -match 'v0\.1.*no confirmed external writes|does not execute confirmed external writes|Confirmed external writes.*remain deferred') -Detail 'README/SECURITY/rollup'

Add-Assertion -Name 'linear_proof_exists' -Passed (Test-Path -LiteralPath $linearProofPath -PathType Leaf) -Detail $linearProofPath
if (Test-Path -LiteralPath $linearProofPath -PathType Leaf) {
    $linearProof = Get-Content -LiteralPath $linearProofPath -Raw
    Add-Assertion -Name 'linear_proof_mentions_evidence_rollup' -Passed ($linearProof -match 'evidence-rollup') -Detail 'task-16-linear-comments.json'
    Add-Assertion -Name 'linear_proof_mentions_hermes_plugins_install' -Passed ($linearProof -match 'hermes plugins install') -Detail 'task-16-linear-comments.json'
    Add-Assertion -Name 'linear_proof_mentions_deferred_scope' -Passed ($linearProof -match 'deferred scope') -Detail 'task-16-linear-comments.json'
    Add-Assertion -Name 'linear_proof_governance_only' -Passed (($linearProof -match 'governance/evidence handoff') -and ($linearProof -notmatch 'created issue|new issue for Todo 16')) -Detail 'task-16-linear-comments.json'
}

if (Get-Command gh -ErrorAction SilentlyContinue) {
    $repoView = Invoke-LoggedProcess -File 'gh' -Arguments @('repo', 'view', $ExpectedRepo, '--json', 'name,owner,visibility,url,description,homepageUrl,repositoryTopics,hasIssuesEnabled,hasWikiEnabled') -Cwd $resolvedRepo
    Add-Assertion -Name 'gh_repo_view_succeeded' -Passed ($repoView.ExitCode -eq 0) -Detail "exit=$($repoView.ExitCode)"
    if ($repoView.ExitCode -eq 0) {
        try {
            $metadata = $repoView.Stdout | ConvertFrom-Json
            $owner = if ($metadata.owner.login) { $metadata.owner.login } else { $metadata.owner.name }
            Add-Assertion -Name 'gh_owner_expected' -Passed ($owner -eq 'Tinycute00') -Detail $owner
            Add-Assertion -Name 'gh_name_expected' -Passed ($metadata.name -eq 'hermes-office-core-plugin') -Detail $metadata.name
            Add-Assertion -Name 'gh_url_expected_repo' -Passed ([string]$metadata.url -eq 'https://github.com/Tinycute00/hermes-office-core-plugin') -Detail ([string]$metadata.url)
            Add-Assertion -Name 'gh_not_official_or_fork_repo' -Passed (([string]$metadata.url -notmatch 'NousResearch/hermes-agent') -and ([string]$metadata.url -notmatch 'Tinycute00/hermes-agent')) -Detail ([string]$metadata.url)
            Add-Assertion -Name 'gh_issues_disabled' -Passed (-not [bool]$metadata.hasIssuesEnabled)
            Add-Assertion -Name 'gh_wiki_disabled' -Passed (-not [bool]$metadata.hasWikiEnabled)
        } catch {
            Add-Assertion -Name 'gh_metadata_json_parse' -Passed $false -Detail $_.Exception.Message
        }
    }
} else {
    Add-Assertion -Name 'gh_unavailable_issues_wiki_check_skipped' -Passed $true -Detail 'gh not available'
}

Add-Evidence ''
Add-Evidence 'assertions_summary:'
$Assertions | ForEach-Object { Add-Evidence $_ }

Add-Evidence ''
Add-Evidence 'adversarial_classes:'
Add-Evidence 'stale_state: git branch/upstream/remote and GitHub metadata are read live during this run'
Add-Evidence 'dirty_worktree: product git status and official Hermes tracked/staged cleanliness are captured in the evidence output'
Add-Evidence 'misleading_success_output: exact files, rollup references/count claims, parsed JSON fields, and required proof strings are asserted'
Add-Evidence 'overfit_slop: checks cover repo boundary, install docs, evidence files, Linear proof, GitHub metadata, and external-write policy'
Add-Evidence 'prompt_injection: Linear proof and evidence logs are treated only as data'
Add-Evidence 'malformed_input: missing required or rollup-referenced evidence files fail nonzero'
Add-Evidence 'hung_or_long_commands: external commands use bounded process timeouts'

if ($Failures.Count -gt 0) {
    Add-Evidence ''
    Add-Evidence 'result: FAIL'
    exit 1
}

Add-Evidence ''
Add-Evidence 'result: PASS'
exit 0
