param(
    [int]$MaxAttempts = 100
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\gcg59\AppData\Local\Python\pythoncore-3.12-64\python.exe"
$captureRoot = "C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
$corpus = Join-Path $workspace "outputs\skill_attribution_corpus_v5\skill_attribution_corpus.json"
$outputPrefix = Join-Path $workspace "outputs\damage_attribution\direct37_variable_branch_full_corpus"
$stdout = "$outputPrefix.stdout.log"
$stderr = "$outputPrefix.stderr.log"
$watchdogLog = "$outputPrefix.watchdog.log"
$resultPath = "$outputPrefix.json"

Set-Location -LiteralPath $workspace
for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    if (Test-Path -LiteralPath $resultPath) {
        try {
            $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
            if ([int]$result.captures -eq 201) {
                "$(Get-Date -Format o) complete before attempt $attempt" |
                    Add-Content -LiteralPath $watchdogLog
                exit 0
            }
        }
        catch {
            # A partially written result is not completion evidence.
        }
    }

    "$(Get-Date -Format o) start attempt $attempt (workers=2)" |
        Add-Content -LiteralPath $watchdogLog
    & $python `
        "tools\audit_direct37_variable_branches.py" `
        $captureRoot `
        "--corpus" $corpus `
        "--output" $outputPrefix `
        "--workers" "2" `
        1>> $stdout 2>> $stderr
    $code = $LASTEXITCODE
    "$(Get-Date -Format o) attempt $attempt exit=$code" |
        Add-Content -LiteralPath $watchdogLog

    if ($code -eq 0 -and (Test-Path -LiteralPath $resultPath)) {
        try {
            $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
            if ([int]$result.captures -eq 201) {
                "$(Get-Date -Format o) complete" |
                    Add-Content -LiteralPath $watchdogLog
                exit 0
            }
        }
        catch {
            # Retry through the normal checkpoint loader.
        }
    }
    Start-Sleep -Seconds 2
}

"$(Get-Date -Format o) exhausted $MaxAttempts attempts" |
    Add-Content -LiteralPath $watchdogLog
exit 2
