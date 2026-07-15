param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [string]$PluginRoot,
    [string]$PluginData,
    [string]$ManagedMarker
)

$ErrorActionPreference = "Stop"
if ($PluginRoot) { $env:PLUGIN_ROOT = $PluginRoot }
if ($PluginData) { $env:PLUGIN_DATA = $PluginData }
if ($ManagedMarker) {
    if ($ManagedMarker -ne "OFFICE_OS_MANAGED_HOOK=1") {
        Write-Error "Office OS received an invalid managed Hook marker."
        exit 2
    }
    $env:OFFICE_OS_MANAGED_HOOK = "1"
}
$pythonCandidates = @(
    (Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
    (Join-Path $HOME ".codex\runtimes\python\python.exe")
)

$processCandidates = @()
foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate) {
        $processCandidates += [pscustomobject]@{
            FileName = $candidate
            Arguments = @($ScriptPath)
        }
    }
}

foreach ($commandName in @("python3", "python", "py")) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        $arguments = @($ScriptPath)
        if ($commandName -eq "py") {
            $arguments = @("-3", $ScriptPath)
        }
        $processCandidates += [pscustomobject]@{
            FileName = $command.Source
            Arguments = $arguments
        }
    }
}

foreach ($candidate in $processCandidates) {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $candidate.FileName
    $startInfo.Arguments = ($candidate.Arguments | ForEach-Object {
        '"' + $_.Replace('"', '\"') + '"'
    }) -join ' '
    $startInfo.UseShellExecute = $false

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $process.WaitForExit()
    exit $process.ExitCode
}

Write-Error "Office OS requires Python 3. Install Python or use a Codex desktop runtime that bundles it."
exit 127
