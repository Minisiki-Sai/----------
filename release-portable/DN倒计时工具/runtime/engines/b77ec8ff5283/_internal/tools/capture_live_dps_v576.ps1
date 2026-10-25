param(
    [int]$DurationSeconds = 45,
    [string]$ServerAddress = "",
    [int]$ServerPort = 0,
    [ValidateSet("ANY", "TCP", "UDP")]
    [string]$TransportProtocol = "ANY",
    [string]$LocalUdpPorts = "",
    [switch]$AutoGameUdp,
    [switch]$LiveStream,
    [string]$StreamOutputPath = "",
    [string]$StreamStatusPath = "",
    [string]$StreamStopPath = "",
    [int]$MaximumSeconds = 86400,
    [switch]$NonInteractive,
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "This capture must be run as administrator."
}

$udpPorts = @(
    $LocalUdpPorts -split ',' |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match '^\d+$' } |
        ForEach-Object { [int]$_ }
)

if ($AutoGameUdp) {
    $gameProcess = Get-Process -Name "dragonnest_reborn" -ErrorAction Stop |
        Select-Object -First 1
    $udpPorts = @(
        Get-NetUDPEndpoint -OwningProcess $gameProcess.Id -ErrorAction Stop |
            Select-Object -ExpandProperty LocalPort |
            Sort-Object -Unique
    )
    if ($udpPorts.Count -eq 0) {
        throw "No Dragon Nest UDP endpoint was found."
    }
}

if ($udpPorts.Count -eq 0 -and ([string]::IsNullOrWhiteSpace($ServerAddress) -or $ServerPort -le 0)) {
    $gameProcess = Get-Process -Name "dragonnest_reborn" -ErrorAction Stop | Select-Object -First 1
    $gameConnection = Get-NetTCPConnection -OwningProcess $gameProcess.Id -State Established -ErrorAction Stop |
        Where-Object {
            $_.RemoteAddress -notin @("127.0.0.1", "::1", "0.0.0.0") -and
            $_.RemotePort -ge 10000
        } |
        Sort-Object RemotePort -Descending |
        Select-Object -First 1
    if ($null -eq $gameConnection) {
        throw "No established Dragon Nest game-server connection was found."
    }
    $ServerAddress = $gameConnection.RemoteAddress
    $ServerPort = [int]$gameConnection.RemotePort
}

$outputDir = $OutputDirectory
if ([string]::IsNullOrWhiteSpace($outputDir)) {
    $outputDir = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "DragonNestDpsCapture"
}
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$etlPath = Join-Path $outputDir "dragon_nest_v576_$stamp.etl"
$pcapPath = Join-Path $outputDir "dragon_nest_v576_$stamp.pcapng"
$textPath = Join-Path $outputDir "dragon_nest_v576_$stamp.txt"
$logPath = Join-Path $outputDir "dragon_nest_v576_$stamp.capture.log"
$filterName = "DragonNestV576"

Start-Transcript -Path $logPath -Force | Out-Null

function Reset-PktMonState {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    pktmon stop 2>$null | Out-Null
    pktmon filter remove 2>$null | Out-Null
    $ErrorActionPreference = $previousPreference
}

function Write-StreamStatus {
    param(
        [string]$State,
        [string]$Message = "",
        [object]$PacketMonitorPid = 0
    )
    if ([string]::IsNullOrWhiteSpace($StreamStatusPath)) {
        return
    }
    $normalizedPacketMonitorPid = 0
    if ($null -ne $PacketMonitorPid) {
        [void][int]::TryParse([string]$PacketMonitorPid, [ref]$normalizedPacketMonitorPid)
    }
    $payload = [ordered]@{
        state = $State
        message = $Message
        pktmon_pid = $normalizedPacketMonitorPid
        ports = @($udpPorts)
        updated_at = (Get-Date).ToString("o")
    }
    $temporaryPath = "$StreamStatusPath.tmp"
    $payload | ConvertTo-Json -Compress |
        Set-Content -LiteralPath $temporaryPath -Encoding utf8
    Move-Item -LiteralPath $temporaryPath -Destination $StreamStatusPath -Force
}

if ($LiveStream) {
    if ([string]::IsNullOrWhiteSpace($StreamOutputPath)) {
        $StreamOutputPath = Join-Path $outputDir "dragon_nest_v576_$stamp.live.txt"
    }
    if ([string]::IsNullOrWhiteSpace($StreamStatusPath)) {
        $StreamStatusPath = Join-Path $outputDir "dragon_nest_v576_$stamp.live.status.json"
    }
    if ([string]::IsNullOrWhiteSpace($StreamStopPath)) {
        $StreamStopPath = Join-Path $outputDir "dragon_nest_v576_$stamp.live.stop"
    }
    $streamErrorPath = [IO.Path]::ChangeExtension($StreamOutputPath, ".error.txt")
    Remove-Item -LiteralPath $StreamOutputPath, $streamErrorPath, $StreamStatusPath, $StreamStopPath `
        -Force -ErrorAction SilentlyContinue
    Write-StreamStatus -State "starting" -Message "Starting real-time packet capture"
    $packetMonitorProcess = $null
    try {
        Reset-PktMonState
        if ($udpPorts.Count -gt 0) {
            for ($index = 0; $index -lt $udpPorts.Count; $index++) {
                pktmon filter add "$filterName-$index" -t UDP -p $udpPorts[$index] | Out-Null
            }
        }
        elseif ($TransportProtocol -eq "TCP") {
            pktmon filter add $filterName -i $ServerAddress -t TCP -p $ServerPort | Out-Null
        }
        elseif ($TransportProtocol -eq "UDP") {
            pktmon filter add $filterName -i $ServerAddress -t UDP | Out-Null
        }
        else {
            pktmon filter add $filterName -i $ServerAddress | Out-Null
        }

        $packetMonitorArguments = @(
            "start", "--capture", "--comp", "nics", "--type", "flow",
            "--pkt-size", "0", "--flags", "0x010", "--log-mode", "real-time"
        )
        $packetMonitorExecutable = Join-Path $env:SystemRoot "System32\pktmon.exe"
        $winptyCandidates = @(
            (Get-Command "winpty.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
            "C:\Program Files\Git\usr\bin\winpty.exe",
            "C:\Program Files (x86)\Git\usr\bin\winpty.exe"
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_) }
        $winptyPath = $winptyCandidates | Select-Object -First 1
        if (-not [string]::IsNullOrWhiteSpace($winptyPath)) {
            $packetMonitorArguments = @("--", $packetMonitorExecutable) + $packetMonitorArguments
            $packetMonitorExecutable = $winptyPath
        }
        $packetMonitorProcess = Start-Process -FilePath $packetMonitorExecutable `
            -ArgumentList $packetMonitorArguments `
            -RedirectStandardOutput $StreamOutputPath `
            -RedirectStandardError $streamErrorPath `
            -WindowStyle Hidden -PassThru
        $packetMonitorPid = [int]$packetMonitorProcess.Id
        Write-StreamStatus -State "capturing" -Message "Real-time capture is running" -PacketMonitorPid $packetMonitorPid

        $startedAt = Get-Date
        while (-not (Test-Path -LiteralPath $StreamStopPath)) {
            if ($packetMonitorProcess.HasExited) {
                $errorText = ""
                if (Test-Path -LiteralPath $streamErrorPath) {
                    $errorText = (Get-Content -LiteralPath $streamErrorPath -Raw -ErrorAction SilentlyContinue).Trim()
                }
                if ([string]::IsNullOrWhiteSpace($errorText)) {
                    $errorText = "PktMon real-time process exited unexpectedly (code $($packetMonitorProcess.ExitCode))."
                }
                throw $errorText
            }
            if ($MaximumSeconds -gt 0 -and ((Get-Date) - $startedAt).TotalSeconds -ge $MaximumSeconds) {
                break
            }
            Start-Sleep -Milliseconds 200
        }
        Write-StreamStatus -State "stopping" -Message "Stopping real-time capture" -PacketMonitorPid $packetMonitorPid
        pktmon stop 2>$null | Out-Null
        if (-not $packetMonitorProcess.WaitForExit(5000)) {
            $packetMonitorProcess.Kill()
        }
        Write-StreamStatus -State "complete" -Message "Real-time capture stopped"
    }
    catch {
        Write-StreamStatus -State "failed" -Message $_.Exception.Message
    }
    finally {
        if ($null -ne $packetMonitorProcess -and -not $packetMonitorProcess.HasExited) {
            $packetMonitorProcess.Kill()
        }
        Reset-PktMonState
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    }
    exit 0
}

Write-Host "Dragon Nest v576 read-only network sample" -ForegroundColor Cyan
if ($udpPorts.Count -gt 0) {
    Write-Host "Local game UDP ports: $($udpPorts -join ', ')"
}
else {
    Write-Host "Server: $ServerAddress`:$ServerPort"
    Write-Host "Transport: $TransportProtocol"
}
Write-Host "Duration: $DurationSeconds seconds"
Write-Host "Please deal damage during the capture so the built-in DPS meter updates."
Write-Host ""

try {
    try {
        Reset-PktMonState
        if ($udpPorts.Count -gt 0) {
            for ($index = 0; $index -lt $udpPorts.Count; $index++) {
                pktmon filter add "$filterName-$index" -t UDP -p $udpPorts[$index] | Out-Null
            }
        }
        elseif ($TransportProtocol -eq "TCP") {
            pktmon filter add $filterName -i $ServerAddress -t TCP -p $ServerPort | Out-Null
        }
        elseif ($TransportProtocol -eq "UDP") {
            pktmon filter add $filterName -i $ServerAddress -t UDP | Out-Null
        }
        else {
            pktmon filter add $filterName -i $ServerAddress | Out-Null
        }
        pktmon start --capture --comp nics --type flow --pkt-size 0 --flags 0x010 --file-name $etlPath --file-size 256 --log-mode circular | Out-Null

        Write-Host "Capture started." -ForegroundColor Green
        for ($remaining = $DurationSeconds; $remaining -gt 0; $remaining--) {
            Write-Progress -Activity "Capturing Dragon Nest combat traffic" -Status "$remaining seconds remaining" -PercentComplete ((($DurationSeconds - $remaining) / $DurationSeconds) * 100)
            Start-Sleep -Seconds 1
        }
        Write-Progress -Activity "Capturing Dragon Nest combat traffic" -Completed
    }
    finally {
        Reset-PktMonState
    }

    if (-not (Test-Path -LiteralPath $etlPath)) {
        throw "PktMon did not create the ETL capture."
    }

    pktmon etl2pcap $etlPath --out $pcapPath | Out-Null
    pktmon etl2txt $etlPath --out $textPath --timestamp --brief --hex | Out-Null

    Write-Host ""
    Write-Host "Capture completed." -ForegroundColor Green
    Write-Host "ETL:    $etlPath"
    Write-Host "PCAPNG: $pcapPath"
    Write-Host "TEXT:   $textPath"
}
catch {
    Write-Host ""
    Write-Host "Capture failed: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    Write-Host ""
    Write-Host "Log: $logPath"
    if (-not $NonInteractive) {
        Write-Host "Press Enter to close."
        [void](Read-Host)
    }
}
