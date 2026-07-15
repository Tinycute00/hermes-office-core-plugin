param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [string]$PluginRoot,
    [string]$PluginData,
    [string]$ManagedMarker
)

$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;

public sealed class OfficeOsRawHandleStream : Stream
{
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GetStdHandle(int standardHandle);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool WriteFile(
        IntPtr handle,
        byte[] buffer,
        uint count,
        out uint written,
        IntPtr overlapped);

    private readonly IntPtr handle;

    public OfficeOsRawHandleStream(int standardHandle)
    {
        handle = GetStdHandle(standardHandle);
        if (handle == IntPtr.Zero || handle == new IntPtr(-1))
        {
            throw new IOException("Office OS could not acquire a standard stream handle.");
        }
    }

    public override bool CanRead { get { return false; } }
    public override bool CanSeek { get { return false; } }
    public override bool CanWrite { get { return true; } }
    public override long Length { get { throw new NotSupportedException(); } }
    public override long Position
    {
        get { throw new NotSupportedException(); }
        set { throw new NotSupportedException(); }
    }

    public override void Flush() { }
    public override Task FlushAsync(CancellationToken cancellationToken) { return Task.FromResult(0); }
    public override int Read(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    public override long Seek(long offset, SeekOrigin origin) { throw new NotSupportedException(); }
    public override void SetLength(long value) { throw new NotSupportedException(); }

    public override void Write(byte[] buffer, int offset, int count)
    {
        while (count > 0)
        {
            byte[] segment;
            if (offset == 0 && count == buffer.Length)
            {
                segment = buffer;
            }
            else
            {
                segment = new byte[count];
                Buffer.BlockCopy(buffer, offset, segment, 0, count);
            }
            uint written;
            if (!WriteFile(handle, segment, (uint)segment.Length, out written, IntPtr.Zero) || written == 0)
            {
                throw new IOException("Office OS could not write a raw standard stream.", new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error()));
            }
            offset += (int)written;
            count -= (int)written;
        }
    }

    public override Task WriteAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Write(buffer, offset, count);
        return Task.FromResult(0);
    }
}
'@

$rawStdout = [OfficeOsRawHandleStream]::new(-11)
$rawStderr = [OfficeOsRawHandleStream]::new(-12)
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
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()

    $stdoutTask = $process.StandardOutput.BaseStream.CopyToAsync(
        $rawStdout
    )
    $stderrTask = $process.StandardError.BaseStream.CopyToAsync(
        $rawStderr
    )
    $inputStream = [Console]::OpenStandardInput()
    $buffer = [byte[]]::new(65536)
    while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $process.StandardInput.BaseStream.Write($buffer, 0, $count)
    }
    $process.StandardInput.BaseStream.Flush()
    $process.StandardInput.BaseStream.Dispose()
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    [void]$stdoutTask.GetAwaiter().GetResult()
    [void]$stderrTask.GetAwaiter().GetResult()
    exit $exitCode
}

Write-Error "Office OS requires Python 3. Install Python or use a Codex desktop runtime that bundles it."
exit 127
