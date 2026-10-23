param([string]$OutputDirectory)
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
foreach($name in @('Huihui','Kangkang','Yaoyao')) {
    $voice=New-Object -ComObject SAPI.SpVoice
    $token=New-Object -ComObject SAPI.SpObjectToken
    $token.SetId("HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens\MSTTS_V110_zhCN_${name}M")
    $voice.Voice=$token
    $stream=New-Object -ComObject SAPI.SpFileStream
    $path=Join-Path $OutputDirectory "$name.wav"
    try {
        $stream.Open($path,3,$false)
        $voice.AudioOutputStream=$stream
        $voice.Rate=0
        $voice.Volume=100
        [void]$voice.Speak('注意旋转激光。注意旋风。注意拉冰，前往龙左侧安全区。注意影舞者点名。',0)
    } finally { $stream.Close() }
    Write-Output "$name : $path"
}
