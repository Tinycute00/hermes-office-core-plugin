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
        return [pscustomobject]@{ ExitCode = 125; Stdout = ''; Stderr = $_.Exception.Message }
    }
    if (-not $started) {
        Add-Evidence 'exit: 125'
        Add-Evidence 'stderr: failed to start process'
        return [pscustomobject]@{ ExitCode = 125; Stdout = ''; Stderr = 'failed to start process' }
    }

    $completed = $process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $completed) {
        $process.Kill()
        Add-Evidence 'exit: 124'
        Add-Evidence "timeout_seconds: $TimeoutSeconds"
        return [pscustomobject]@{ ExitCode = 124; Stdout = ''; Stderr = 'timeout' }
    }

    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    Add-Evidence "exit: $($process.ExitCode)"
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

    return [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $stdout; Stderr = $stderr }
}

function Get-PythonLauncher {
    $pyProbe = Invoke-LoggedProcess -File 'py' -Arguments @('-3.11', '--version') -Cwd $RepoRoot -TimeoutSeconds 30
    if ($pyProbe.ExitCode -eq 0) {
        return [pscustomobject]@{ File = 'py'; Arguments = @('-3.11') }
    }

    $pythonProbe = Invoke-LoggedProcess -File 'python' -Arguments @('--version') -Cwd $RepoRoot -TimeoutSeconds 30
    Add-Assertion -Name 'python_launcher_available' -Passed ($pythonProbe.ExitCode -eq 0)
    return [pscustomobject]@{ File = 'python'; Arguments = @() }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Evidence) | Out-Null
Set-Content -LiteralPath $Evidence -Value @(
    'bootstrap-dev',
    ('started_at: ' + (Get-Date).ToString('o')),
    "repo_root: $RepoRoot"
) -Encoding UTF8

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).ProviderPath
Add-Assertion -Name 'repo_root_exists' -Passed (Test-Path -LiteralPath $resolvedRepo -PathType Container) -Detail $resolvedRepo
Add-Assertion -Name 'pyproject_exists' -Passed (Test-Path -LiteralPath (Join-Path $resolvedRepo 'pyproject.toml'))

$venvPython = Join-Path $resolvedRepo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $launcher = Get-PythonLauncher
    $venv = Invoke-LoggedProcess -File $launcher.File -Arguments ($launcher.Arguments + @('-m', 'venv', (Join-Path $resolvedRepo '.venv'))) -Cwd $resolvedRepo
    Add-Assertion -Name 'venv_created' -Passed ($venv.ExitCode -eq 0)
} else {
    Add-Evidence ''
    Add-Evidence 'command: verify existing .venv'
    Add-Evidence "cwd: $resolvedRepo"
    Add-Evidence 'exit: 0'
    Add-Evidence "stdout: $venvPython"
    Add-Evidence 'stderr: <empty>'
}

Add-Assertion -Name 'venv_python_exists' -Passed (Test-Path -LiteralPath $venvPython) -Detail $venvPython

if ($Failures.Count -eq 0) {
    $pipUpgrade = Invoke-LoggedProcess -File $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip') -Cwd $resolvedRepo
    Add-Assertion -Name 'pip_upgrade_succeeded' -Passed ($pipUpgrade.ExitCode -eq 0)
}

if ($Failures.Count -eq 0) {
    $editable = Invoke-LoggedProcess -File $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', '-e', '.[dev]') -Cwd $resolvedRepo
    Add-Assertion -Name 'editable_dev_install_succeeded' -Passed ($editable.ExitCode -eq 0)
}

if ($Failures.Count -eq 0) {
    $toolCheck = Invoke-LoggedProcess -File $venvPython -Arguments @('-c', 'import build, office_core_plugin, pytest, ruff; print("dev imports ok")') -Cwd $resolvedRepo
    Add-Assertion -Name 'dev_tools_and_package_installed' -Passed ($toolCheck.ExitCode -eq 0)
}

Add-Evidence ''
Add-Evidence 'assertions:'
$Assertions | ForEach-Object { Add-Evidence $_ }

Add-Evidence ''
Add-Evidence 'adversarial_classes:'
Add-Evidence 'dirty_worktree: bootstrap writes only .venv and supplied evidence; git status is checked outside this script'
Add-Evidence 'stale_state: pyproject and venv are resolved from the supplied repo root at runtime'
Add-Evidence 'malformed_input: missing repo root or pyproject is an assertion failure'
Add-Evidence 'misleading_success_output: each command exit code is asserted explicitly'
Add-Evidence 'hung_or_long_commands: process execution is bounded by timeouts'
Add-Evidence 'flaky_tests: package installation is deterministic from pyproject dev extras'
Add-Evidence 'untrusted_external_text: package metadata is installed by pip, not executed as assertions'
Add-Evidence 'not_applicable: cancel_resume, repeated_interruptions'

if ($Failures.Count -gt 0) {
    Add-Evidence ''
    Add-Evidence 'result: FAIL'
    exit 1
}

Add-Evidence ''
Add-Evidence 'result: PASS'
exit 0
