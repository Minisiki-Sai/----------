param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [int]$Port = 1420
)

$ErrorActionPreference = "Stop"

function Get-PortListeners {
    param([int]$LocalPort)

    @(Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

$projectPath = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
$listeners = Get-PortListeners -LocalPort $Port
if ($listeners.Count -eq 0) {
    Write-Host "[OK] Port $Port is available."
    exit 0
}

$foreignProcesses = @()
foreach ($listener in $listeners) {
    $processId = [int]$listener.OwningProcess
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    $commandLine = if ($null -ne $process) { [string]$process.CommandLine } else { "" }
    $executableName = if ($null -ne $process) { [string]$process.Name } else { "unknown" }

    $isProjectVite = (
        $executableName -ieq "node.exe" -and
        $commandLine.IndexOf($projectPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $commandLine.IndexOf("vite", [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    )

    if ($isProjectVite) {
        Write-Host "[CLEAN] Stopping stale V4.10 Vite process PID $processId..."
        Stop-Process -Id $processId -Force -ErrorAction Stop
    }
    else {
        $foreignProcesses += [pscustomobject]@{
            Id = $processId
            Name = $executableName
            CommandLine = $commandLine
        }
    }
}

if ($foreignProcesses.Count -gt 0) {
    foreach ($item in $foreignProcesses) {
        Write-Host "[BLOCKED] Port $Port is used by PID $($item.Id) ($($item.Name))."
        if (-not [string]::IsNullOrWhiteSpace($item.CommandLine)) {
            Write-Host "          $($item.CommandLine)"
        }
    }
    exit 1
}

$deadline = [DateTime]::UtcNow.AddSeconds(5)
do {
    Start-Sleep -Milliseconds 100
    $remaining = Get-PortListeners -LocalPort $Port
} while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)

if ($remaining.Count -gt 0) {
    Write-Host "[ERROR] Port $Port is still occupied after stopping the stale process."
    exit 1
}

Write-Host "[OK] Port $Port is available."
exit 0
