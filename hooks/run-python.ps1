param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath
)

$ErrorActionPreference = "Stop"
$pythonCandidates = @(
    (Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
    (Join-Path $HOME ".codex\runtimes\python\python.exe")
)

foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate) {
        & $candidate $ScriptPath
        exit $LASTEXITCODE
    }
}

foreach ($commandName in @("python3", "python", "py")) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        if ($commandName -eq "py") {
            & $command.Source -3 $ScriptPath
        } else {
            & $command.Source $ScriptPath
        }
        exit $LASTEXITCODE
    }
}

Write-Error "Office OS requires Python 3. Install Python or use a Codex desktop runtime that bundles it."
exit 127
