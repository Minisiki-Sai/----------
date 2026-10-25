param(
    [string]$Version = '',
    [string]$OutDir = '',
    [string]$EngineDist = '',
    [Alias('SkipBuild')][switch]$SkipShell,
    [switch]$SkipEngine,
    [switch]$NoZip,
    [switch]$FreshEngine
)

# Complete one-shot build of the no-install portable bundle:
#   1. PyInstaller engine            -> outputs\portable\engine_dist\DragonNestEngine
#   2. Tauri desktop shell, no NSIS  -> frontend\src-tauri\target\release\*.exe
#   3. portable layout               -> <OutDir>\Start-Portable.bat + runtime\engines\<id>
#   4. optional zip                  -> <OutDir>.zip
# Nothing is written to the registry or Program Files.
#
# Usage (from the repo root):
#   打包便携版.bat                          full build
#   打包便携版.bat -SkipEngine              reuse the existing frozen engine
#   打包便携版.bat -SkipShell               reuse the existing desktop executable
#   打包便携版.bat -SkipEngine -SkipShell    only reassemble the folder
#   打包便携版.bat -NoZip                   skip the zip step
#   打包便携版.bat -FreshEngine             pass --clean to PyInstaller
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $root
$env:PATH = (Join-Path $env:USERPROFILE '.cargo\bin') + ';' + $env:PATH

function Write-Step([string]$Message) { Write-Output ('[portable] ' + $Message) }
function Write-Note([string]$Message) { Write-Output ('[portable] - ' + $Message) }
function Format-Elapsed($Span) { return $Span.ToString('mm\:ss') }
function Stop-WithHelp {
    param([string]$Message, [string[]]$Hints)
    Write-Output ''
    Write-Output ('[portable] 构建中止：' + $Message)
    foreach ($hint in $Hints) { Write-Output ('  - ' + $hint) }
    Write-Output ''
    exit 2
}

$total = [Diagnostics.Stopwatch]::StartNew()

# --- resolve paths and version --------------------------------------------
$python = Join-Path $root '.venv\Scripts\python.exe'
$specFile = Join-Path $root 'DragonNestEngine_V420.spec'
if (-not $EngineDist) { $EngineDist = Join-Path $root 'outputs\portable\engine_dist\DragonNestEngine' }
$engineWork = Join-Path $root 'outputs\portable\engine_work'
if (-not $Version) {
    $cargoFile = Join-Path $root 'frontend\src-tauri\Cargo.toml'
    $match = Select-String -LiteralPath $cargoFile -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) { throw "Cannot read the desktop version from $cargoFile" }
    $Version = $match.Matches[0].Groups[1].Value
}
if (-not $OutDir) {
    # 构建产物放在项目**同级**的 dnact-release\，不要写回源码树。
    # 放在仓库里每次打包都会整目录重建（旧引擎删、新引擎写），
    # 一次就是几百条 git 变更，把真正的代码改动埋掉。
    $releaseParent = Split-Path -Parent $root
    $OutDir = Join-Path $releaseParent 'dnact-release\DN倒计时工具'
}
Write-Output ''
Write-Step ('Version ' + $Version)
Write-Step ('Project ' + $root)
Write-Output ''

# --- 0. preflight ----------------------------------------------------------
$problems = New-Object System.Collections.ArrayList

if (-not $SkipEngine) {
    if (-not (Test-Path -LiteralPath $python)) {
        [void]$problems.Add('缺少 Python 虚拟环境：' + $python + '（先执行 python -m venv .venv 并安装 requirements.txt）')
    } elseif (-not (Test-Path -LiteralPath $specFile)) {
        [void]$problems.Add('缺少 PyInstaller 配置：' + $specFile)
    } else {
        $probe = & $python -c "import PyInstaller,sys;sys.stdout.write(PyInstaller.__version__)" 2>&1
        if ($LASTEXITCODE -ne 0) {
            [void]$problems.Add('虚拟环境里没有 PyInstaller（执行 .venv\Scripts\python.exe -m pip install pyinstaller）')
        } else {
            Write-Note ('PyInstaller ' + $probe)
        }
    }
}

if (-not $SkipShell) {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        [void]$problems.Add('找不到 cargo（安装 rustup 后重开终端）')
    } else {
        Write-Note ((& cargo --version 2>&1 | Select-Object -First 1))
    }

    $sdkCmd = Join-Path $root 'tools\build_tauri_nobundle.cmd'
    $msvcRoot = 'E:\vs_build_tools\VC\Tools\MSVC\14.50.35717'
    $sdkRoot = 'C:\Program Files (x86)\Windows Kits\10'
    $sdkVer = '10.0.26100.0'
    if (Test-Path -LiteralPath $sdkCmd) {
        $cmdText = Get-Content -LiteralPath $sdkCmd -Raw
        $m1 = [regex]::Match($cmdText, 'set\s+"MSVC_ROOT=([^"]*)"')
        $m2 = [regex]::Match($cmdText, 'set\s+"SDK_ROOT=([^"]*)"')
        $m3 = [regex]::Match($cmdText, 'set\s+"SDK_VER=([^"]*)"')
        if ($m1.Success) { $msvcRoot = $m1.Groups[1].Value }
        if ($m2.Success) { $sdkRoot = $m2.Groups[1].Value }
        if ($m3.Success) { $sdkVer = $m3.Groups[1].Value }
    }
    $linkExe = Join-Path $msvcRoot 'bin\Hostx64\x64\link.exe'
    if (-not (Test-Path -LiteralPath $linkExe)) {
        [void]$problems.Add('找不到 MSVC 链接器：' + $linkExe + '（改 tools\build_tauri_nobundle.cmd 里的 MSVC_ROOT）')
    } else {
        Write-Note ('MSVC ' + $msvcRoot)
    }
    $ucrtLib = Join-Path $sdkRoot ('Lib\' + $sdkVer + '\ucrt\x64')
    if (-not (Test-Path -LiteralPath $ucrtLib)) {
        [void]$problems.Add('找不到 Windows SDK ' + $sdkVer + '：' + $ucrtLib + '（改 SDK_VER 或安装对应 SDK）')
    } else {
        Write-Note ('Windows SDK ' + $sdkVer)
    }

    $pnpm = $null
    $pnpmCmd = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($pnpmCmd) { $pnpm = $pnpmCmd.Source }
    if (-not $pnpm -and (Test-Path -LiteralPath (Join-Path $env:APPDATA 'npm\pnpm.cmd'))) { $pnpm = Join-Path $env:APPDATA 'npm\pnpm.cmd' }
    if (-not $pnpm -and (Test-Path -LiteralPath (Join-Path $env:ProgramFiles 'nodejs\pnpm.cmd'))) { $pnpm = Join-Path $env:ProgramFiles 'nodejs\pnpm.cmd' }
    if (-not $pnpm) { [void]$problems.Add('找不到 pnpm（安装 Node.js 后执行 npm i -g pnpm）') }
    else { Write-Note ('pnpm ' + $pnpm) }
}

if ($problems.Count -gt 0) {
    Stop-WithHelp '构建环境不完整。' $problems.ToArray()
}

# --- 1. engine -------------------------------------------------------------
$engineExe = Join-Path $EngineDist 'DragonNestEngine.exe'
if ($SkipEngine) {
    if (-not (Test-Path -LiteralPath $engineExe)) {
        Stop-WithHelp ('-SkipEngine 已指定，但找不到已冻结的引擎：' + $engineExe) @(
            '去掉 -SkipEngine 让脚本自己构建，或先手动跑 PyInstaller。'
        )
    }
    Write-Step ('Step 1/4 skipped, engine: ' + $EngineDist)
} else {
    if (Test-Path -LiteralPath $EngineDist) {
        Write-Step 'Cleaning the previous engine output...'
        & cmd /c ('rmdir /s /q "' + $EngineDist + '"')
        if (Test-Path -LiteralPath $EngineDist) {
            Stop-WithHelp ('无法清理旧的引擎输出：' + $EngineDist) @(
                '多半是仍有进程占用其中的文件（引擎还在跑？）或权限不足。'
            )
        }
    }
    Write-Step 'Step 1/4 freezing the Python engine (PyInstaller, this takes a few minutes)...'
    Write-Note 'PyInstaller 的 conda-meta WARNING 属于环境探测提示，可以忽略。'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $pyArgs = @(
        'tools\run_pyinstaller.py',
        '--noconfirm',
        '--distpath', (Join-Path $root 'outputs\portable\engine_dist'),
        '--workpath', $engineWork
    )
    if ($FreshEngine) { $pyArgs += '--clean' }
    $pyArgs += $specFile
    & $python @pyArgs
    if ($LASTEXITCODE -ne 0) {
        Stop-WithHelp ('PyInstaller 构建失败，退出码 ' + $LASTEXITCODE) @(
            '看上面 PyInstaller 的 ERROR 行，通常是缺少依赖或 spec 里的数据目录不存在。',
            '完整告警清单：outputs\portable\engine_work\DragonNestEngine_V420\warn-DragonNestEngine_V420.txt'
        )
    }
    if (-not (Test-Path -LiteralPath $engineExe)) {
        Stop-WithHelp ('PyInstaller 结束了，但没有产出 ' + $engineExe) @(
            '检查 spec 里的 COLLECT name 是否仍为 DragonNestEngine。'
        )
    }
    $sw.Stop()
    Write-Step ('Step 1/4 done in ' + (Format-Elapsed $sw.Elapsed))
}

# --- 2. desktop shell ------------------------------------------------------
$releaseDir = Join-Path $root 'frontend\src-tauri\target\release'
if ($SkipShell) {
    Write-Step 'Step 2/4 skipped, reusing the existing desktop executable.'
} else {
    Write-Step 'Step 2/4 building the desktop shell (vite + cargo, no installer bundle)...'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & (Join-Path $root 'tools\build_tauri_nobundle.cmd')
    if ($LASTEXITCODE -ne 0) {
        Stop-WithHelp ('桌面程序构建失败，退出码 ' + $LASTEXITCODE) @(
            '常见原因：Rust 工具链缺失、Visual C++ 生成工具版本变了、crates 下载失败、TypeScript 类型检查没通过。',
            '单独跑 tools\build_tauri_nobundle.cmd 可以看到完整日志。'
        )
    }
    $sw.Stop()
    Write-Step ('Step 2/4 done in ' + (Format-Elapsed $sw.Elapsed))
}

# Prefer the executable named after the Cargo package: target\release keeps
# stale binaries from earlier product names, and picking "the biggest exe"
# silently shipped the previous build.
$cargoPackage = (Select-String -LiteralPath (Join-Path $root 'frontend\src-tauri\Cargo.toml') -Pattern '^name\s*=\s*"([^"]+)"' | Select-Object -First 1).Matches[0].Groups[1].Value
$mainExe = Get-Item -LiteralPath (Join-Path $releaseDir ($cargoPackage + '.exe')) -ErrorAction SilentlyContinue
if (-not $mainExe) {
    $mainExe = Get-ChildItem -LiteralPath $releaseDir -Filter *.exe -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notmatch 'DragonNestEngine' } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $mainExe) {
    Stop-WithHelp ('没有找到桌面主程序：' + $releaseDir) @(
        '确认 tools\build_tauri_nobundle.cmd 已成功结束，且 target\release 下有 exe。'
    )
}
Write-Step ('Desktop: ' + $mainExe.Name + ' (' + [math]::Round($mainExe.Length / 1MB, 1) + ' MB)')

# --- 3. portable layout ----------------------------------------------------
$outFull = [IO.Path]::GetFullPath($OutDir).TrimEnd('\')
if ($outFull -eq [IO.Path]::GetPathRoot($outFull).TrimEnd('\')) { throw 'A concrete output folder is required.' }
if (Test-Path -LiteralPath $outFull) {
    # 只允许清理两个位置：项目自己、以及项目同级的 dnact-release\。
    # 产物放项目外是为了不污染源码仓库，但“清理”这个动作仍然不能
    # 落到任意目录上 —— 万一 OutDir 传错，会连别人的东西一起删。
    $releaseRoot = [IO.Path]::GetFullPath(
        (Join-Path (Split-Path -Parent $root) 'dnact-release')
    ).TrimEnd('\')
    $insideProject = $outFull.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)
    $insideRelease = $outFull.Equals($releaseRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $outFull.StartsWith($releaseRoot + '\', [StringComparison]::OrdinalIgnoreCase)
    if (-not ($insideProject -or $insideRelease)) {
        throw "Refusing to clean an output folder outside the project: $outFull"
    }
    $holders = @()
    foreach ($proc in (Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if ($proc.Path -and $proc.Path.StartsWith($outFull, [StringComparison]::OrdinalIgnoreCase)) {
                $holders += ($proc.ProcessName + '(' + $proc.Id + ')')
            }
        } catch {
        }
    }
    if ($holders.Count -gt 0) {
        Stop-WithHelp ('旧的绿色版目录正被占用，无法清理：' + $outFull) @(
            '先关闭正在运行的绿色版程序，再重新打包。',
            '占用中的进程：' + (($holders | Select-Object -Unique) -join ', ')
        )
    }
    Write-Step 'Step 3/4 cleaning the previous bundle...'
    try {
        Remove-Item -LiteralPath $outFull -Recurse -Force
    } catch {
        $suspects = @()
        foreach ($name in @('dn-countdown', 'dragon-nest-simulator-v420', 'DragonNestEngine')) {
            foreach ($proc in (Get-Process -Name $name -ErrorAction SilentlyContinue)) {
                $suspects += ($name + '(' + $proc.Id + ')')
            }
        }
        $hints = @('多半是仍有进程占用其中的文件。关闭绿色版程序后重试。')
        if ($suspects.Count -gt 0) {
            $hints += ('检测到这些进程仍在运行：' + (($suspects | Select-Object -Unique) -join ', ') + ' —— 请先结束它们。')
        } else {
            $hints += '也可以在任务管理器里结束 dn-countdown.exe 与 DragonNestEngine.exe。'
        }
        Stop-WithHelp ('清理旧输出目录失败：' + $_.Exception.Message) $hints
    }
}
New-Item -ItemType Directory -Path $outFull -Force | Out-Null

Copy-Item -LiteralPath $mainExe.FullName -Destination (Join-Path $outFull $mainExe.Name) -Force
Get-ChildItem -LiteralPath $releaseDir -Filter *.dll -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $outFull $_.Name) -Force
}

$runtimeOut = Join-Path $outFull 'runtime'
$releaseId = ([guid]::NewGuid().ToString('N')).Substring(0, 12)
$engineOut = Join-Path $runtimeOut ('engines\' + $releaseId)
New-Item -ItemType Directory -Path $engineOut -Force | Out-Null
Write-Step ('Copying engine to runtime\engines\' + $releaseId)
Copy-Item -Path (Join-Path $EngineDist '*') -Destination $engineOut -Recurse -Force

$selection = [ordered]@{
    schemaVersion = 1
    verified      = $true
    appVersion    = $Version
    directory     = 'engines/' + $releaseId
}
$json = ($selection | ConvertTo-Json -Compress)
[IO.File]::WriteAllText((Join-Path $engineOut 'engine-release.json'), $json, (New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText((Join-Path $runtimeOut 'active-engine.json'), $json, (New-Object Text.UTF8Encoding($false)))

$npcap = Join-Path $root 'third_party\npcap\npcap-1.88.exe'
$hitTool = Join-Path $root 'third_party\skill_hit_tool'
if ((Test-Path -LiteralPath $npcap) -or (Test-Path -LiteralPath $hitTool)) {
    $thirdOut = Join-Path $outFull 'third_party'
    if (Test-Path -LiteralPath $npcap) {
        New-Item -ItemType Directory -Path (Join-Path $thirdOut 'npcap') -Force | Out-Null
        Copy-Item -LiteralPath $npcap -Destination (Join-Path $thirdOut 'npcap\npcap-1.88.exe') -Force
    }
    if (Test-Path -LiteralPath $hitTool) {
        Copy-Item -LiteralPath $hitTool -Destination (Join-Path $thirdOut 'skill_hit_tool') -Recurse -Force
    }
    Write-Step 'third_party payload copied'
} else {
    Write-Note 'third_party payload not present; the bundle ships without the Npcap installer.'
}
if (-not (Test-Path -LiteralPath $npcap)) {
    Write-Note '缺少 third_party\npcap\npcap-1.88.exe，用户需要自行准备抓包驱动。'
}
if (-not (Test-Path -LiteralPath $hitTool)) {
    Write-Note '缺少 third_party\skill_hit_tool，引擎自检 --verify-runtime 会报缺组件（不影响抓包与倒计时）。'
}

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
[IO.File]::WriteAllText((Join-Path $outFull 'Start-Portable.bat'),
    ($launcher -replace "`r?`n", "`r`n"), (New-Object Text.UTF8Encoding($false)))

$readme = Join-Path $root 'docs\portable-readme.txt'
if (Test-Path -LiteralPath $readme) {
    Copy-Item -LiteralPath $readme -Destination (Join-Path $outFull 'README-Portable.txt') -Force
} else {
    Write-Note '缺少 docs\portable-readme.txt，包内不会附带使用说明。'
}

# --- 4. zip ----------------------------------------------------------------
$zipPath = $outFull + '.zip'
if ($NoZip) {
    Write-Step 'Step 4/4 skipped, zip not requested.'
} else {
    Write-Step 'Step 4/4 compressing the bundle (a minute or two)...'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    Compress-Archive -Path $outFull -DestinationPath $zipPath -CompressionLevel Optimal
    $sw.Stop()
    Write-Step ('Step 4/4 done in ' + (Format-Elapsed $sw.Elapsed))
}

# --- report ----------------------------------------------------------------
$total.Stop()
$size = (Get-ChildItem -LiteralPath $outFull -Recurse -File | Measure-Object -Property Length -Sum).Sum
$engineSize = (Get-ChildItem -LiteralPath $engineOut -Recurse -File | Measure-Object -Property Length -Sum).Sum
$fileCount = (Get-ChildItem -LiteralPath $outFull -Recurse -File).Count

Write-Output ''
Write-Output '[portable] ================ build finished ================'
Write-Output ('[portable] Bundle   : ' + $outFull)
Write-Output ('[portable] Files    : ' + $fileCount + ' files, ' + ('{0:N1}' -f ($size / 1MB)) + ' MB (engine ' + ('{0:N1}' -f ($engineSize / 1MB)) + ' MB)')
if (-not $NoZip -and (Test-Path -LiteralPath $zipPath)) {
    Write-Output ('[portable] Zip      : ' + $zipPath + ' (' + ('{0:N1}' -f ((Get-Item -LiteralPath $zipPath).Length / 1MB)) + ' MB)')
}
Write-Output ('[portable] Executable: ' + $mainExe.Name)
Write-Output ('[portable] Version  : ' + $Version)
Write-Output ('[portable] Elapsed  : ' + (Format-Elapsed $total.Elapsed))
Write-Output '[portable] Next: double-click Start-Portable.bat inside the bundle folder.'
Write-Output '[portable] ================================================'
