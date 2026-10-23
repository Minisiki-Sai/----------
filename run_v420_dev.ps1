$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontend = Join-Path $root "frontend"

function Stop-StaleDevListener {
    param([int]$Port)

    $listeners = Get-NetTCPConnection `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $process = Get-CimInstance `
            Win32_Process `
            -Filter "ProcessId=$($listener.OwningProcess)" `
            -ErrorAction SilentlyContinue
        $commandLine = if ($process) { [string]$process.CommandLine } else { "" }
        if (-not $commandLine.Contains($root)) {
            if ($Port -eq 48710 -and (Stop-RunningDragonNestDesktop -BackendPid $listener.OwningProcess)) {
                continue
            }
            throw "Port $Port is occupied by another application (PID $($listener.OwningProcess))."
        }
        Start-Process `
            -FilePath "taskkill.exe" `
            -ArgumentList @("/PID", "$($listener.OwningProcess)", "/T", "/F") `
            -WindowStyle Hidden `
            -Wait | Out-Null
    }
}

function Wait-LocalPortRelease {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 10
    )

    $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1, $TimeoutSeconds))
    do {
        $listener = Get-NetTCPConnection `
            -LocalPort $Port `
            -State Listen `
            -ErrorAction SilentlyContinue
        if (-not $listener) {
            return $true
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Stop-RunningDragonNestDesktop {
    param([int]$BackendPid)

    try {
        $health = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:48710/api/health" `
            -TimeoutSec 2 `
            -ErrorAction Stop
    }
    catch {
        return $false
    }
    if ([string]$health.product -ne "dragon-nest-battle-platform") {
        return $false
    }

    $backend = Get-CimInstance `
        Win32_Process `
        -Filter "ProcessId=$BackendPid" `
        -ErrorAction SilentlyContinue
    if (-not $backend) {
        return (Wait-LocalPortRelease -Port 48710 -TimeoutSeconds 2)
    }
    $desktopPid = [int]$backend.ParentProcessId
    $desktopInfo = Get-CimInstance `
        Win32_Process `
        -Filter "ProcessId=$desktopPid" `
        -ErrorAction SilentlyContinue
    $desktopName = if ($desktopInfo) { [string]$desktopInfo.Name } else { "" }
    if ($desktopName -notmatch '^dragon-nest-simulator-v420(?:\.exe)?$') {
        # A previous development launch can leave only its Python backend
        # behind. Its command line may contain a relative script path, so the
        # caller cannot associate it with this checkout by path alone. Only
        # reclaim the listener after both the health product and the exact
        # backend/port signature have been verified.
        $backendCommandLine = [string]$backend.CommandLine
        $isVerifiedOrphanBackend = (
            [string]$backend.Name -match '^python(?:w)?\.exe$' -and
            $backendCommandLine -match '(?i)(?:^|[\\/\s])v420_backend\.py(?:\s|$)' -and
            $backendCommandLine -match '(?i)--port(?:=|\s+)48710(?:\s|$)'
        )
        if (-not $isVerifiedOrphanBackend) {
            return $false
        }

        Write-Host "Detected an orphaned ACT development backend. Closing it before restart..."
        try {
            Invoke-WebRequest `
                -UseBasicParsing `
                -Method Post `
                -Uri "http://127.0.0.1:48710/api/shutdown?pid=$BackendPid" `
                -TimeoutSec 2 `
                -ErrorAction Stop | Out-Null
        }
        catch {
            # The listener commonly closes before the HTTP response arrives.
        }
        if (Wait-LocalPortRelease -Port 48710 -TimeoutSeconds 3) {
            return $true
        }

        Start-Process `
            -FilePath "taskkill.exe" `
            -ArgumentList @("/PID", "$BackendPid", "/T", "/F") `
            -WindowStyle Hidden `
            -Wait | Out-Null
        return (Wait-LocalPortRelease -Port 48710 -TimeoutSeconds 5)
    }

    Write-Host "Detected the installed Dragon Nest ACT. Closing it before starting the development build..."
    $desktop = Get-Process -Id $desktopPid -ErrorAction SilentlyContinue
    if (-not $desktop -or -not $desktop.CloseMainWindow()) {
        throw "The installed Dragon Nest ACT is using port 48710. Close it and run the development launcher again."
    }
    if (Wait-LocalPortRelease -Port 48710 -TimeoutSeconds 5) {
        return $true
    }

    # The desktop has already received WM_CLOSE and had five seconds to run
    # its build-save handshake. Some WebView/Tauri shutdown races can leave
    # that verified instance alive indefinitely. The development launcher is
    # an explicit request to replace it, so terminate only this validated
    # desktop process tree after the graceful path times out. This also
    # removes its owned DragonNestEngine listener atomically.
    $desktop = Get-Process -Id $desktopPid -ErrorAction SilentlyContinue
    if ($desktop) {
        Write-Host "The installed ACT did not finish closing; stopping its verified process tree..."
        Start-Process `
            -FilePath "taskkill.exe" `
            -ArgumentList @("/PID", "$desktopPid", "/T", "/F") `
            -WindowStyle Hidden `
            -Wait | Out-Null
    }
    else {
        try {
            Invoke-WebRequest `
                -UseBasicParsing `
                -Method Post `
                -Uri "http://127.0.0.1:48710/api/shutdown?pid=$BackendPid" `
                -TimeoutSec 2 `
                -ErrorAction Stop | Out-Null
        }
        catch {
            # The listener can disappear before PowerShell receives the HTTP
            # response; the port-release check below is the authority.
        }
    }
    if (Wait-LocalPortRelease -Port 48710 -TimeoutSeconds 5) {
        return $true
    }
    throw "The installed Dragon Nest ACT did not release port 48710. Close it and run the development launcher again."
}

Stop-StaleDevListener -Port 1420
Stop-StaleDevListener -Port 48710

function Resolve-PnpmPath {
    # A managed Node installation can expose pnpm.CMD without putting its
    # directory on the Windows PATH, so a launcher started from Explorer sees
    # no pnpm even though an interactive shell can run it. Probe the known
    # runtime locations before failing.
    $command = Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path `
            $env:USERPROFILE `
            ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"),
        (Join-Path $env:APPDATA "npm\pnpm.cmd"),
        (Join-Path $env:ProgramFiles "nodejs\pnpm.cmd")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    $managedVersions = Join-Path `
        $env:USERPROFILE `
        ".workbuddy\binaries\node\versions"
    if (Test-Path -LiteralPath $managedVersions) {
        $managed = Get-ChildItem `
            -LiteralPath $managedVersions `
            -Filter "pnpm.CMD" `
            -Recurse `
            -Depth 1 `
            -ErrorAction SilentlyContinue |
            Sort-Object -Property LastWriteTime -Descending |
            Select-Object -First 1
        if ($managed) {
            return $managed.FullName
        }
    }

    return ""
}

$pnpmPath = Resolve-PnpmPath
if (-not $pnpmPath) {
    throw (
        "pnpm was not found. Install Node.js/pnpm or prepare the bundled Codex runtime. " +
        "Checked PATH, %APPDATA%\npm, %ProgramFiles%\nodejs and " +
        "%USERPROFILE%\.workbuddy\binaries\node\versions."
    )
}
$pnpmDirectory = Split-Path -Parent $pnpmPath
$env:PATH = "$pnpmDirectory;$env:PATH"
$nodeCommand = Get-Command "node.exe" -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    $bundledNodeDirectory = [System.IO.Path]::GetFullPath(
        (Join-Path (Split-Path -Parent $pnpmPath) "..\..\node\bin")
    )
    $bundledNode = Join-Path $bundledNodeDirectory "node.exe"
    if (Test-Path -LiteralPath $bundledNode) {
        $env:PATH = "$bundledNodeDirectory;$env:PATH"
    }
    else {
        throw "node.exe was not found beside the bundled pnpm runtime."
    }
}
$cargoCommand = Get-Command "cargo.exe" -ErrorAction SilentlyContinue
if (-not $cargoCommand) {
    $cargoDirectory = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path -LiteralPath (Join-Path $cargoDirectory "cargo.exe")) {
        $env:PATH = "$cargoDirectory;$env:PATH"
        $cargoCommand = Get-Command "cargo.exe" -ErrorAction SilentlyContinue
    }
}
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw (
        "Python virtual environment not found at `.venv\Scripts\python.exe`. " +
        "Run `pipenv install` (and `pipenv install --dev`) in the project root first."
    )
}
$backendScript = (Resolve-Path -LiteralPath (Join-Path $root "v420_backend.py")).Path
$backend = Start-Process `
    -FilePath $venvPython `
    -ArgumentList @($backendScript, "--port", "48710") `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru

try {
    Set-Location $frontend
    if ($cargoCommand) {
        & $pnpmPath dev
    }
    else {
        Write-Host "cargo (Rust) not found; using browser-only dev mode."
        Write-Host "Frontend: http://127.0.0.1:1420   API: http://127.0.0.1:48710"
        Write-Host "To use the Tauri desktop shell, install Rust, then run install_tauri_build_tools.bat"
        & $pnpmPath dev:web
    }
}
finally {
    if (-not $backend.HasExited) {
        Start-Process `
            -FilePath "taskkill.exe" `
            -ArgumentList @("/PID", "$($backend.Id)", "/T", "/F") `
            -WindowStyle Hidden `
            -Wait | Out-Null
    }
}
