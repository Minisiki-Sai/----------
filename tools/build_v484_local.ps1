param([ValidateSet('4.84','4.85','4.86','4.87','4.88')][string]$Version = '4.84')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $root
$build = Join-Path $root ('outputs/build_v' + $Version.Replace('.','') + '_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $build -Force | Out-Null
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Build command failed: $LASTEXITCODE" } }
function Preserve-Generated([string]$relative, [string]$name) {
    $source = [IO.Path]::GetFullPath((Join-Path $root $relative))
    $target = [IO.Path]::GetFullPath((Join-Path $build $name))
    if (!$source.StartsWith($root + '\') -or !$target.StartsWith($build + '\')) { throw 'Outside workspace' }
    if (Test-Path -LiteralPath $source) {
        if (Test-Path -LiteralPath $target) { throw "Backup already exists: $target" }
        Move-Item -LiteralPath $source -Destination $target
    }
}
& py -3.12 tools/run_pyinstaller.py --noconfirm --distpath "$build/engine_dist" --workpath "$build/engine_work" DragonNestEngine_V420.spec
Check-Exit
& "$build/engine_dist/DragonNestEngine/DragonNestEngine.exe" --verify-runtime
Check-Exit
Preserve-Generated 'runtime/DragonNestEngine' 'previous-engine'
Copy-Item -LiteralPath "$build/engine_dist/DragonNestEngine" -Destination "$root/runtime/DragonNestEngine" -Recurse
& py -3.12 tools/generate_install_manifest.py --project-root $root --version "$Version.0"
Check-Exit
& "$PSScriptRoot/verify_install_manifest.ps1" -InstallRoot $root -ManifestPath "$root/runtime/install-manifest.json"
Preserve-Generated 'frontend/src-tauri/target/release/runtime' 'previous-target-runtime'
Preserve-Generated 'frontend/src-tauri/target/release/third_party' 'previous-target-third-party'
$helpers = "$root/frontend/src-tauri/target/release/tools"
New-Item -ItemType Directory -Path $helpers -Force | Out-Null
foreach ($name in @('stop_installed_backend.ps1','install_engine_release.ps1','verify_install_manifest.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $helpers $name) -Force
}
& "$PSScriptRoot/build_frontend_release.cmd"
Check-Exit
& py -3.12 -X utf8 -B tools/stage_v482_release.py --version $Version
Check-Exit
Write-Output "v$Version local EXE and ZIP built; no cloud publication performed."
