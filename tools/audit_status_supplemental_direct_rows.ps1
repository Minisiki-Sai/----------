param(
    [Parameter(Mandatory = $true)]
    [string[]]$Captures,
    [string]$OutputBase = "outputs/damage_attribution/status_supplemental_direct_rows_audit"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$xorKey = [Convert]::FromHexString("c8bfcda4943f2642fd8a723474")
$phaseBySignature = @{
    0xA7 = 0; 0x18 = 0; 0xCE = 0; 0x65 = 1; 0xE5 = 3; 0x97 = 3
    0xFE = 4; 0x7C = 4; 0x2E = 4; 0x6E = 5; 0x87 = 6; 0x92 = 8
    0x28 = 10; 0xD0 = 11
}
$allowedTails = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
@(
    "0001000000000001b100ffffffff",
    "0001010000000001b1004b180000",
    "0001040000000001b1004b180000",
    "0001000000000001b1004b180000",
    "00010000000001014501ffffffff",
    "00010000000001012a00ffffffff",
    "00010000000000012c00ffffffff",
    "0001020000000001b1004b180000",
    "0001010000000001b1002f010000",
    "0001040000000001b1002f010000",
    "0001000000000001a100ffffffff"
) | ForEach-Object { [void]$allowedTails.Add($_) }

function Read-U16LE([byte[]]$bytes, [int]$offset) {
    return [int]$bytes[$offset] -bor ([int]$bytes[$offset + 1] -shl 8)
}

function Read-U32LE([byte[]]$bytes, [int]$offset) {
    return [uint32]([int64]$bytes[$offset] -bor
        ([int64]$bytes[$offset + 1] -shl 8) -bor
        ([int64]$bytes[$offset + 2] -shl 16) -bor
        ([int64]$bytes[$offset + 3] -shl 24))
}

function Read-U16BE([byte[]]$bytes, [int]$offset) {
    return ([int]$bytes[$offset] -shl 8) -bor [int]$bytes[$offset + 1]
}

function Find-Sequence([byte[]]$bytes, [byte[]]$needle, [int]$start) {
    for ($at = [Math]::Max(0, $start); $at -le $bytes.Length - $needle.Length; $at++) {
        $ok = $true
        for ($index = 0; $index -lt $needle.Length; $index++) {
            if ($bytes[$at + $index] -ne $needle[$index]) { $ok = $false; break }
        }
        if ($ok) { return $at }
    }
    return -1
}

function Get-Ipv4Packet([byte[]]$frame) {
    $candidates = [Collections.Generic.List[int]]::new()
    foreach ($offset in @(14, 0, 4, 18)) { $candidates.Add($offset) }
    $search = 0
    while ($search -le $frame.Length - 3) {
        if ($frame[$search] -eq 0x08 -and $frame[$search + 1] -eq 0x00 -and $frame[$search + 2] -eq 0x45) {
            $candidates.Add($search + 2)
        }
        $search++
    }
    foreach ($offset in $candidates) {
        if ($offset -lt 0 -or $offset + 20 -gt $frame.Length) { continue }
        $versionIhl = $frame[$offset]
        $ihl = ($versionIhl -band 0x0F) * 4
        $total = Read-U16BE $frame ($offset + 2)
        $protocol = $frame[$offset + 9]
        if (($versionIhl -shr 4) -eq 4 -and $ihl -ge 20 -and $total -ge $ihl -and
            $offset + $total -le $frame.Length -and $protocol -in @(6, 17)) {
            $packet = [byte[]]::new($total)
            [Array]::Copy($frame, $offset, $packet, 0, $total)
            return $packet
        }
    }
    return $null
}

function Get-UdpPayload([byte[]]$frame) {
    $ip = Get-Ipv4Packet $frame
    if ($null -eq $ip -or $ip[9] -ne 17) { return $null }
    $ihl = ($ip[0] -band 0x0F) * 4
    if ($ihl + 8 -gt $ip.Length) { return $null }
    $udpLength = Read-U16BE $ip ($ihl + 4)
    $end = [Math]::Min($ip.Length, $ihl + [Math]::Max(8, $udpLength))
    $length = $end - ($ihl + 8)
    if ($length -lt 9) { return $null }
    $payload = [byte[]]::new($length)
    [Array]::Copy($ip, $ihl + 8, $payload, 0, $length)
    return $payload
}

function Decode-Body([byte[]]$payload, [int]$phase) {
    $decoded = [byte[]]$payload.Clone()
    for ($index = 9; $index -lt $decoded.Length; $index++) {
        $keyIndex = (($index - $phase) % $xorKey.Length + $xorKey.Length) % $xorKey.Length
        $decoded[$index] = $decoded[$index] -bxor $xorKey[$keyIndex]
    }
    return $decoded
}

function Get-MethodCounts([string]$captureName) {
    $candidate = if ($captureName -eq "0808竞速.dpscap") {
        Join-Path $root "outputs/damage_attribution/0808竞速_result_group_v6.json"
    } elseif ($captureName -eq "0830星期天.dpscap") {
        Join-Path $root "outputs/damage_attribution/0830星期天_result_group_v6.json"
    } else { $null }
    $result = [ordered]@{
        supplemental_cast_owner = 0
        supplemental_continuation = 0
        supplemental_same_packet = 0
    }
    if ($null -ne $candidate -and (Test-Path -LiteralPath $candidate)) {
        $json = Get-Content -LiteralPath $candidate -Raw | ConvertFrom-Json
        foreach ($name in @($result.Keys)) {
            if ($null -ne $json.methods.$name) { $result[$name] = [int64]$json.methods.$name.hits }
        }
    }
    return $result
}

$knownSkills = [Collections.Generic.HashSet[uint32]]::new()
$tinyJobs = Join-Path $root "dragon_nest_builder/data/modular/tables/skills/skill_damage_tiny_jobs"
Get-ChildItem -LiteralPath $tinyJobs -Filter "job_*.json" | ForEach-Object {
    $data = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
    foreach ($row in $data.n) {
        if ($row.Count -ge 2) {
            $id = [uint32]$row[0]
            if ($id -gt 0) { [void]$knownSkills.Add($id) }
        }
    }
}

$reports = [Collections.Generic.List[object]]::new()
$candidateCsv = [Collections.Generic.List[object]]::new()

foreach ($captureText in $Captures) {
    $capture = [IO.Path]::GetFullPath($captureText)
    if (-not (Test-Path -LiteralPath $capture -PathType Leaf)) { throw "Capture not found: $capture" }
    $methodCounts = Get-MethodCounts ([IO.Path]::GetFileName($capture))
    $targetInferenceRows = ($methodCounts.Values | Measure-Object -Sum).Sum
    $rawRows = 0L
    $ordinaryRows = 0L
    $packets = 0L
    $literalRows = 0L
    $literalMatch = 0L
    $literalConflict = 0L
    $objectRows = 0L
    $groupRows = 0L
    $parentRows = 0L
    $duplicatePayloads = 0L
    $objectOffsets = @{}
    $groupOffsets = @{}
    $parentOffsets = @{}
    $singleExactPackets = 0L
    $singleExactRows = 0L
    $subtypes = @{}
    $tails = @{}
    $samples = [Collections.Generic.List[object]]::new()
    $fieldMap = @{}
    $seenPayload = @{}

    $stream = [IO.File]::OpenRead($capture)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        $magic = [Text.Encoding]::ASCII.GetString($reader.ReadBytes(6))
        if ($magic -ne "DNDP1`0") { throw "Invalid DNDP1 trace: $capture" }
        while ($stream.Position -lt $stream.Length) {
            if ($stream.Length - $stream.Position -lt 17) { break }
            $timestamp = $reader.ReadDouble()
            $adapter = $reader.ReadByte()
            $originalLength = $reader.ReadUInt32()
            $capturedLength = $reader.ReadUInt32()
            if ($capturedLength -gt 16MB -or $stream.Length - $stream.Position -lt $capturedLength) { break }
            $frame = $reader.ReadBytes($capturedLength)
            $payload = Get-UdpPayload $frame
            if ($null -eq $payload) { continue }
            $payloadHash = [Convert]::ToHexString(
                [Security.Cryptography.SHA256]::HashData($payload)
            )
            $previousPayload = $seenPayload[$payloadHash]
            if ($null -ne $previousPayload -and
                $previousPayload.Adapter -ne $adapter -and
                [Math]::Abs($timestamp - $previousPayload.Timestamp) -le 0.020) {
                $duplicatePayloads++
                continue
            }
            $seenPayload[$payloadHash] = [pscustomobject]@{
                Timestamp = $timestamp
                Adapter = $adapter
            }
            $phases = if ($phaseBySignature.ContainsKey([int]$payload[5])) {
                @([int]$phaseBySignature[[int]$payload[5]])
            } else { 0..12 }
            $payloadHadStatus = $false
            foreach ($phase in $phases) {
                $decoded = Decode-Body $payload $phase
                $direct = [Collections.Generic.List[object]]::new()
                $directMarkers = [Collections.Generic.List[int]]::new()
                $at = Find-Sequence $decoded ([byte[]](0x08,0x73,0x00)) 9
                while ($at -ge 0) { $directMarkers.Add($at); $at = Find-Sequence $decoded ([byte[]](0x08,0x73,0x00)) ($at + 1) }
                for ($di = 0; $di -lt $directMarkers.Count; $di++) {
                    $dm = $directMarkers[$di]
                    if ($dm -lt 4 -or $dm + 27 -gt $decoded.Length) { continue }
                    $nextDm = if ($di + 1 -lt $directMarkers.Count) { $directMarkers[$di + 1] } else { $decoded.Length }
                    $skill = 0U
                    foreach ($rel in @(102, 114)) {
                        if ($dm + $rel + 4 -le $nextDm) {
                            $value = Read-U32LE $decoded ($dm + $rel)
                            if ($knownSkills.Contains($value)) { $skill = $value; break }
                        }
                    }
                    if ($skill -gt 0) {
                        $direct.Add([pscustomobject]@{
                            Skill = $skill
                            Object = (Read-U32LE $decoded ($dm + 13))
                            Group = (Read-U32LE $decoded ($dm + 4))
                            Offset = $dm
                        })
                    }
                }
                $uniqueExactSkills = @($direct | Select-Object -ExpandProperty Skill -Unique)
                if ($uniqueExactSkills.Count -eq 1) { $singleExactPackets++ }

                for ($subtype = 0; $subtype -le 4; $subtype++) {
                    $marker = [byte[]](0x1C,0x1B,0x00,$subtype)
                    $sm = Find-Sequence $decoded $marker 9
                    while ($sm -ge 0) {
                        if ($sm -ge 4 -and $sm + 30 -le $decoded.Length) {
                            $target = Read-U32LE $decoded ($sm - 4)
                            $delta = [BitConverter]::ToInt64($decoded, $sm + 4)
                            $source = Read-U32LE $decoded ($sm + 12)
                            $tailBytes = [byte[]]::new(14)
                            [Array]::Copy($decoded, $sm + 16, $tailBytes, 0, 14)
                            $tail = [Convert]::ToHexString($tailBytes).ToLowerInvariant()
                            if ($source -gt 0 -and $target -gt 0 -and $delta -lt 0 -and $delta -ge -1000000000000 -and $allowedTails.Contains($tail)) {
                                $payloadHadStatus = $true
                                $rawRows++
                                $subtypes[[string]$subtype] = 1L + [int64]($subtypes[[string]$subtype])
                                $tails[$tail] = 1L + [int64]($tails[$tail])
                                $ordinary = $source -gt 0x00FFFFFF -and $target -le 0x00FFFFFF -and $tail -ne "0001000000000001a100ffffffff"
                                if ($ordinary) {
                                    $ordinaryRows++
                                    if ($uniqueExactSkills.Count -eq 1) { $singleExactRows++ }
                                    $literalValues = [Collections.Generic.HashSet[uint32]]::new()
                                    $objectMatch = $false; $groupMatch = $false; $parentMatch = $false
                                    foreach ($width in @(1,2,4)) {
                                        for ($rel = 16; $rel -le 30 - $width; $rel++) {
                                            $value = if ($width -eq 1) { [uint32]$decoded[$sm + $rel] } elseif ($width -eq 2) { [uint32](Read-U16LE $decoded ($sm + $rel)) } else { Read-U32LE $decoded ($sm + $rel) }
                                            if ($uniqueExactSkills.Count -eq 1) {
                                                $selector = "u$($width * 8)@$([string]::Format('{0:+0;-0;0}', $rel))"
                                                $token = "$selector=$value"
                                                if (-not $fieldMap.ContainsKey($token)) { $fieldMap[$token] = [ordered]@{ Hits = 0L; Skills = [Collections.Generic.HashSet[uint32]]::new() } }
                                                $fieldMap[$token].Hits++
                                                [void]$fieldMap[$token].Skills.Add([uint32]$uniqueExactSkills[0])
                                            }
                                            if ($width -eq 4 -and $knownSkills.Contains($value)) { [void]$literalValues.Add($value) }
                                            if ($width -eq 4) {
                                                if (@($direct | Where-Object { $_.Object -gt 0 -and $_.Object -eq $value }).Count -gt 0) {
                                                    $objectMatch = $true
                                                    $objectOffsets[[string]$rel] = 1L + [int64]$objectOffsets[[string]$rel]
                                                }
                                                if (@($direct | Where-Object { $_.Group -gt 0 -and $_.Group -eq $value }).Count -gt 0) {
                                                    $groupMatch = $true
                                                    $groupOffsets[[string]$rel] = 1L + [int64]$groupOffsets[[string]$rel]
                                                }
                                            }
                                            if ($width -le 2 -and $value -gt 0 -and $value -le $direct.Count) {
                                                $parentMatch = $true
                                                $parentKey = "u$($width * 8)@$rel"
                                                $parentOffsets[$parentKey] = 1L + [int64]$parentOffsets[$parentKey]
                                            }
                                        }
                                    }
                                    if ($literalValues.Count -gt 0) {
                                        $literalRows++
                                        if ($uniqueExactSkills.Count -eq 1 -and $literalValues.Contains([uint32]$uniqueExactSkills[0])) { $literalMatch++ }
                                        elseif ($uniqueExactSkills.Count -eq 1) { $literalConflict++ }
                                    }
                                    if ($objectMatch) { $objectRows++ }
                                    if ($groupMatch) { $groupRows++ }
                                    if ($parentMatch) { $parentRows++ }
                                    if ($samples.Count -lt 12) {
                                        $record = [byte[]]::new(34); [Array]::Copy($decoded, $sm - 4, $record, 0, 34)
                                        $samples.Add([ordered]@{
                                            phase = $phase; marker_offset = $sm; subtype = $subtype
                                            source_actor_id = ('0x{0:X8}' -f $source); target_actor_id = ('0x{0:X8}' -f $target)
                                            damage = -$delta; tail = $tail; record_hex = [Convert]::ToHexString($record).ToLowerInvariant()
                                            same_packet_exact_skills_diagnostic_only = @($uniqueExactSkills)
                                            tail_skill_literals = @($literalValues)
                                        })
                                    }
                                }
                            }
                        }
                        $sm = Find-Sequence $decoded $marker ($sm + 1)
                    }
                }
            }
            if ($payloadHadStatus) { $packets++ }
        }
    } finally { $reader.Dispose(); $stream.Dispose() }

    $fieldRows = [Collections.Generic.List[object]]::new()
    $bySelector = @{}
    foreach ($entry in $fieldMap.GetEnumerator()) {
        $separator = $entry.Key.IndexOf('=')
        $selector = $entry.Key.Substring(0, $separator)
        if (-not $bySelector.ContainsKey($selector)) { $bySelector[$selector] = [Collections.Generic.List[object]]::new() }
        $bySelector[$selector].Add($entry.Value)
    }
    foreach ($entry in $bySelector.GetEnumerator()) {
        $support = 0L; $conflict = 0L; $values = 0
        foreach ($token in $entry.Value) {
            $support += [int64]$token.Hits; $values++
            if ($token.Skills.Count -gt 1) { $conflict += [int64]$token.Hits }
        }
        $fieldRows.Add([ordered]@{ candidate = $entry.Key; support_hits = $support; distinct_values = $values; conflict_hits = $conflict; zero_conflict = ($conflict -eq 0) })
    }
    $sortedFields = @($fieldRows | Sort-Object conflict_hits, @{Expression='support_hits';Descending=$true}, candidate)
    $report = [ordered]@{
        capture = [IO.Path]::GetFileName($capture)
        production_inference_counts_from_existing_audit = $methodCounts
        target_inference_rows = [int64]$targetInferenceRows
        raw_status_packets = $packets
        cross_adapter_duplicate_payloads_skipped = $duplicatePayloads
        raw_status_rows = $rawRows
        ordinary_player_source_status_rows = $ordinaryRows
        rows_with_single_same_packet_exact_skill_diagnostic_only = $singleExactRows
        candidate_evidence = [ordered]@{
            literal_skill_rows = $literalRows
            literal_matches_single_exact_skill = $literalMatch
            literal_conflicts_single_exact_skill = $literalConflict
            fixed_tail_object_id_matches = $objectRows
            fixed_tail_object_id_match_offsets = $objectOffsets
            fixed_tail_result_group_matches = $groupRows
            fixed_tail_result_group_match_offsets = $groupOffsets
            fixed_tail_small_parent_ordinal_hypotheses = $parentRows
            fixed_tail_small_parent_ordinal_offsets = $parentOffsets
        }
        subtype_counts = $subtypes
        tail_counts = $tails
        candidate_offsets = @($sortedFields | Select-Object -First 42)
        samples = $samples
    }
    $reports.Add($report)

    foreach ($field in $sortedFields) {
        $candidateCsv.Add([pscustomobject]@{
            capture = $report.capture; candidate = $field.candidate; support_hits = $field.support_hits
            conflict_hits = $field.conflict_hits; zero_conflict = $field.zero_conflict
            verdict = if ($field.conflict_hits -gt 0) { 'rejected_conflict' } else { 'rejected_no_independent_truth' }
            reason = if ($field.conflict_hits -gt 0) { 'same token co-occurs with multiple independently encoded exact skills' } else { 'same-packet agreement is diagnostic only and cannot prove field semantics' }
        })
    }
}

$aggregate = [ordered]@{
    target_inference_rows = [int64](($reports | ForEach-Object { $_.target_inference_rows } | Measure-Object -Sum).Sum)
    raw_status_rows = [int64](($reports | ForEach-Object { $_.raw_status_rows } | Measure-Object -Sum).Sum)
    ordinary_player_source_status_rows = [int64](($reports | ForEach-Object { $_.ordinary_player_source_status_rows } | Measure-Object -Sum).Sum)
    accepted_deterministic_links = 0
    newly_coverable_hits = 0
    conclusion = 'No fixed skill/object/entity/parent key passed independent zero-conflict validation.'
}
$result = [ordered]@{
    schema = 'status-supplemental-direct-rows-audit-v1'
    policy = [ordered]@{
        scope = @($reports.capture)
        production_code_modified = $false
        forbidden = @('recent_action','result_group_inheritance','damage_fingerprint','same_packet_primary_skill_propagation')
        note = 'Single-skill same-packet rows are used only as a negative collision control, never as attribution.'
    }
    aggregate = $aggregate
    reports = $reports
}

$base = [IO.Path]::GetFullPath((Join-Path $root $OutputBase))
$directory = Split-Path -Parent $base
[IO.Directory]::CreateDirectory($directory) | Out-Null
$result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath "$base.json" -Encoding utf8
$candidateCsv | Export-Csv -LiteralPath "$base.csv" -NoTypeInformation -Encoding utf8

$md = [Collections.Generic.List[string]]::new()
$md.Add('# status_decoder 普通数值正伤害严格归因审计')
$md.Add('')
$md.Add('## 阶段结论')
$md.Add('')
$md.Add("- 两份抓包当前三类推断行：$($aggregate.target_inference_rows)")
$md.Add("- 扫描到普通玩家来源状态正伤害行：$($aggregate.ordinary_player_source_status_rows)")
$md.Add('- 新增可确定覆盖：**0**')
$md.Add('- 未发现位于已验证 34 字节记录内、具有独立语义真值且跨抓包零冲突的固定 skill/object/entity/父结果索引。')
$md.Add('')
$md.Add('原始固定结构为 `target[4] + 1C 1B 00 subtype[4] + signed HP delta[8] + source[4] + tail[14]`。三种 `supplemental_*` 名称来自状态解码后的动作/延续/同包推断，不是原始字段。')
$md.Add('')
$md.Add('## 两份抓包')
$md.Add('')
$md.Add('| 抓包 | 推断行 | 原始状态行 | 普通玩家来源行 | tail skill 字面量 | 字面量冲突 | object 匹配 | group 匹配 | 父序号假设 |')
$md.Add('|---|---:|---:|---:|---:|---:|---:|---:|---:|')
foreach ($report in $reports) {
    $e = $report.candidate_evidence
    $md.Add("| $($report.capture) | $($report.target_inference_rows) | $($report.raw_status_rows) | $($report.ordinary_player_source_status_rows) | $($e.literal_skill_rows) | $($e.literal_conflicts_single_exact_skill) | $($e.fixed_tail_object_id_matches) | $($e.fixed_tail_result_group_matches) | $($e.fixed_tail_small_parent_ordinal_hypotheses) |")
}
$md.Add('')
$md.Add('## 已排除连接')
$md.Add('')
$md.Add('- `tail +16..+29` 的 u8/u16/u32：候选偏移及冲突数见 CSV/JSON；有冲突的直接排除，零冲突但只有同包相关性的也不能自证语义。')
$md.Add('- tail 内对象 ID：没有形成可接受的固定对象键；相邻 direct37 的对象字段不属于这条 34 字节记录。')
$md.Add('- 小整数父结果序号：固定尾部本来就含大量 0/1，未发现独立父索引语义。')
$md.Add('- 最近动作、continuation、同包主技能、result group、伤害指纹：按审计规则禁止。')
$md.Add('')
$md.Add('## Zero-conflict 证据')
$md.Add('')
$md.Add('接受候选为 0，支持命中为 0。CSV 中的 `zero_conflict=true` 仅表示在“单一同包精确技能”负对照里未观察到碰撞，不构成独立语义证据，因此仍被拒绝。')
$md.Add('')
$md.Add('原始十六进制样本和每个候选偏移的支持/冲突数见同名 JSON。')
$md | Set-Content -LiteralPath "$base.md" -Encoding utf8

$aggregate | ConvertTo-Json -Depth 4
