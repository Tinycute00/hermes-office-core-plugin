param(
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$InstallRoot = (Join-Path $HOME 'plugins\office-os'),
    [string]$PluginData = (Join-Path $HOME '.codex\plugin-data\office-os'),
    [string]$HooksConfig = (Join-Path $HOME '.codex\hooks.json'),
    [string]$CodexConfig = (Join-Path $HOME '.codex\config.toml'),
    [string]$CodexCli = 'codex',
    [string]$Python = 'python',
    [switch]$AcceptOfficeCliDownload,
    [switch]$SkipPluginAdd,
    [switch]$SkipHookActivation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail($Message) {
    Write-Error "Office OS installer: $Message"
    exit 2
}

function Require-OrdinaryDirectory($Path, $Description) {
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if (-not $item.PSIsContainer) { Fail "$Description is not a directory: $Path" }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "$Description is a reparse point: $Path"
    }
    return $item
}

function Require-PluginRoot($Root) {
    $rootItem = Require-OrdinaryDirectory $Root 'source root'
    foreach ($relative in @(
        '.codex-plugin\plugin.json',
        '.mcp.json',
        'skills\office-os\SKILL.md',
        'scripts\office_hook_registry.py',
        'scripts\officecli_manager.py',
        'hooks\hooks.json'
    )) {
        $path = Join-Path $rootItem.FullName $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            Fail "source root is missing $relative"
        }
    }
    return $rootItem
}

function Require-SafeInstallRoot($Path) {
    $expectedParent = Join-Path $HOME 'plugins'
    $parent = Split-Path -Parent $Path
    $leaf = Split-Path -Leaf $Path
    if ([IO.Path]::GetFullPath($parent) -cne [IO.Path]::GetFullPath($expectedParent)) {
        Fail "install root must be the direct personal plugin child: $expectedParent\office-os"
    }
    if ($leaf -cne 'office-os') {
        Fail 'install root leaf must be office-os'
    }
}

function Copy-PluginTree($Source, $Destination) {
    Require-SafeInstallRoot $Destination
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $stage = Join-Path $parent ("office-os.install-stage-{0}" -f ([guid]::NewGuid().ToString('N')))
    try {
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
        $exclude = @('.git', '.omo', '.codegraph', '__pycache__')
        Get-ChildItem -LiteralPath $Source -Force | Where-Object {
            $exclude -notcontains $_.Name
        } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $stage -Recurse -Force
        }
        Require-PluginRoot $stage | Out-Null
        if (Test-Path -LiteralPath $Destination) {
            $existing = Require-OrdinaryDirectory $Destination 'existing install root'
            $manifest = Join-Path $existing.FullName '.codex-plugin\plugin.json'
            if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
                Fail "existing install root is not an Office OS plugin: $Destination"
            }
            Remove-Item -LiteralPath $existing.FullName -Recurse -Force
        }
        Move-Item -LiteralPath $stage -Destination $Destination
    }
    finally {
        if (Test-Path -LiteralPath $stage) {
            Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-JsonCommand($Label, [string[]]$Command) {
    $output = & $Command[0] @($Command | Select-Object -Skip 1)
    if ($LASTEXITCODE -ne 0) { Fail "$Label failed" }
    return ($output -join "`n")
}

$source = Require-PluginRoot $SourceRoot
Copy-PluginTree $source.FullName $InstallRoot
$installed = Require-PluginRoot $InstallRoot
$manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $installed.FullName '.codex-plugin\plugin.json') | ConvertFrom-Json
if ([string]$manifest.name -cne 'office-os') { Fail 'plugin manifest name must be office-os' }

if (-not $SkipPluginAdd) {
    [void](Invoke-JsonCommand 'codex plugin add' @($CodexCli, 'plugin', 'add', 'office-os@personal', '--json'))
}

if (-not $SkipHookActivation) {
    $registry = Join-Path $installed.FullName 'scripts\office_hook_registry.py'
    $registryOutput = & $Python $registry install --plugin-root $installed.FullName --data-root $PluginData --config $HooksConfig --activate --codex-config $CodexConfig
    if ($LASTEXITCODE -ne 0) { Fail 'hook registry activation failed' }
    $registryResult = $registryOutput -join "`n"
} else {
    $registryResult = '{"activation":"skipped"}'
}

$runtime = @{ installed = $false; integrity = 'not_checked'; downloaded = $false }
$previousPluginData = [Environment]::GetEnvironmentVariable('PLUGIN_DATA', 'Process')
$env:PLUGIN_DATA = $PluginData
try {
    $manager = Join-Path $installed.FullName 'scripts\officecli_manager.py'
    $statusText = & $Python $manager status
    if ($LASTEXITCODE -eq 0 -and $statusText) {
        $status = ($statusText -join "`n") | ConvertFrom-Json
        $runtime.installed = [bool]$status.installed
        $runtime.integrity = [string]$status.integrity
        $runtime.version = [string]$status.version
    }
    if ($AcceptOfficeCliDownload -and (-not $runtime.installed -or $runtime.integrity -cne 'verified')) {
        $installText = & $Python $manager install --accept-download
        if ($LASTEXITCODE -ne 0) { Fail 'OfficeCLI runtime installation failed' }
        $installStatus = ($installText -join "`n") | ConvertFrom-Json
        $runtime.installed = [bool]$installStatus.installed
        $runtime.integrity = [string]$installStatus.integrity
        $runtime.version = [string]$installStatus.version
        $runtime.downloaded = [bool]$installStatus.downloaded
    }
}
finally {
    if ($null -eq $previousPluginData) {
        Remove-Item Env:\PLUGIN_DATA -ErrorAction SilentlyContinue
    } else {
        $env:PLUGIN_DATA = $previousPluginData
    }
}

$result = [ordered]@{
    ok = $true
    plugin = 'office-os@personal'
    version = [string]$manifest.version
    installRoot = $installed.FullName
    pluginData = [IO.Path]::GetFullPath($PluginData)
    codexPluginAdd = -not $SkipPluginAdd
    hookActivation = ($registryResult | ConvertFrom-Json).activation
    runtime = $runtime
    next = 'Restart Codex or open /hooks to review trusted hook definitions if prompted.'
}

$result | ConvertTo-Json -Depth 8
