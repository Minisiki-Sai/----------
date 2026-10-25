# 开发模式启动器：不打包，直接跑源码。
#
#   后端 = .venv\Scripts\python.exe v420_backend.py  （脚本托管，日志可见可落盘）
#   前端 = tauri dev（Vite 1420 热更新）+ debug 版桌面壳
#
# 桌面壳在 debug 下会先杀掉外部后端再自己 spawn，所以这里设置
# DRAGON_NEST_DEV_EXTERNAL_BACKEND=1 让壳跳过 spawn（见 lib.rs）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File tools\dev_run.ps1
#   powershell -ExecutionPolicy Bypass -File tools\dev_run.ps1 -BackendOnly
#   powershell -ExecutionPolicy Bypass -File tools\dev_run.ps1 -NoWatch -InspectPort 9222

param(
    [switch]$BackendOnly,
    [switch]$NoWatch,
    [int]$BackendPort = 48710,
    [int]$VitePort = 1420,
    [int]$InspectPort = 0
)

$ErrorActionPreference = 'Stop'

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root       = Split-Path -Parent $PSScriptRoot
$frontend   = Join-Path $root 'frontend'
$devDir     = Join-Path $root '.dev'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$backendLog = Join-Path $devDir 'backend.log'
$shellLog   = Join-Path $devDir 'shell.log'

# 本机环境同时存在 HTTPS_PROXY 与 https_proxy：Windows 环境块允许，但 .NET 的
# 进程环境字典（Start-Process 会枚举它）不允许同名不同大小写，会直接抛
# ArgumentException。所以子进程一律用 System.Diagnostics.Process 原生继承环境块。
function Start-NativeProcess {
    param(
        [string]$FilePath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [switch]$RedirectStandardOutput,
        [string]$StdOutPath
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = $Arguments
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $false
    if ($RedirectStandardOutput -and $StdOutPath) {
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
    }
    return [System.Diagnostics.Process]::Start($psi)
}

# taskkill 失败是常态（进程已退出、子进程被父进程带走、拒绝访问），而
# PowerShell 5.1 在 ErrorActionPreference='Stop' 下会把原生命令写往 stderr 的
# 内容当成终止性错误，导致整个脚本直接中断。所以这里统一收敛：
#   1) 只用 2>$null 丢弃 stderr，绝不用 2>&1 合并进成功流
#   2) 局部降级 ErrorActionPreference，失败一律回退 Stop-Process
#   3) 任何路径都不抛出，清理失败不能让启动器停摆
function Stop-ProcessTreeById {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch {
        try { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue } catch { }
    } finally {
        $ErrorActionPreference = $saved
    }
}

function Test-ProcessAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Stop-NativeProcessTree {
    param($Proc)
    if (-not $Proc) { return }
    try { $Proc.Refresh() } catch { }
    if ($Proc.HasExited) { return }
    Stop-ProcessTreeById -ProcessId $Proc.Id
}

function Write-Step { param([string]$Text) Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Note { param([string]$Text) Write-Host "   $Text" -ForegroundColor DarkGray }
function Write-Warn { param([string]$Text) Write-Host "!! $Text" -ForegroundColor Yellow }

function Resolve-PnpmPath {
    $cmd = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:APPDATA 'npm\pnpm.cmd'),
        (Join-Path $env:ProgramFiles 'nodejs\pnpm.cmd')
    )
    $candidates += @(Get-ChildItem -Path (Join-Path $env:USERPROFILE '.workbuddy\binaries\node\versions') -Filter 'pnpm.CMD' -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) { return $path }
    }
    return $null
}

function Set-MsvcEnvironment {
    $cmdPath = Join-Path $root 'tools\build_tauri_nobundle.cmd'
    if (-not (Test-Path -LiteralPath $cmdPath)) { throw "缺少 $cmdPath（需要里面的 MSVC/SDK 路径）" }
    $text = Get-Content -LiteralPath $cmdPath -Raw
    $msvc = [regex]::Match($text, 'set\s+"MSVC_ROOT=([^"]*)"').Groups[1].Value
    $sdk  = [regex]::Match($text, 'set\s+"SDK_ROOT=([^"]*)"').Groups[1].Value
    $sdkv = [regex]::Match($text, 'set\s+"SDK_VER=([^"]*)"').Groups[1].Value
    $link = Join-Path $msvc 'bin\Hostx64\x64\link.exe'
    if (-not (Test-Path -LiteralPath $link)) { throw "找不到 MSVC 链接器：$link" }
    if (-not (Test-Path -LiteralPath (Join-Path $sdk "Lib\$sdkv\ucrt\x64"))) { throw "找不到 Windows SDK $sdkv" }
    $env:PATH = "$env:USERPROFILE\.cargo\bin;$msvc\bin\Hostx64\x64;$sdk\bin\$sdkv\x64;$env:PATH"
    $env:LIB = "$msvc\lib\x64;$sdk\Lib\$sdkv\ucrt\x64;$sdk\Lib\$sdkv\um\x64"
    $env:INCLUDE = "$msvc\include;$sdk\Include\$sdkv\ucrt;$sdk\Include\$sdkv\um;$sdk\Include\$sdkv\shared;$sdk\Include\$sdkv\winrt"
    $env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = $link
    $env:CARGO_HTTP_MULTIPLEXING = 'false'
    Write-Note "MSVC $msvc"
    Write-Note "SDK  $sdkv"
}

function Stop-PortListener {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { return $true }
    foreach ($owner in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        Write-Warn "端口 $Port 被 PID $owner 占用，尝试结束…"
        Stop-ProcessTreeById -ProcessId $owner
    }
    if (Wait-PortFree -Port $Port -TimeoutSeconds 4) { return $true }
    Write-Warn "端口 $Port 仍被占用（占用方可能以管理员权限运行，未提权的脚本无法结束它）"
    return $false
}

function Assert-PortFree {
    param([int]$Port, [string]$What)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { return }
    $owners = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
    $names = foreach ($owner in $owners) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
        if ($proc) { "$($proc.Name)(PID $owner)" } else { "(PID $owner)" }
    }
    Write-Host ''
    Write-Host "== 启动中止：端口 $Port（$What）被占用：$($names -join ', ')" -ForegroundColor Red
    Write-Host '   最常见原因是之前启动的便携版（dragon-nest-simulator-v420.exe）还在以管理员权限运行，' -ForegroundColor Red
    Write-Host '   未提权的 PowerShell 无法结束它。请任选一种处理后重试：' -ForegroundColor Red
    Write-Host '     1) 手动关闭托盘里的「DN 倒计时工具」，或在任务管理器结束 dragon-nest-simulator-v420.exe / DragonNestEngine.exe' -ForegroundColor Red
    Write-Host '     2) 以管理员身份运行本脚本：右键 PowerShell → 以管理员身份运行，再执行同一条命令' -ForegroundColor Red
    Write-Host ''
    exit 1
}

function Stop-NamedProcess {
    param([string]$Name, [string]$PathPrefix)
    $procs = Get-CimInstance Win32_Process -Filter ("Name='" + $Name + "'") -ErrorAction SilentlyContinue
    # 先结束子进程再结束父进程：/T 已经会带树，但父进程先死会让子进程变成孤儿，
    # 之后再按 PID 清理就会报 "could not be terminated"。按父子深度倒序更稳。
    $rows = @($procs | Where-Object {
        -not ($PathPrefix -and $_.ExecutablePath -and -not $_.ExecutablePath.StartsWith($PathPrefix, 'OrdinalIgnoreCase'))
    })
    if (-not $rows.Count) { return }
    $parentIds = @{}
    foreach ($row in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($row.ProcessId) { $parentIds[[int]$row.ProcessId] = [int]$row.ParentProcessId }
    }
    $depth = @{}
    foreach ($row in $rows) {
        $target = [int]$row.ProcessId
        $level = 0
        $cursor = $target
        while ($level -lt 32 -and $parentIds.ContainsKey($cursor)) {
            $next = [int]$parentIds[$cursor]
            if ($next -le 0 -or $next -eq $cursor) { break }
            $cursor = $next
            $level++
        }
        $depth[$target] = $level
    }
    foreach ($row in ($rows | Sort-Object { -1 * $depth[[int]$_.ProcessId] })) {
        $target = [int]$row.ProcessId
        Write-Warn "结束残留进程 $Name (PID $target)"
        Stop-ProcessTreeById -ProcessId $target
        if (Test-ProcessAlive -ProcessId $target) {
            Write-Warn "PID $target 未被结束：它很可能是以管理员权限启动的（便携版常见），当前 PowerShell 未提权，杀不掉。"
            Write-Warn "  → 请手动关闭它，或用「以管理员身份运行」重开本脚本。"
        }
    }
}

function Read-BackendPid {
    $pidFile = Join-Path $root '.v420_backend_pid'
    if (-not (Test-Path -LiteralPath $pidFile)) { return 0 }
    try { return [int]((Get-Content -LiteralPath $pidFile -Raw).Trim()) } catch { return 0 }
}

function Wait-PortFree {
    param([int]$Port, [int]$TimeoutSeconds = 5)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if (-not $listening) { return $true }
        Start-Sleep -Milliseconds 150
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Stop-BackendProcess {
    param($Proc)
    $target = Read-BackendPid
    if ($target -le 0 -and $Proc) { $target = $Proc.Id }
    if ($target -gt 0) {
        try {
            Invoke-WebRequest -UseBasicParsing -Method Post -TimeoutSec 2 `
                -Uri "http://127.0.0.1:$BackendPort/api/shutdown?pid=$target" | Out-Null
        } catch { }
    }
    if (Wait-PortFree -Port $BackendPort -TimeoutSeconds 4) { return }
    if ($Proc) {
        Stop-NativeProcessTree -Proc $Proc
    }
    Stop-PortListener -Port $BackendPort
    Wait-PortFree -Port $BackendPort -TimeoutSeconds 3 | Out-Null
}

function Start-Backend {
    if (Test-Path -LiteralPath $backendLog) {
        $stamp = Get-Date -Format 'HHmmss'
        Move-Item -LiteralPath $backendLog -Destination "$backendLog.$stamp" -Force
    }
    $before = Read-BackendPid
    $wrapper = Join-Path $devDir 'run_backend.ps1'
    $line = "& '$venvPython' -u '$root\v420_backend.py' --port $BackendPort 2>&1 | Tee-Object -FilePath '$backendLog'"
    Set-Content -LiteralPath $wrapper -Value ("`$ErrorActionPreference='Continue'`r`n" + $line) -Encoding UTF8
    $proc = Start-NativeProcess -FilePath 'powershell.exe' `
        -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + $wrapper + '"') `
        -WorkingDirectory $root
    $deadline = (Get-Date).AddSeconds(60)
    do {
        Start-Sleep -Milliseconds 250
        $proc.Refresh()
        if ($proc.HasExited) { throw "后端进程已退出（exit=$($proc.ExitCode)），见 $backendLog" }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 1 -ErrorAction Stop
        } catch { continue }
        if (-not $health.ok) { continue }
        $owner = Read-BackendPid
        if ($owner -gt 0 -and $owner -ne $before -and $health.ready -eq $true) { return $proc }
    } while ((Get-Date) -lt $deadline)
    Stop-BackendProcess -Proc $proc
    throw "后端 60s 内未就绪，见 $backendLog"
}

function Get-SourceStamp {
    $files = @(Get-ChildItem -LiteralPath (Join-Path $root 'dragon_nest_builder') -Recurse -File -Filter '*.py' -ErrorAction SilentlyContinue)
    foreach ($name in @('v420_backend.py', 'v420_runtime.py')) {
        $path = Join-Path $root $name
        if (Test-Path -LiteralPath $path) { $files += Get-Item -LiteralPath $path }
    }
    return (($files | Sort-Object FullName | ForEach-Object { "$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)|$($_.Length)" }) -join "`n")
}

# ---------------------------------------------------------------- preflight
Write-Step "开发模式启动器（不打包）"

if (-not (Test-Path -LiteralPath $venvPython)) { throw "缺少 Python 虚拟环境：$venvPython（先 python -m venv .venv 并装 requirements.txt）" }
if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules\@tauri-apps\cli'))) { throw "缺少前端依赖：先在 frontend 下执行 pnpm install" }

$pnpm = Resolve-PnpmPath
if (-not $pnpm) { throw '找不到 pnpm（安装 Node.js 后执行 npm i -g pnpm）' }
Write-Note "pnpm  $pnpm"

# pnpm.cmd 内部会调 node，如果 node 不在 PATH 上就会报
# '"node"' is not recognized（从 bundled node 目录里找到 pnpm 时最容易踩到）。
$pnpmDir = Split-Path -Parent $pnpm
if ($pnpmDir) {
    $env:PATH = "$pnpmDir;$env:PATH"
    $nodeExe = Join-Path $pnpmDir 'node.exe'
    if (Test-Path -LiteralPath $nodeExe) {
        Write-Note "node  $nodeExe"
    } else {
        Write-Warn "pnpm 同目录没有 node.exe：请确认 Node.js 已安装并在 PATH 中"
    }
}
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) { throw '找不到 node.exe（安装 Node.js 后可解决）' }

Set-MsvcEnvironment

if (-not (Test-Path -LiteralPath $devDir)) { New-Item -ItemType Directory -Path $devDir -Force | Out-Null }

# 单实例互斥体与正式版共享：先关掉已安装版，避免 dev 被弹窗拒绝。
Stop-NamedProcess -Name 'dncountdown.exe' -PathPrefix $null
Stop-NamedProcess -Name 'dragon-nest-simulator-v420.exe' -PathPrefix $null
Stop-NamedProcess -Name 'dn-countdown.exe' -PathPrefix $root
Stop-PortListener -Port $BackendPort | Out-Null
Stop-PortListener -Port $VitePort | Out-Null

# 清理失败本身不致命，但端口被占一定会让后端起不来。这里提前给出可执行的提示，
# 而不是让 Start-Backend 在后面抛一个看不懂的绑定失败。
Assert-PortFree -Port $BackendPort -What '后端'
Assert-PortFree -Port $VitePort -What 'Vite'

# ------------------------------------------------------------------- env
$env:DRAGON_NEST_DEV_EXTERNAL_BACKEND = '1'
# 调试记录（观测日志 + 保留抓包）在「设置 → 调试记录」里开关，配置持久化到
# <项目根>\data\debug_recording.json。下面这行只在配置文件还没写过开关时生效：
# $env:DRAGON_NEST_NEST_PROBE = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONPYCACHEPREFIX = Join-Path $devDir 'pycache'
$env:DRAGON_NEST_DESKTOP_PID = "$PID"
$env:DRAGON_NEST_APP_VERSION = '4.88'
if ($InspectPort -gt 0) {
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=$InspectPort"
    Write-Note "WebView2 CDP（尽力而为，部分机器上该环境变量不生效）：http://127.0.0.1:$InspectPort/json/list"
}

# ---------------------------------------------------------------- backend
Write-Step "启动后端（源码）"
$backend = Start-Backend
Write-Step "后端就绪（PID $(Read-BackendPid)），日志：$backendLog"

# ------------------------------------------------------------------ shell
Write-Step ($(if ($BackendOnly) { '启动 Vite（浏览器模式）' } else { '启动桌面壳（tauri dev，首次编译较久）' }))
if (-not $BackendOnly) { Write-Note '首次 debug 编译需要 4~10 分钟，之后 Rust 改动 30~90 秒、前端改动秒级' }
$shellScript = if ($BackendOnly) { 'dev:web' } else { 'dev' }
$shellWrapper = Join-Path $devDir 'run_shell.ps1'
$shellLine = "& '$pnpm' $shellScript 2>&1 | Tee-Object -FilePath '$shellLog'"
Set-Content -LiteralPath $shellWrapper -Value ("`$ErrorActionPreference='Continue'`r`n" + $shellLine) -Encoding UTF8
$shell = Start-NativeProcess -FilePath 'powershell.exe' `
    -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + $shellWrapper + '"') `
    -WorkingDirectory $frontend

Write-Step '已启动，Ctrl+C 退出'
if ($BackendOnly) {
    Write-Note "主界面：http://127.0.0.1:$VitePort/"
    Write-Note "悬浮窗：http://127.0.0.1:$VitePort/index.html?view=buff-overlay"
    Write-Note "首次需在控制台执行 localStorage.setItem('dragon-nest-v420-buff-overlay-enabled','1') 后刷新"
} else {
    Write-Note '改 .vue/.ts 会热更新；改 dragon_nest_builder/**.py 会自动重启后端（采集会话会中断，需重新点开始采集）'
}

$stamp = Get-SourceStamp
$pendingSince = $null
$restartTimes = New-Object System.Collections.ArrayList

try {
    while ($true) {
        $shell.Refresh()
        if ($shell.HasExited) { Write-Warn "前端进程已退出（exit=$($shell.ExitCode)）"; break }

        $backend.Refresh()
        if ($backend.HasExited) {
            Write-Warn '后端进程退出，尝试重启'
            $now = Get-Date
            while ($restartTimes.Count -gt 0 -and ($now - $restartTimes[0]).TotalSeconds -gt 60) { [void]$restartTimes.RemoveAt(0) }
            if ($restartTimes.Count -ge 5) { Write-Warn '1 分钟内已重启 5 次，停止自动重启'; break }
            [void]$restartTimes.Add($now)
            try { $backend = Start-Backend; Write-Host '[watch] 后端已恢复' -ForegroundColor Green } catch { Write-Warn $_.Exception.Message }
            $stamp = Get-SourceStamp
            continue
        }

        if (-not $NoWatch) {
            $next = Get-SourceStamp
            if ($next -ne $stamp) {
                $stamp = $next
                $pendingSince = Get-Date
            } elseif ($pendingSince -and ((Get-Date) - $pendingSince).TotalMilliseconds -ge 500) {
                $pendingSince = $null
                Write-Host '[watch] 源码变更，重启后端...' -ForegroundColor Cyan
                Stop-BackendProcess -Proc $backend
                try {
                    $backend = Start-Backend
                    Write-Host ('[watch] 后端已就绪（PID ' + (Read-BackendPid) + '）') -ForegroundColor Green
                } catch { Write-Warn $_.Exception.Message }
                $stamp = Get-SourceStamp
                continue
            }
        }

        Start-Sleep -Milliseconds 250
    }
}
finally {
    Write-Step '清理中'
    if ($backend) { Stop-BackendProcess -Proc $backend }
    if ($shell) { Stop-NativeProcessTree -Proc $shell }
    Stop-NamedProcess -Name 'dn-countdown.exe' -PathPrefix $root
    Stop-PortListener -Port $BackendPort
    Stop-PortListener -Port $VitePort
    Write-Step '已退出'
}
