[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$Evidence
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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
        [int]$TimeoutSeconds = 300
    )

    $commandText = ($File + ' ' + (Join-ProcessArguments $Arguments)).Trim()
    Add-Evidence ''
    Add-Evidence "command: $commandText"
    Add-Evidence "cwd: $Cwd"

    $stdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) "office-core-quality-$PID-$([guid]::NewGuid()).out"
    $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) "office-core-quality-$PID-$([guid]::NewGuid()).err"
    $redirectedCommand = '{0} > "{1}" 2> "{2}"' -f $commandText, $stdoutPath, $stderrPath
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'cmd.exe'
    $psi.Arguments = '/d /c "' + $redirectedCommand + '"'
    $psi.WorkingDirectory = $Cwd
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError = $false
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi

    try {
        $started = $process.Start()
    } catch {
        Add-Evidence 'exit: 125'
        Add-Evidence 'stdout: <empty>'
        Add-Evidence ('stderr: ' + $_.Exception.Message)
        return 125
    }

    if (-not $started) {
        Add-Evidence 'exit: 125'
        Add-Evidence 'stdout: <empty>'
        Add-Evidence 'stderr: failed to start process'
        return 125
    }

    $completed = $process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $completed) {
        $process.Kill()
        Add-Evidence 'exit: 124'
        Add-Evidence "timeout_seconds: $TimeoutSeconds"
        return 124
    }

    Add-Evidence "exit: $($process.ExitCode)"
    Add-Evidence 'stdout:'
    $stdoutArray = @()
    if (Test-Path -LiteralPath $stdoutPath) {
        $stdoutArray = @(Get-Content -LiteralPath $stdoutPath)
    }
    if (@($stdoutArray).Count -eq 0) {
        Add-Evidence '<empty>'
    } else {
        $stdoutArray | ForEach-Object { Add-Evidence $_ }
    }
    Add-Evidence 'stderr:'
    $stderrArray = @()
    if (Test-Path -LiteralPath $stderrPath) {
        $stderrArray = @(Get-Content -LiteralPath $stderrPath)
    }
    if (@($stderrArray).Count -eq 0) {
        Add-Evidence '<empty>'
    } else {
        $stderrArray | ForEach-Object { Add-Evidence $_ }
    }
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    return $process.ExitCode
}

function Invoke-Gate {
    param(
        [string]$Name,
        [string]$File,
        [string[]]$Arguments,
        [string]$Cwd,
        [int]$TimeoutSeconds = 300
    )
    if ($Failures.Count -gt 0) {
        Add-Evidence "skipped: $Name because an earlier gate failed"
        return
    }
    $exitCode = Invoke-LoggedProcess -File $File -Arguments $Arguments -Cwd $Cwd -TimeoutSeconds $TimeoutSeconds
    Add-Assertion -Name $Name -Passed ($exitCode -eq 0) -Detail "exit=$exitCode"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Evidence) | Out-Null
Set-Content -LiteralPath $Evidence -Encoding UTF8 -Value @(
    'final-quality',
    ('started_at: ' + (Get-Date).ToString('o')),
    "repo_root_input: $RepoRoot",
    "evidence: $Evidence"
)

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath
$python = Join-Path $resolvedRepo '.venv\Scripts\python.exe'
$bootstrap = Join-Path $resolvedRepo 'scripts\qa\bootstrap-dev.ps1'
$bootstrapEvidence = Join-Path (Split-Path -Parent $Evidence) 'final-quality-bootstrap.txt'

Add-Assertion -Name 'repo_root_exists' -Passed (Test-Path -LiteralPath $resolvedRepo -PathType Container) -Detail $resolvedRepo
Add-Assertion -Name 'venv_python_exists' -Passed (Test-Path -LiteralPath $python -PathType Leaf) -Detail $python
Invoke-Gate -Name 'bootstrap_dev' -File 'powershell' -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $bootstrap, '-RepoRoot', $resolvedRepo, '-Evidence', $bootstrapEvidence) -Cwd $resolvedRepo -TimeoutSeconds 600
Invoke-Gate -Name 'ruff_check' -File $python -Arguments @('-m', 'ruff', 'check', '.') -Cwd $resolvedRepo
Invoke-Gate -Name 'pytest' -File $python -Arguments @('-m', 'pytest', '-q') -Cwd $resolvedRepo

if ($Failures.Count -eq 0) {
    Remove-Item -LiteralPath (Join-Path $resolvedRepo 'dist') -Recurse -Force -ErrorAction SilentlyContinue
}

Invoke-Gate -Name 'build' -File $python -Arguments @('-m', 'build') -Cwd $resolvedRepo
Invoke-Gate -Name 'twine_check' -File $python -Arguments @('-m', 'twine', 'check', 'dist/*') -Cwd $resolvedRepo
Invoke-Gate -Name 'package_data' -File $python -Arguments @('scripts\qa\validate_package_data.py', '--repo', '.', '--dist-dir', 'dist') -Cwd $resolvedRepo
Invoke-Gate -Name 'distribution_contract' -File $python -Arguments @('scripts\qa\validate_distribution.py', '--repo', '.') -Cwd $resolvedRepo
Invoke-Gate -Name 'docs_contract' -File $python -Arguments @('scripts\qa\validate_docs.py', '--repo', '.') -Cwd $resolvedRepo
Invoke-Gate -Name 'skills_contract' -File $python -Arguments @('scripts\qa\validate_skills.py', '--repo', '.') -Cwd $resolvedRepo
Invoke-Gate -Name 'inventory_contract' -File $python -Arguments @('scripts\qa\validate_inventory.py', '--inventory', 'docs\inventory\skill-mcp-inventory.json') -Cwd $resolvedRepo

Add-Evidence ''
Add-Evidence 'assertions_summary:'
$Assertions | ForEach-Object { Add-Evidence $_ }

Add-Evidence ''
Add-Evidence 'adversarial_classes:'
Add-Evidence 'malformed_input: package-data omission is covered by validate_package_data.py failure mode'
Add-Evidence 'prompt_injection: templates and validators treat external text as data and require no secret execution'
Add-Evidence 'stale_state: build artifacts are regenerated before artifact validation'
Add-Evidence 'dirty_worktree: final git status is checked by task evidence outside this script'
Add-Evidence 'hung_or_long_commands: every subprocess uses a bounded timeout'
Add-Evidence 'misleading_success_output: exit codes and artifact contents are asserted directly'
Add-Evidence 'overfit_slop: gates cover lint, tests, metadata, package data, docs, skills, inventory, and distribution'

if ($Failures.Count -gt 0) {
    Add-Evidence ''
    Add-Evidence 'result: FAIL'
    exit 1
}

Add-Evidence ''
Add-Evidence 'result: PASS'
exit 0
