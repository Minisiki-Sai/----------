param([int]$AuditProcessId = 19172)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDir
$runDir = Join-Path $projectDir 'outputs/v26c_full_corpus_20260908'
$lastCompleted = -1
while ($true) {
    $active = Get-CimInstance Win32_Process -Filter "ProcessId=$AuditProcessId"
    if (-not $active -or $active.CommandLine -notlike '*v26c_full_corpus_20260908*') { break }
    $completed = @(Get-ChildItem -LiteralPath (Join-Path $runDir 'profession_direct_damage_checkpoints') -File -Filter '*.json').Count
    if ($completed -ne $lastCompleted) {
        Write-Output "FULL_REPLAY_CHECKPOINTS=$completed/203"
        $lastCompleted = $completed
    }
    Start-Sleep -Seconds 10
}
$reportPath = Join-Path $runDir 'profession_direct_damage_report.json'
if (-not (Test-Path -LiteralPath $reportPath)) { throw 'Full replay ended without a final report; do not claim completion.' }
$report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($report.successful_captures -ne 203 -or @($report.failures).Count -ne 0) {
    throw 'Full replay is incomplete or has failures; inspect the report before the A/B stage.'
}
Write-Output 'FULL_REPLAY_COMPLETED=203; STARTING_SEQUENTIAL_SELF_ATTACK_AB'
$captureRoot = 'C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics'
$captures = @('0808竞速最佳.dpscap','0813硬核火舞.dpscap','0817黑典噬魂.dpscap','0821硬核04.dpscap','0826硬核曜.dpscap','0830星期天.dpscap','0831黑硬曜.dpscap') | ForEach-Object { Join-Path $captureRoot $_ }
foreach ($capture in $captures) { if (-not (Test-Path -LiteralPath $capture)) { throw "Capture missing: $capture" } }
& 'C:\Users\gcg59\AppData\Local\Python\pythoncore-3.12-64\python.exe' -B tools/audit_selfattack_ab_v26c.py --capture @captures --allow-capture-replay --output-dir outputs/v26c_selfattack_ab_7captures_after203
exit $LASTEXITCODE
