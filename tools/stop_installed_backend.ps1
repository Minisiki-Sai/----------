param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [int]$GracefulTimeoutSeconds = 8,
    [int]$UnlockTimeoutSeconds = 12,
    [string]$LogPath = "",
    [switch]$SideBySide
)

# Keep this file ASCII-only: Windows PowerShell 5.1 may use an ANSI code page.
$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd([char[]]@('\', '/'))
if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd([char[]]@('\', '/'))) {
    throw "An installation directory, not a drive root, is required."
}
$runtimeDirectory = Join-Path $root "runtime"
$runtimePrefix = $runtimeDirectory + [IO.Path]::DirectorySeparatorChar
$desktopPath = Join-Path $root "dragon-nest-simulator-v420.exe"

function Write-PreflightLog {
    param([string]$Message)
    $line = "{0:O} {1}" -f [DateTime]::UtcNow, $Message
    if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
        try { Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 }
        catch { Write-Warning "Could not write installer log: $($_.Exception.Message)" }
    }
}

function Get-OwnedProcesses {
    param([switch]$Desktop)

    try {
        $candidates = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'DragonNestEngine%.exe' OR Name = 'dragon-nest-simulator-v420.exe'" -ErrorAction Stop)
    }
    catch {
        Write-PreflightLog "CIM process query failed; using process-path fallback: $($_.Exception.Message)"
        $candidates = @(
            Get-Process -Name "DragonNestEngine*", "dragon-nest-simulator-v420" -ErrorAction SilentlyContinue |
                ForEach-Object {
                    try {
                        [pscustomobject]@{ ProcessId = $_.Id; ExecutablePath = $_.Path; CreationDate = $_.StartTime }
                    }
                    catch { Write-PreflightLog "Cannot read process identity." }
                }
        )
    }
    @(
        foreach ($candidate in $candidates) {
            $path = [string]$candidate.ExecutablePath
            if ([string]::IsNullOrWhiteSpace($path)) {
                try { $path = (Get-Process -Id $candidate.ProcessId -ErrorAction Stop).Path }
                catch { continue }
            }
            if ([string]::IsNullOrWhiteSpace($path)) { continue }
            try { $path = [IO.Path]::GetFullPath($path) }
            catch { continue }
            $matchesPath = if ($Desktop) {
                $path.Equals($desktopPath, [StringComparison]::OrdinalIgnoreCase)
            }
            else {
                [IO.Path]::GetFileName($path) -like "DragonNestEngine*.exe" -and
                    $path.StartsWith($runtimePrefix, [StringComparison]::OrdinalIgnoreCase)
            }
            if ($matchesPath) {
                [pscustomobject]@{
                    ProcessId = $candidate.ProcessId
                    ExecutablePath = $path
                    CreationDate = $candidate.CreationDate
                }
            }
        }
    ) | Sort-Object CreationDate
}

function Stop-OwnedBackendProcess {
    param($Process)
    # Revalidate path AND creation time immediately before using a cached PID.
    if ($null -eq $Process.CreationDate) {
        Write-PreflightLog "Skipping PID=$($Process.ProcessId): creation time is unavailable."
        return
    }
    $current = @(Get-OwnedProcesses | Where-Object {
        $_.ProcessId -eq $Process.ProcessId -and
        $_.ExecutablePath -eq $Process.ExecutablePath -and
        $_.CreationDate -eq $Process.CreationDate
    })
    if ($current.Count -eq 0) { return }
    try {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction Stop
        Write-PreflightLog "Stopped owned backend PID=$($Process.ProcessId) path=$($Process.ExecutablePath)"
    }
    catch { Write-PreflightLog "Cannot stop PID=$($Process.ProcessId): $($_.Exception.Message)" }
}

function Get-InstallFileIssues {
    # Exclusive write access WITHOUT truncating or modifying existing binaries.
    # Image mappings, inaccessible processes and scanners may still hold files
    # even after process enumeration reports no matching engine.
    $files = @()
    try {
        if (Test-Path -LiteralPath $desktopPath) { $files += Get-Item -LiteralPath $desktopPath -ErrorAction Stop }
        # A side-by-side update never writes old engine files. Their read-only
        # attributes / third-party locks must not block installing a new copy.
        # Process shutdown is still mandatory, independently of this probe.
        if (-not $SideBySide -and (Test-Path -LiteralPath $runtimeDirectory)) {
            $files += @(Get-ChildItem -LiteralPath $runtimeDirectory -Recurse -File -ErrorAction Stop |
                Where-Object { $_.Extension -in @(".exe", ".dll", ".pyd") })
        }
    }
    catch { [pscustomobject]@{ Path = $runtimeDirectory; Reason = $_.Exception.Message } }
    foreach ($file in $files) {
        $stream = $null
        try {
            $stream = [IO.File]::Open($file.FullName, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        }
        catch { [pscustomobject]@{ Path = $file.FullName; Reason = $_.Exception.Message } }
        finally { if ($null -ne $stream) { $stream.Dispose() } }
    }
    if (Test-Path -LiteralPath $root) {
        $probe = $null
        try {
            # DeleteOnClose removes only our new, randomly named empty probe.
            $probePath = Join-Path $root (".dragon-nest-update-" + [Guid]::NewGuid().ToString("N") + ".tmp")
            $probe = [IO.FileStream]::new($probePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::None, 4096, [IO.FileOptions]::DeleteOnClose)
        }
        catch { [pscustomobject]@{ Path = $root; Reason = $_.Exception.Message } }
        finally { if ($null -ne $probe) { $probe.Dispose() } }
    }
}

function Invoke-InstallerBackendPreflight {
    Write-PreflightLog "Begin preflight install_root=$root"
    # The NSIS close prompt must run BEFORE this helper. Never report success
    # for an open desktop, including a relaunch during an update.
    if (@(Get-OwnedProcesses -Desktop).Count -gt 0) {
        Write-PreflightLog "Blocked: desktop is still running in this installation."
        Write-Output "The desktop is still running. Close it before retrying the installation."
        return 3
    }
    $owned = @(Get-OwnedProcesses)
    if ($owned.Count -gt 0) {
        foreach ($process in @($owned | Select-Object -First 3)) {
            Write-PreflightLog "Requesting shutdown PID=$($process.ProcessId) path=$($process.ExecutablePath)"
            try {
                $uri = "http://127.0.0.1:48710/api/shutdown?pid=$($process.ProcessId)"
                Invoke-WebRequest -UseBasicParsing -Method Post -Uri $uri -TimeoutSec 1 -ErrorAction Stop | Out-Null
                break
            }
            catch { Write-PreflightLog "Graceful shutdown unavailable: $($_.Exception.Message)" }
        }
        $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(0, $GracefulTimeoutSeconds))
        while ($owned.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 200
            $owned = @(Get-OwnedProcesses)
        }
    }

    $unlockDeadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(0, $UnlockTimeoutSeconds))
    $lastIssue = ""
    do {
        if (@(Get-OwnedProcesses -Desktop).Count -gt 0) {
            Write-Output "The desktop was reopened during installation. Close it and retry."
            Write-PreflightLog "Blocked: desktop restarted during preflight."
            return 3
        }
        $owned = @(Get-OwnedProcesses)
        foreach ($process in $owned) { Stop-OwnedBackendProcess $process }
        $remaining = @(Get-OwnedProcesses)
        $issues = @(Get-InstallFileIssues)
        if ($remaining.Count -eq 0 -and $issues.Count -eq 0) {
            Write-PreflightLog "Success: no owned engine remains and installation binaries are writable."
            Write-Output "Backend stopped; installation files are unlocked and writable."
            return 0
        }
        $issue = (@($remaining | ForEach-Object { "PID=$($_.ProcessId) path=$($_.ExecutablePath)" }) +
            @($issues | ForEach-Object { "$($_.Path): $($_.Reason)" })) -join "`n"
        if ($issue -ne $lastIssue) { Write-PreflightLog $issue; $lastIssue = $issue }
        if ([DateTime]::UtcNow -ge $unlockDeadline) { break }
        Start-Sleep -Milliseconds 250
    } while ($true)
    Write-Output "Installation files remain busy or unwritable. No installation files were replaced."
    Write-Output ($lastIssue -split "`n" | Select-Object -First 3 | Out-String)
    Write-PreflightLog "Preflight failed; extraction must not start."
    return 2
}

# Dot-sourcing exposes the same production functions for isolated regression
# tests. Normal installer execution always runs the preflight.
if ($MyInvocation.InvocationName -ne '.') {
    $result = @(Invoke-InstallerBackendPreflight)
    $exitCode = [int]$result[-1]
    $result | Select-Object -SkipLast 1 | Write-Output
    exit $exitCode
}
