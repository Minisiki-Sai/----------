param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [string]$EngineRoot = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Write-VerificationLog([string]$Message) {
    if ($LogPath) {
        try { Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("{0:O} {1}" -f [DateTime]::UtcNow, $Message) }
        catch { Write-Warning 'Could not write the verification diagnostic log.' }
    }
}

function Read-InstallFileHash([string]$Path, [string]$Relative) {
    # Antivirus/indexing may briefly hold an exclusive handle after extraction.
    # Retry only sharing/lock violations, never integrity failures or denial of
    # permission. A file remains subject to the full size and SHA-256 checks.
    for ($attempt = 1; $attempt -le 8; $attempt++) {
        $stream = $null
        $algorithm = $null
        try {
            $stream = [IO.File]::OpenRead($Path)
            $algorithm = [Security.Cryptography.SHA256]::Create()
            return ([BitConverter]::ToString($algorithm.ComputeHash($stream)) -replace '-', '').ToLowerInvariant()
        }
        catch {
            $cause = $_.Exception.GetBaseException()
            $code = $cause.HResult -band 0xffff
            if (($cause -is [IO.IOException]) -and ($code -in @(32, 33)) -and $attempt -lt 8) {
                Write-VerificationLog "Retrying busy install file ($attempt/8): $Relative"
            }
            else { throw "Install file read failed: $Relative; $($cause.Message)" }
        }
        finally {
            if ($null -ne $algorithm) { $algorithm.Dispose() }
            if ($null -ne $stream) { $stream.Dispose() }
        }
        Start-Sleep -Milliseconds 300
    }
}

try {
    $root = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    $rootPrefix = $root + '\'
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schemaVersion -ne 1 -or $manifest.algorithm -ne 'SHA-256') {
        throw 'Unsupported install manifest format.'
    }

    $checked = 0
    $entries = @($manifest.files)
    if ($null -ne $manifest.engineArchive) { $entries += $manifest.engineArchive }
    Write-VerificationLog "Begin verification version=$($manifest.appVersion) install_root=$root engine_root=$EngineRoot files=$($entries.Count)"
    foreach ($entry in $entries) {
        $relative = [string]$entry.path
        $target = [System.IO.Path]::GetFullPath((Join-Path $root ($relative -replace '/', '\')))
        if ($EngineRoot -and $relative.StartsWith('runtime/DragonNestEngine/', [StringComparison]::Ordinal)) {
            $engineBase = [IO.Path]::GetFullPath($EngineRoot).TrimEnd('\')
            if (-not $engineBase.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Engine root escapes install root.'
            }
            $target = [IO.Path]::GetFullPath((Join-Path $engineBase $relative.Substring(25)))
            if (-not $target.StartsWith($engineBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw "Engine manifest path escapes release directory: $relative"
            }
        }
        if (-not $target.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Manifest path escapes install root: $relative"
        }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "Missing install file: $relative"
        }
        $file = Get-Item -LiteralPath $target
        if ($file.Length -ne [int64]$entry.size) {
            throw "Install file size mismatch: $relative; expected=$($entry.size) actual=$($file.Length)"
        }
        $actual = Read-InstallFileHash $target $relative
        if ($actual -ne ([string]$entry.sha256).ToLowerInvariant()) {
            throw "Install file hash mismatch: $relative; expected=$($entry.sha256) actual=$actual"
        }
        $checked++
    }

    $result = [ordered]@{
        verified = $true
        version = [string]$manifest.appVersion
        checkedFiles = $checked
        verifiedAtUtc = [DateTime]::UtcNow.ToString('o')
    }
    $resultPath = if ($EngineRoot) { Join-Path $EngineRoot 'install-verification.json' }
        else { Join-Path $root 'runtime\install-verification.json' }
    $result | ConvertTo-Json -Compress | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-VerificationLog "Install verification passed: $checked files, version $($manifest.appVersion)."
    Write-Output "Install verification passed: $checked files, version $($manifest.appVersion)."
    exit 0
}
catch {
    $reason = $_.Exception.Message
    Write-VerificationLog "Install verification failed: $reason"
    # A compact machine-readable line survives nsExec's limited output buffer.
    # Write-Error with ErrorAction=Stop would throw again and bypass exit 3.
    Write-Output "INSTALL_VERIFY_ERROR: $reason"
    exit 3
}
