param(
    [string]$ReleaseDir = '',
    [string]$OutDir = '',
    [string]$Version = '',
    [switch]$KeepEnginePackage,
    [switch]$SkipEngineVerification
)

# Windows PowerShell 5.1: keep this file ASCII-only except the message text,
# which is written with double-quoted strings and simple concatenation.
# Assembles a portable (no-install) folder from an existing Tauri release build.
# It never touches Program Files, the registry or user documents.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ReleaseDir) { $ReleaseDir = Join-Path $root 'frontend\src-tauri\target\release' }

function Stop-WithHelp([string]$Message, [string[]]$Hints) {
    Write-Output ''
    Write-Output ('[portable] 打包中止：' + $Message)
    foreach ($hint in $Hints) { Write-Output ('  - ' + $hint) }
    Write-Output ''
    exit 2
}

# --- build-artifact preflight ---------------------------------------------
if (-not (Test-Path -LiteralPath $ReleaseDir)) {
    Stop-WithHelp ('找不到桌面程序构建产物：' + $ReleaseDir) @(
        '这个脚本只把已构建好的程序组装成绿色目录，不会自己编译。',
        '请先运行 build_v488_tauri.bat（需要 Rust 与 Visual Studio 的 C++ 生成工具）。',
        '首次准备环境：以管理员身份运行 install_tauri_build_tools.bat，然后安装 rustup（https://rustup.rs）。',
        '构建成功后程序会输出到 frontend\src-tauri\target\release。',
        '只想在本机开发调试的话，直接用 启动V4.68开发版.bat，不需要打包。'
    )
}
$release = (Resolve-Path -LiteralPath $ReleaseDir).Path

if (-not $Version) {
    $cargo = Join-Path $root 'frontend\src-tauri\Cargo.toml'
    $match = Select-String -LiteralPath $cargo -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) { throw "Could not read the desktop version from $cargo" }
    $Version = $match.Matches[0].Groups[1].Value
}
$displayVersion = $Version
if (-not $OutDir) {
    $OutDir = Join-Path $root ('release-portable\DNACT' + ($Version -replace '\.', '') + '-portable')
}

function Write-Step([string]$Message) { Write-Output ("[portable] " + $Message) }
function Get-TreeSize([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    return (Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
}

# --- payload preflight -----------------------------------------------------
$manifestPath = Join-Path $release 'runtime\install-manifest.json'
$archivePath = Join-Path $release 'runtime\engine-package.zip'
$verifier = Join-Path $root 'tools\verify_install_manifest.ps1'
$engineInstaller = Join-Path $root 'tools\install_engine_release.ps1'
$stopHelper = Join-Path $root 'tools\stop_installed_backend.ps1'
$readme = Join-Path $root 'docs\portable-readme.txt'

$required = @($manifestPath, $verifier, $engineInstaller, $stopHelper)
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count) {
    Stop-WithHelp ('缺少打包辅助脚本：' + ($missing -join ', ')) @(
        '这些脚本随仓库提供，请确认没有被安全软件隔离或误删。'
    )
}

$mainExe = Get-ChildItem -LiteralPath $release -Filter *.exe -File |
    Where-Object { $_.Name -notmatch 'DragonNestEngine' } |
    Sort-Object Length -Descending | Select-Object -First 1
if (-not $mainExe) {
    Stop-WithHelp ('build 目录里没有桌面主程序：' + $release) @(
        '请确认 build_v488_tauri.bat 已完整跑完（成功时会生成 龙之谷ACT...exe）。',
        '如果构建中途失败，先看它的最后 20 行错误输出。'
    )
}
if (-not (Test-Path -LiteralPath $archivePath)) {
    Stop-WithHelp ('缺少引擎包：' + $archivePath) @(
        '引擎由 PyInstaller 生成，请先用 build_v488_tauri.bat 完整构建一次。',
        '只跑前端 vite build 不会产出引擎包。'
    )
}
$thirdParty = Join-Path $release 'third_party'
if (-not (Test-Path -LiteralPath $thirdParty)) {
    Stop-WithHelp ('缺少 third_party（Npcap 安装包 / 技能 Hit 工具）：' + $thirdParty) @(
        '构建脚本会把它镜像到 release 目录，请重跑 build_v488_tauri.bat。',
        '若 third_party\npcap 下没有 npcap-1.88.exe，安装清单校验会失败，需要先补齐该文件。'
    )
}

# --- output folder ---------------------------------------------------------
$outFull = [IO.Path]::GetFullPath($OutDir).TrimEnd('\')
if ($outFull -eq [IO.Path]::GetPathRoot($outFull).TrimEnd('\')) { throw 'A concrete output folder is required.' }
if (Test-Path -LiteralPath $outFull) {
    if (-not $outFull.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean an output folder outside the project: $outFull"
    }
    Write-Step "Cleaning previous bundle: $outFull"
    Remove-Item -LiteralPath $outFull -Recurse -Force
}
New-Item -ItemType Directory -Path $outFull -Force | Out-Null

# --- files -----------------------------------------------------------------
Write-Step ("Copying " + $mainExe.Name)
Copy-Item -LiteralPath $mainExe.FullName -Destination (Join-Path $outFull $mainExe.Name) -Force
Get-ChildItem -LiteralPath $release -Filter *.dll -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $outFull $_.Name) -Force
}

Write-Step 'Copying runtime payload (manifest, verifier, engine archive)'
$runtimeOut = Join-Path $outFull 'runtime'
New-Item -ItemType Directory -Path $runtimeOut -Force | Out-Null
Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $runtimeOut 'install-manifest.json') -Force
Copy-Item -LiteralPath $archivePath -Destination (Join-Path $runtimeOut 'engine-package.zip') -Force
Copy-Item -LiteralPath $verifier -Destination (Join-Path $runtimeOut 'verify-install.ps1') -Force
Copy-Item -LiteralPath $stopHelper -Destination (Join-Path $runtimeOut 'stop-backend.ps1') -Force

Write-Step 'Copying third_party payload (Npcap installer, skill-hit tool)'
Copy-Item -LiteralPath $thirdParty -Destination (Join-Path $outFull 'third_party') -Recurse -Force

# --- activate the engine exactly like the installer does -------------------
if ($SkipEngineVerification) {
    Write-Step 'Skipping engine activation (-SkipEngineVerification)'
} else {
    Write-Step ("Activating engine release for version " + $displayVersion)
    $arguments = @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $engineInstaller + '"'),
        '-InstallRoot', ('"' + $outFull + '"'),
        '-ManifestPath', ('"' + (Join-Path $runtimeOut 'install-manifest.json') + '"'),
        '-VerifierPath', ('"' + (Join-Path $runtimeOut 'verify-install.ps1') + '"'),
        '-StopHelperPath', ('"' + (Join-Path $runtimeOut 'stop-backend.ps1') + '"'),
        '-ExpectedVersion', $displayVersion
    )
    $process = Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList $arguments -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -ne 0) {
        Stop-WithHelp '引擎校验或激活失败，绿色目录未完成。' @(
            '常见原因：third_party\npcap\npcap-1.88.exe 缺失或被安全软件隔离。',
            '也可能是安装清单与实际文件不匹配（构建产物被改动过）。',
            '详细原因见上方的校验输出；修好后重跑本脚本即可。'
        )
    }
    $active = Join-Path $runtimeOut 'active-engine.json'
    if (-not (Test-Path -LiteralPath $active)) { throw 'active-engine.json was not published by the activation step.' }
    $selection = Get-Content -LiteralPath $active -Raw -Encoding UTF8 | ConvertFrom-Json
    $engineExe = Join-Path $runtimeOut ((($selection.directory) -replace '/', '\') + '\DragonNestEngine.exe')
    if (-not (Test-Path -LiteralPath $engineExe)) { throw "Activated engine executable is missing: $engineExe" }
    if (-not $KeepEnginePackage) {
        Write-Step 'Removing the consumed engine archive (use -KeepEnginePackage to keep it)'
        Remove-Item -LiteralPath (Join-Path $runtimeOut 'engine-package.zip') -Force
    }
}

# --- portable launcher / readme -------------------------------------------
$launcher = @'
@echo off
setlocal EnableExtensions
cd /d "%~dp0"
for %%F in (*.exe) do (
    if /I not "%%~nF"=="DragonNestEngine" (
        start "" "%%~fF"
        exit /b 0
    )
)
echo Desktop executable was not found next to this launcher.
pause
exit /b 1
'@
$launcherPath = Join-Path $outFull 'Start-Portable.bat'
[IO.File]::WriteAllText($launcherPath, ($launcher -replace "`r?`n", "`r`n"), (New-Object Text.UTF8Encoding($false)))

if (Test-Path -LiteralPath $readme) {
    Copy-Item -LiteralPath $readme -Destination (Join-Path $outFull 'README-Portable.txt') -Force
}

# --- report ---------------------------------------------------------------
$size = Get-TreeSize $outFull
$engineSize = Get-TreeSize (Join-Path $runtimeOut 'engines')
Write-Step ("Bundle: " + $outFull)
Write-Step ("Executable: " + $mainExe.Name)
Write-Step ("Total size: {0:N1} MB (engine {1:N1} MB)" -f ($size / 1MB), ($engineSize / 1MB))
Write-Step 'Portable folder is ready. Npcap still has to be installed once before packet capture works.'
