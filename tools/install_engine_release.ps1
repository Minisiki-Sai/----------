param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [string]$ManifestPath,
    [string]$VerifierPath,
    [string]$StopHelperPath,
    [string]$ExpectedVersion,
    [string]$LogPath = "",
    [switch]$CleanupOnly
)

# ASCII-only for Windows PowerShell 5.1. No old executable is overwritten.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')) { throw 'An installation directory is required.' }
$runtimeRoot = Join-Path $root 'runtime'
$releasesRoot = Join-Path $runtimeRoot 'engines'
$activePath = Join-Path $runtimeRoot 'active-engine.json'
$previousPath = Join-Path $runtimeRoot 'previous-engine.json'
$releasePath = $null
$activated = $false
$installMutex = $null
$mutexOwned = $false
$enginePrefix = 'runtime/DragonNestEngine/'

function Write-ReleaseLog([string]$Message) {
    if ($LogPath) {
        try { Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("{0:O} {1}" -f [DateTime]::UtcNow, $Message) }
        catch { Write-Warning 'Could not write the installation diagnostic log.' }
    }
}

function File-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($stream)) -replace '-', '').ToLowerInvariant() }
    finally { $hash.Dispose(); $stream.Dispose() }
}

function Resolve-ReleaseEntry([string]$Base, [string]$Relative) {
    $name = $Relative.Replace('\', '/')
    if ($name -match '(^/|:|(^|/)\.\.?(/|$)|//)' -or [string]::IsNullOrWhiteSpace($name)) {
        throw "Unsafe engine archive entry: $Relative"
    }
    $path = [IO.Path]::GetFullPath((Join-Path $Base $name))
    if (-not $path.StartsWith($Base + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Engine archive entry escapes release directory: $Relative"
    }
    return $path
}

function Remove-OwnedRelease([string]$Path) {
    # Only a generated, direct child can ever be a recursive deletion target.
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($full) -ne $releasesRoot -or
        [IO.Path]::GetFileName($full) -notmatch '^[0-9a-f]{12}$') { return }
    if (-not (Test-Path -LiteralPath $full)) { return }
    if ((Get-Item -LiteralPath $full -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { return }
    $nodes = @((Get-Item -LiteralPath $full -Force)) + @(Get-ChildItem -LiteralPath $full -Recurse -Force)
    if (@($nodes | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { return }
    # A scanner may still hold an obsolete release. Skip it rather than force
    # unlocking handles or scheduling deletion of active data on reboot.
    foreach ($node in $nodes | Where-Object { -not $_.PSIsContainer }) {
        $stream = $null
        try { $stream = [IO.File]::Open($node.FullName, 'Open', 'ReadWrite', 'None') }
        catch { Write-ReleaseLog "Retaining busy old release: $full"; return }
        finally { if ($null -ne $stream) { $stream.Dispose() } }
    }
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction Stop
    Write-ReleaseLog "Removed inactive generated release: $full"
}

try {
    # Serialize activation/cleanup for this installation. Without this, two
    # installers could each mistake the other's new active release for garbage.
    $mutexHash = [Security.Cryptography.SHA256]::Create()
    try {
        $mutexKey = ([BitConverter]::ToString($mutexHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToUpperInvariant()))) -replace '-', '').Substring(0, 32)
    }
    finally { $mutexHash.Dispose() }
    $installMutex = [Threading.Mutex]::new($false, ('Local\DragonNestEngineInstall-' + $mutexKey))
    try { $mutexOwned = $installMutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $mutexOwned = $true }
    if (-not $mutexOwned) { throw 'Another installer is activating this installation. Close it before retrying.' }
    if ($CleanupOnly) {
        # Called by the uninstaller only after its normal desktop/engine stop
        # check. Ignore unknown folders, links and locked releases.
        foreach ($parent in @($runtimeRoot, $releasesRoot)) {
            if ((Test-Path -LiteralPath $parent) -and
                ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'Release cleanup does not follow directory links.'
            }
        }
        if (Test-Path -LiteralPath $releasesRoot) {
            foreach ($directory in Get-ChildItem -LiteralPath $releasesRoot -Directory) {
                if ($directory.Name -notmatch '^[0-9a-f]{12}$' -or
                    ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
                $markerPath = Join-Path $directory.FullName 'engine-release.json'
                if (-not (Test-Path -LiteralPath $markerPath)) { continue }
                try {
                    $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    if ($marker.schemaVersion -eq 1 -and $marker.verified -eq $true -and
                        $marker.directory -eq ('engines/' + $directory.Name)) {
                        Remove-OwnedRelease $directory.FullName
                    }
                }
                catch { Write-ReleaseLog "Keeping unremovable release: $($directory.FullName)" }
            }
        }
        foreach ($pointer in @($activePath, $previousPath)) {
            if (Test-Path -LiteralPath $pointer) { Remove-Item -LiteralPath $pointer -Force }
        }
        Write-Output 'Generated engine releases cleaned; user data and legacy folders were preserved.'
        exit 0
    }
    if (-not $ManifestPath -or -not $VerifierPath -or -not $StopHelperPath -or -not $ExpectedVersion) {
        throw 'Manifest, verifier, shutdown helper and expected version are required.'
    }
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schemaVersion -ne 1 -or $manifest.algorithm -ne 'SHA-256' -or
        $manifest.appVersion -ne $ExpectedVersion -or
        $manifest.engineArchive.path -ne 'runtime/engine-package.zip') {
        throw 'Invalid engine archive manifest or version mismatch.'
    }
    $archivePath = Join-Path $root 'runtime\engine-package.zip'
    if ((Get-Item -LiteralPath $archivePath).Length -ne [int64]$manifest.engineArchive.size -or
        (File-Sha256 $archivePath) -ne $manifest.engineArchive.sha256) {
        throw 'Engine archive integrity check failed; the previous engine is unchanged.'
    }
    foreach ($parent in @($runtimeRoot, $releasesRoot)) {
        if (Test-Path -LiteralPath $parent) {
            if ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Engine release parent must not be a link: $parent"
            }
        }
        else { New-Item -ItemType Directory -Path $parent | Out-Null }
    }
    # A new ID even for a reinstall of the same app version avoids all writes
    # to any directory that could still be mapped by a previous engine.
    do {
        $releaseId = [Guid]::NewGuid().ToString('N').Substring(0, 12)
        $releasePath = Join-Path $releasesRoot $releaseId
    } while (Test-Path -LiteralPath $releasePath)
    New-Item -ItemType Directory -Path $releasePath -ErrorAction Stop | Out-Null
    $expected = @{}
    foreach ($entry in $manifest.files) {
        if ([string]$entry.path -like ($enginePrefix + '*')) {
            $relative = ([string]$entry.path).Substring($enginePrefix.Length)
            $null = Resolve-ReleaseEntry $releasePath $relative
            if ($expected.ContainsKey($relative)) { throw 'Duplicate engine manifest entry.' }
            $expected[$relative] = $entry
        }
    }
    if (-not $expected.ContainsKey('DragonNestEngine.exe')) { throw 'The engine executable is missing from the manifest.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    $seen = @{}
    try {
        foreach ($entry in $archive.Entries) {
            $relative = $entry.FullName.Replace('\', '/')
            if (-not $expected.ContainsKey($relative) -or $seen.ContainsKey($relative)) {
                throw "Unexpected or repeated engine archive entry: $relative"
            }
            if ($entry.Length -ne [int64]$expected[$relative].size) { throw "Engine entry size mismatch: $relative" }
            $destination = Resolve-ReleaseEntry $releasePath $relative
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
            $inputStream = $entry.Open()
            $outputStream = $null
            try {
                $outputStream = [IO.File]::Open($destination, 'CreateNew', 'Write', 'None')
                $inputStream.CopyTo($outputStream)
            }
            finally { if ($null -ne $outputStream) { $outputStream.Dispose() }; $inputStream.Dispose() }
            $seen[$relative] = $true
        }
    }
    finally { $archive.Dispose() }
    if ($seen.Count -ne $expected.Count) { throw 'The engine archive is incomplete.' }

    $powershell = Join-Path $PSHOME 'powershell.exe'
    # Preserve the specific failing file/reason rather than replace it with a
    # generic outer exception. Native stderr must not skip exit-code handling.
    try {
        $ErrorActionPreference = 'Continue'
        $verificationOutput = @(& $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $VerifierPath -InstallRoot $root -ManifestPath $ManifestPath -EngineRoot $releasePath -LogPath $LogPath 2>&1)
        $verificationExit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = 'Stop' }
    foreach ($line in $verificationOutput) { Write-ReleaseLog "Verifier: $line" }
    if ($verificationExit -ne 0) {
        $reason = @($verificationOutput | Where-Object { ([string]$_).StartsWith('INSTALL_VERIFY_ERROR: ') })
        $detail = if ($reason.Count) { ([string]$reason[-1]).Substring(22) }
            else { ($verificationOutput | ForEach-Object { [string]$_ }) -join ' ' }
        if ($detail.Length -gt 700) { $detail = $detail.Substring(0, 700) }
        throw "Extracted engine verification failed; activation was cancelled. $detail"
    }
    & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $StopHelperPath -InstallRoot $root -SideBySide -GracefulTimeoutSeconds 8 -UnlockTimeoutSeconds 12 -LogPath $LogPath
    if ($LASTEXITCODE -ne 0) { throw 'The old application is still active; activation was cancelled.' }

    $selection = [ordered]@{
        schemaVersion = 1
        appVersion = $ExpectedVersion
        directory = ('engines/' + $releaseId)
        verified = $true
        engineSha256 = $expected['DragonNestEngine.exe'].sha256
        installedAtUtc = [DateTime]::UtcNow.ToString('o')
    }
    $json = $selection | ConvertTo-Json -Compress
    $encoding = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText((Join-Path $releasePath 'engine-release.json'), $json, $encoding)
    $temporaryPointer = Join-Path $runtimeRoot ('.active-engine-' + $releaseId + '.tmp')
    try {
        [IO.File]::WriteAllText($temporaryPointer, $json, $encoding)
        if (Test-Path -LiteralPath $activePath) {
            [IO.File]::Replace($temporaryPointer, $activePath, $previousPath)
        }
        else { [IO.File]::Move($temporaryPointer, $activePath) }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPointer) { Remove-Item -LiteralPath $temporaryPointer -Force }
    }
    $activated = $true
    Write-ReleaseLog "Activated verified engine $ExpectedVersion in $releasePath"
    Write-Output "Verified engine activated: $ExpectedVersion ($releaseId)"

    # Retain the active and immediately previous release. Never touch the
    # legacy DragonNestEngine folder, user settings, captures or databases.
    try {
        $keep = @($selection.directory)
        if (Test-Path -LiteralPath $previousPath) {
            $previous = Get-Content -LiteralPath $previousPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $keep += [string]$previous.directory
        }
        foreach ($directory in Get-ChildItem -LiteralPath $releasesRoot -Directory) {
            $relative = 'engines/' + $directory.Name
            if ($keep -contains $relative) { continue }
            $markerPath = Join-Path $directory.FullName 'engine-release.json'
            if (-not (Test-Path -LiteralPath $markerPath)) { continue }
            $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($marker.schemaVersion -eq 1 -and $marker.verified -eq $true -and $marker.directory -eq $relative) {
                Remove-OwnedRelease $directory.FullName
            }
        }
    }
    catch { Write-ReleaseLog "Deferred old-release cleanup: $($_.Exception.Message)" }
    exit 0
}
catch {
    Write-ReleaseLog "Release install failed: $($_.Exception.Message)"
    if (-not $activated -and $releasePath) {
        try { Remove-OwnedRelease $releasePath }
        catch { Write-ReleaseLog "Retaining failed staging directory: $releasePath" }
    }
    Write-Output "ENGINE_INSTALL_ERROR: $($_.Exception.Message)"
    exit 3
}
finally {
    if ($mutexOwned) { $installMutex.ReleaseMutex() }
    if ($null -ne $installMutex) { $installMutex.Dispose() }
}
