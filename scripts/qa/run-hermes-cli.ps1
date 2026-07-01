[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Evidence,

    [Parameter(Mandatory = $true)]
    [string]$HermesHome,

    [int]$TimeoutSeconds = 60,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$HermesArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$HermesCheckout = 'C:\Users\88697\AppData\Local\hermes\hermes-agent'

function Add-Evidence {
    param([string]$Line)
    Write-Host $Line
    Add-Content -LiteralPath $Evidence -Value $Line -Encoding UTF8
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

function Get-HermesCheckoutStatus {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'git'
    $psi.Arguments = Join-ProcessArguments @(
        '-C',
        $HermesCheckout,
        'status',
        '--short',
        '--untracked-files=all'
    )
    $psi.WorkingDirectory = $HermesCheckout
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($psi)
    $completed = $process.WaitForExit(30000)
    if (-not $completed) {
        try {
            $process.Kill()
        } catch {
        }
        return [pscustomobject]@{
            ExitCode = 124
            Stdout = ''
            Stderr = 'git status timed out'
        }
    }

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $process.StandardOutput.ReadToEnd()
        Stderr = $process.StandardError.ReadToEnd()
    }
}

function Add-StatusEvidence {
    param(
        [string]$Label,
        [pscustomobject]$Status
    )
    Add-Evidence "$Label command: git -C $HermesCheckout status --short --untracked-files=all"
    Add-Evidence "$Label exit: $($Status.ExitCode)"
    Add-Evidence "$Label stdout:"
    if ([string]::IsNullOrWhiteSpace($Status.Stdout)) {
        Add-Evidence '<empty>'
    } else {
        $Status.Stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ }
    }
    Add-Evidence "$Label stderr:"
    if ([string]::IsNullOrWhiteSpace($Status.Stderr)) {
        Add-Evidence '<empty>'
    } else {
        $Status.Stderr.TrimEnd() -split "`r?`n" | ForEach-Object { Add-Evidence $_ }
    }
}

function Invoke-BoundedProcess {
    param(
        [string]$File,
        [string[]]$Arguments,
        [string]$Cwd,
        [hashtable]$Environment = @{}
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $psi.Arguments = Join-ProcessArguments $Arguments
    $psi.WorkingDirectory = $Cwd
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    foreach ($key in $Environment.Keys) {
        $psi.Environment[$key] = [string]$Environment[$key]
    }

    Add-Evidence ''
    Add-Evidence ('command: ' + (($File + ' ' + (Join-ProcessArguments $Arguments)).Trim()))
    Add-Evidence "cwd: $Cwd"
    Add-Evidence "timeout_seconds: $TimeoutSeconds"
    Add-Evidence "env:HERMES_HOME: $($psi.Environment['HERMES_HOME'])"
    if ($psi.Environment.ContainsKey('UV_PROJECT_ENVIRONMENT')) {
        Add-Evidence "env:UV_PROJECT_ENVIRONMENT: $($psi.Environment['UV_PROJECT_ENVIRONMENT'])"
    }
    if ($psi.Environment.ContainsKey('HERMES_PLUGINS_DEBUG')) {
        Add-Evidence "env:HERMES_PLUGINS_DEBUG: $($psi.Environment['HERMES_PLUGINS_DEBUG'])"
    }

    try {
        $process = [System.Diagnostics.Process]::Start($psi)
    } catch {
        Add-Evidence 'exit: 127'
        Add-Evidence 'stdout:'
        Add-Evidence '<empty>'
        Add-Evidence 'stderr:'
        Add-Evidence $_.Exception.Message
        return [pscustomobject]@{
            ExitCode = 127
            Stdout = ''
            Stderr = $_.Exception.Message
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
        ExitCode = $process.ExitCode
        Stdout = $stdout
        Stderr = $stderr
        TimedOut = $false
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Evidence) | Out-Null

Add-Evidence ''
Add-Evidence 'run-hermes-cli'
Add-Evidence ('started_at: ' + (Get-Date).ToString('o'))
Add-Evidence "hermes_home: $HermesHome"
Add-Evidence ('hermes_args: ' + (Join-ProcessArguments $HermesArgs))

$beforeStatus = Get-HermesCheckoutStatus
Add-StatusEvidence -Label 'hermes_checkout_before' -Status $beforeStatus

$envVars = @{
    HERMES_HOME = $HermesHome
}
if (-not [string]::IsNullOrWhiteSpace($env:HERMES_PLUGINS_DEBUG)) {
    $envVars['HERMES_PLUGINS_DEBUG'] = $env:HERMES_PLUGINS_DEBUG
}

$uvEnvironment = $null
$uv = Get-Command uv -ErrorAction SilentlyContinue
$installedHermes = Get-Command hermes -ErrorAction SilentlyContinue
$installedLauncherBroken = $false
if ($installedHermes) {
    Add-Evidence "hermes_cli_source: installed:$($installedHermes.Source)"
    $result = Invoke-BoundedProcess -File $installedHermes.Source -Arguments $HermesArgs -Cwd (Get-Location).ProviderPath -Environment $envVars
    $installedLauncherBroken = $result.ExitCode -ne 0 -and (
        $result.Stderr -match 'not recognized' -or
        $result.Stderr -match 'No such file' -or
        $result.Stderr -match 'cannot find'
    )
    if ($installedLauncherBroken) {
        Add-Evidence 'hermes_cli_source_fallback_reason: installed launcher target unavailable'
    }
}

if ((-not $installedHermes) -or $installedLauncherBroken) {
    if (-not $uv) {
        Add-Evidence 'hermes_cli_source: unavailable'
        Add-Evidence 'assertion: fallback_uv_available=FAIL - uv command not found'
        exit 127
    }

    $uvEnvironment = Join-Path ([System.IO.Path]::GetTempPath()) ("hermes-cli-uv-env-$PID")
    if (Test-Path -LiteralPath $uvEnvironment) {
        Remove-Item -LiteralPath $uvEnvironment -Recurse -Force
    }
    $envVars['UV_PROJECT_ENVIRONMENT'] = $uvEnvironment
    Add-Evidence "hermes_cli_source: fallback_uv_project:$HermesCheckout"
    $arguments = @('run', '--project', $HermesCheckout, '--extra', 'dev', 'hermes') + $HermesArgs
    $result = Invoke-BoundedProcess -File $uv.Source -Arguments $arguments -Cwd (Get-Location).ProviderPath -Environment $envVars
}

$afterStatus = Get-HermesCheckoutStatus
Add-StatusEvidence -Label 'hermes_checkout_after' -Status $afterStatus
$statusUnchanged = ($beforeStatus.ExitCode -eq $afterStatus.ExitCode) -and ($beforeStatus.Stdout -eq $afterStatus.Stdout) -and ($beforeStatus.Stderr -eq $afterStatus.Stderr)
if ($statusUnchanged) {
    Add-Evidence 'assertion: hermes_checkout_status_unchanged=PASS'
} else {
    Add-Evidence 'assertion: hermes_checkout_status_unchanged=FAIL'
}

if ($uvEnvironment) {
    Remove-Item -LiteralPath $uvEnvironment -Recurse -Force -ErrorAction SilentlyContinue
    Add-Evidence "uv_environment_cleanup_path: $uvEnvironment"
    Add-Evidence ('assertion: uv_environment_removed=' + ($(if (Test-Path -LiteralPath $uvEnvironment) { 'FAIL' } else { 'PASS' })))
}

if (-not [string]::IsNullOrEmpty($result.Stdout)) {
    Write-Output $result.Stdout.TrimEnd()
}

if (-not $statusUnchanged) {
    exit 70
}

exit $result.ExitCode
