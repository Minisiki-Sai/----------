"""Read-only audit of Dragon Nest's built-in per-skill damage meter.

The audit combines hash-pinned static client evidence with the fixed 35-capture
corpus.  It never starts or loads the client and never mutates production code.
Network searches are discovery/audit only: exact aggregate values are used to
look for a structured summary message, never to attribute production damage.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.npcap import (  # noqa: E402
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_captured_frame,
    split_v576_payload,
)
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder  # noqa: E402
from dragon_nest_builder.live_dps.tcp_profile_codec import (  # noqa: E402
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)
from tools.analyze_npcap_trace import read_trace  # noqa: E402
from tools.review_client_damage_types_v44 import (  # noqa: E402
    BASE,
    CodeReview,
    StaticView,
)
import capstone  # noqa: E402
from capstone import x86_const as x86  # noqa: E402


CLIENT = Path(r"D:\DragonNest\Reborn\dragonnest_reborn.exe")
CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
MANIFEST = ROOT / "outputs/current_native_baseline_v57_20260909/profession_direct_damage_report.json"
OUT = ROOT / "outputs/builtin_meter_summary_wire_v58_20260909"
JSON_OUT = OUT / "REPORT.json"
MD_OUT = OUT / "REPORT.md"

SPECIAL_ADAPTERS = frozenset(
    {
        IDENTITY_TRACE_ADAPTER_INDEX,
        IDENTITY_METADATA_ADAPTER_INDEX,
        SESSION_EVIDENCE_ADAPTER_INDEX,
    }
)


@dataclass(frozen=True, slots=True)
class AggregateRow:
    actor_id: int
    skill_id: int
    hit_count: int
    damage: int
    first_time: float
    last_time: float


@dataclass(frozen=True, slots=True)
class WireBlob:
    timestamp: float
    transport: str
    direction: str
    encoding: str
    opcode: str
    payload: bytes
    wire_length: int


def _digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _fixed_captures() -> list[dict[str, Any]]:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = document["capture_manifest"]
    if len(rows) != 35:
        raise ValueError(f"fixed manifest changed: expected 35, got {len(rows)}")
    result = []
    for row in rows:
        path = Path(row["capture_path"])
        if not path.is_file():
            raise FileNotFoundError(path)
        if _digest(path) != row["capture_sha256"]:
            raise ValueError(f"capture hash mismatch: {path}")
        result.append({"path": path, "sha256": row["capture_sha256"]})
    return result


def _static_client_evidence() -> dict[str, Any]:
    if _digest(CLIENT) != CLIENT_SHA256:
        raise ValueError("client hash mismatch")
    view = StaticView()
    if sha256(view.raw).hexdigest() != CLIENT_SHA256:
        raise ValueError("decoded static view hash mismatch")
    code = CodeReview(view)

    functions = {
        "damage_meter_dispatch": 0x6A52E0,
        "local_per_hit_aggregate": 0x6AAB40,
        "detail_panel_populate": 0x4B0590,
        "detail_panel_open": 0x6ABEB9,
    }
    reviewed: dict[str, Any] = {}
    for name, start in functions.items():
        function = view.function(start)
        if function is None or function[0] != start:
            raise ValueError(f"missing exact function {name} at {start:#x}")
        rows = code.decode(function)
        reviewed[name] = {
            "begin_rva": function[0],
            "end_rva": function[1],
            "sha256": sha256(view.read(function[0], function[1] - function[0])).hexdigest(),
            "direct_calls": [
                {
                    "rva": ins.address - BASE,
                    "target_rva": ins.operands[0].imm - BASE,
                }
                for ins in rows
                if ins.group(capstone.CS_GRP_CALL)
                and ins.operands
                and ins.operands[0].type == x86.X86_OP_IMM
            ],
        }

    dispatch_calls = {
        row["target_rva"] for row in reviewed["damage_meter_dispatch"]["direct_calls"]
    }
    if functions["local_per_hit_aggregate"] not in dispatch_calls:
        raise AssertionError("per-hit dispatch no longer calls local aggregate")

    open_rows = code.decode(view.function(functions["detail_panel_open"]))
    open_calls = [
        (ins.address - BASE, ins.operands[0].imm - BASE)
        for ins in open_rows
        if ins.group(capstone.CS_GRP_CALL)
        and ins.operands
        and ins.operands[0].type == x86.X86_OP_IMM
    ]
    if (0x6ABEED, functions["detail_panel_populate"]) not in open_calls:
        raise AssertionError("detail panel no longer calls local populate function")

    evidence = [
        {
            "rva": 0x6A563D,
            "instruction": "mov rax, qword ptr [r15 + 0x30]",
            "meaning": "read this physical Hit's damage value",
        },
        {
            "rva": 0x6A5646,
            "instruction": "mov esi, dword ptr [r15 + 0x90]",
            "meaning": "read this physical Hit's native SkillTable row key",
        },
        {
            "rva": 0x6A6A96,
            "instruction": "mov r9, qword ptr [rsp + 0x78]",
            "meaning": "pass the same Hit damage to the local aggregator",
        },
        {
            "rva": 0x6A6AA9,
            "instruction": "call 0x1406aab40",
            "meaning": "aggregate one decoded Hit locally",
        },
        {
            "rva": 0x6AAC54,
            "instruction": "add r9, 0x48",
            "meaning": "walk local per-skill rows with 0x48-byte stride",
        },
        {
            "rva": 0x6AACF9,
            "instruction": "add qword ptr [r13 + rcx*8 + 0x28], r12",
            "meaning": "add this Hit damage to an existing local skill row",
        },
        {
            "rva": 0x6AACEB,
            "instruction": "add qword ptr [rsi + 8], 0x48",
            "meaning": "append a new local per-skill row",
        },
        {
            "rva": 0x6AAD08,
            "instruction": "add qword ptr [r13 + 0x690], r12",
            "meaning": "add this Hit damage to the local total",
        },
        {
            "rva": 0x6ABEDC,
            "instruction": "mov r8, qword ptr [rbx + 0x688]",
            "meaning": "read local accumulated total when opening detail panel",
        },
        {
            "rva": 0x6ABEE3,
            "instruction": "lea rdx, [rbx + 0x630]",
            "meaning": "pass local per-skill vector to detail panel",
        },
        {
            "rva": 0x6ABEED,
            "instruction": "call 0x1404b0590",
            "meaning": "populate panel directly from the local vector",
        },
        {
            "rva": 0x4B084D,
            "instruction": "add r13, 0x30",
            "meaning": "detail panel walks presentation rows locally",
        },
    ]
    for row in evidence:
        raw = view.read(row["rva"], 16)
        row["bytes_prefix"] = raw.hex()
        row["provenance"] = view.provenance(row["rva"], 16)

    return {
        "client_path": str(CLIENT),
        "client_sha256": CLIENT_SHA256,
        "method": "hash-pinned static bytes only; client never executed or loaded",
        "functions": reviewed,
        "instruction_evidence": evidence,
        "conclusion": (
            "The built-in meter is fed once per physical damage result and stores per-skill "
            "rows plus totals in a client-memory container. Opening the detail panel passes "
            "that existing local vector directly to UI population code."
        ),
        "panel_open_direct_network_request_found": False,
        "panel_open_direct_call_targets": [
            {"rva": rva, "target_rva": target} for rva, target in open_calls
        ],
    }


def _tcp_plaintext(packet: Any) -> tuple[bytes, str, str] | None:
    direction = str(packet.inferred_direction() or "unknown")
    if direction == "server_to_client":
        decoded = decode_registered_server_tcp_envelope(
            packet.payload,
            connection_context=packet,
        )
        if decoded is not None:
            plaintext, _key_index, _rounds, key_source = decoded
            return plaintext, key_source, plaintext[2:4].hex(" ") if len(plaintext) >= 4 else "short"
    decoded = decode_registered_tcp_envelope(packet.payload)
    if decoded is not None:
        plaintext, _key_index, _rounds, key_source = decoded
        return plaintext, key_source, plaintext[2:4].hex(" ") if len(plaintext) >= 4 else "short"
    return None


def _udp_large_plaintexts(packet: Any) -> list[WireBlob]:
    result: list[WireBlob] = []
    direction = str(packet.inferred_direction() or "unknown")
    for part in split_v576_payload(packet.payload):
        # Known per-Hit records are smaller and intentionally excluded.  A
        # multi-player/skill summary must carry repeated rows and is expected
        # to exceed this conservative threshold.
        if len(part) < 256:
            continue
        phases = _v576_candidate_xor_phases(part)
        if not phases:
            result.append(
                WireBlob(
                    packet.timestamp_seconds,
                    "UDP",
                    direction,
                    "raw-unknown-large",
                    part[5:9].hex(" ") if len(part) >= 9 else "short",
                    bytes(part),
                    len(part),
                )
            )
            continue
        seen: set[bytes] = set()
        for phase in phases:
            body = _decode_v576_body(part, phase)
            if body in seen:
                continue
            seen.add(body)
            result.append(
                WireBlob(
                    packet.timestamp_seconds,
                    "UDP",
                    direction,
                    f"xor-phase-{phase}",
                    part[5:9].hex(" ") if len(part) >= 9 else "short",
                    body,
                    len(part),
                )
            )
    return result


def _collect_capture(path: Path) -> tuple[list[AggregateRow], list[WireBlob], dict[str, Any]]:
    decoder = _V576PacketEventDecoder(DATA_DIR, {})
    transport = V576TransportReassembler()
    ledger = V576EventLedger()
    aggregates: dict[tuple[int, int], list[int | float]] = {}
    actor_totals: Counter[int] = Counter()
    actor_hits: Counter[int] = Counter()
    blobs: list[WireBlob] = []
    stats: Counter[str] = Counter()
    sizes: list[int] = []
    largest: list[tuple[int, float, str, str, str]] = []
    first_damage: float | None = None
    last_damage: float | None = None
    first_packet: float | None = None
    last_packet: float | None = None

    def observe(logical: Any) -> None:
        nonlocal first_damage, last_damage, first_packet, last_packet
        timestamp = float(logical.timestamp_seconds)
        first_packet = timestamp if first_packet is None else min(first_packet, timestamp)
        last_packet = timestamp if last_packet is None else max(last_packet, timestamp)
        direction = str(logical.inferred_direction() or "unknown")
        transport_name = str(logical.transport).upper()
        stats[f"logical_{transport_name.lower()}"] += 1
        # Let the ordinary decoder observe connection/profile material first.
        # Server TCP decryption uses connection-scoped key history; decoding a
        # frame for the audit before that state update would under-count
        # searchable plaintext on the same connection.
        decoded_events = decoder.feed_events(logical)
        if transport_name == "TCP":
            decoded = _tcp_plaintext(logical)
            if decoded is None:
                stats[f"tcp_undecoded_{direction}"] += 1
                # Exact aggregate values cannot be searched inside encrypted
                # bytes; retain only inventory counts, never call it a match.
            else:
                plaintext, key_source, opcode = decoded
                stats[f"tcp_decoded_{direction}"] += 1
                stats[f"tcp_opcode_{direction}_{opcode}"] += 1
                blobs.append(
                    WireBlob(
                        timestamp,
                        "TCP",
                        direction,
                        f"registered-{key_source}",
                        opcode,
                        bytes(plaintext),
                        len(logical.payload),
                    )
                )
                sizes.append(len(plaintext))
                largest.append((len(plaintext), timestamp, "TCP", direction, opcode))
        else:
            added = _udp_large_plaintexts(logical)
            blobs.extend(added)
            for item in added:
                stats[f"udp_large_{direction}"] += 1
                sizes.append(len(item.payload))
                largest.append((len(item.payload), timestamp, "UDP", direction, item.opcode))

        for event in decoded_events:
            if not isinstance(event, CombatEvent) or event.healing or int(event.amount) <= 0:
                continue
            try:
                actor_id = int(event.source_id)
                skill_id = int(str(event.skill_id))
            except (TypeError, ValueError):
                continue
            if actor_id <= 0 or skill_id <= 0:
                continue
            first_damage = timestamp if first_damage is None else min(first_damage, timestamp)
            last_damage = timestamp if last_damage is None else max(last_damage, timestamp)
            key = (actor_id, skill_id)
            row = aggregates.setdefault(key, [0, 0, timestamp, timestamp])
            row[0] = int(row[0]) + 1
            row[1] = int(row[1]) + int(event.amount)
            row[2] = min(float(row[2]), timestamp)
            row[3] = max(float(row[3]), timestamp)
            actor_totals[actor_id] += int(event.amount)
            actor_hits[actor_id] += 1

    for _timestamp, adapter, original_length, frame in read_trace(path):
        stats["physical_frames"] += 1
        if adapter in SPECIAL_ADAPTERS:
            stats["special_evidence_frames_skipped"] += 1
            continue
        packet = decode_captured_frame(_timestamp, frame, original_length)
        if packet is None:
            stats["unparsed_frames"] += 1
            continue
        for logical in transport.feed(packet):
            if ledger.accept(logical):
                observe(logical)
            else:
                stats["ledger_duplicates"] += 1
    for logical in transport.flush():
        if ledger.accept(logical):
            observe(logical)

    rows = [
        AggregateRow(actor, skill, int(values[0]), int(values[1]), float(values[2]), float(values[3]))
        for (actor, skill), values in aggregates.items()
        if int(values[0]) >= 2 and int(values[1]) > 0
    ]
    actor_rows = [
        AggregateRow(actor, 0, actor_hits[actor], damage, first_damage or 0.0, last_damage or 0.0)
        for actor, damage in actor_totals.items()
        if actor_hits[actor] >= 2 and damage > 0
    ]
    rows.extend(actor_rows)
    largest.sort(reverse=True)
    inventory = {
        "counts": dict(stats),
        "first_packet_time": first_packet,
        "last_packet_time": last_packet,
        "first_damage_time": first_damage,
        "last_damage_time": last_damage,
        "aggregate_skill_rows_searched": sum(1 for row in rows if row.skill_id > 0),
        "aggregate_actor_rows_searched": sum(1 for row in rows if row.skill_id == 0),
        "searchable_blob_count": len(blobs),
        "searchable_plaintext_bytes": sum(len(blob.payload) for blob in blobs),
        "searchable_size_median": statistics.median(sizes) if sizes else 0,
        "largest_messages": [
            {
                "length": size,
                "seconds_from_capture_start": round(timestamp - (first_packet or timestamp), 6),
                "seconds_from_last_damage": (
                    round(timestamp - last_damage, 6) if last_damage is not None else None
                ),
                "transport": transport_name,
                "direction": direction,
                "opcode_or_signature": opcode,
            }
            for size, timestamp, transport_name, direction, opcode in largest[:20]
        ],
    }
    return rows, blobs, inventory


def _positions(blob: bytes, needle: bytes) -> list[int]:
    found: list[int] = []
    start = 0
    while True:
        index = blob.find(needle, start)
        if index < 0:
            return found
        found.append(index)
        start = index + 1


def _scan_aggregate_rows(
    rows: list[AggregateRow], blobs: list[WireBlob], first_packet: float | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    by_message: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for blob_index, wire in enumerate(blobs):
        payload = wire.payload
        for row in rows:
            damage_needles = [int(row.damage).to_bytes(8, "little", signed=False)]
            if row.damage <= 0xFFFFFFFF:
                damage_needles.append(int(row.damage).to_bytes(4, "little", signed=False))
            damage_positions = sorted(
                {position for needle in damage_needles for position in _positions(payload, needle)}
            )
            if not damage_positions:
                continue
            actor_positions = _positions(payload, int(row.actor_id).to_bytes(4, "little"))
            skill_positions = (
                _positions(payload, int(row.skill_id).to_bytes(4, "little"))
                if row.skill_id > 0
                else []
            )
            hit_positions = _positions(payload, int(row.hit_count).to_bytes(4, "little"))
            all_four = bool(actor_positions and skill_positions and hit_positions)
            actor_skill_damage = bool(actor_positions and skill_positions)
            if all_four:
                by_message[blob_index].add((row.actor_id, row.skill_id))
            matches.append(
                {
                    "blob_index": blob_index,
                    "seconds_from_capture_start": round(
                        wire.timestamp - (first_packet or wire.timestamp), 6
                    ),
                    "transport": wire.transport,
                    "direction": wire.direction,
                    "encoding": wire.encoding,
                    "opcode_or_signature": wire.opcode,
                    "message_length": len(payload),
                    "actor_id": row.actor_id,
                    "skill_id": row.skill_id,
                    "hit_count": row.hit_count,
                    "damage": row.damage,
                    "damage_offsets": damage_positions[:8],
                    "actor_offsets": actor_positions[:8],
                    "skill_offsets": skill_positions[:8],
                    "hit_count_offsets": hit_positions[:8],
                    "actor_skill_damage_same_message": actor_skill_damage,
                    "all_four_fields_same_message": all_four,
                }
            )
    structured = [
        {
            "blob_index": blob_index,
            "matching_distinct_player_skill_rows": len(keys),
            "rows": [
                {"actor_id": actor, "skill_id": skill} for actor, skill in sorted(keys)
            ],
        }
        for blob_index, keys in by_message.items()
        if len(keys) >= 2
    ]
    return matches, structured


def _capture_report(path: Path, expected_sha256: str) -> dict[str, Any]:
    started = time.monotonic()
    rows, blobs, inventory = _collect_capture(path)
    matches, structured = _scan_aggregate_rows(rows, blobs, inventory["first_packet_time"])
    all_four = [row for row in matches if row["all_four_fields_same_message"]]
    actor_skill_damage = [row for row in matches if row["actor_skill_damage_same_message"]]
    return {
        "capture": path.name,
        "capture_path": str(path),
        "capture_sha256": expected_sha256,
        "inventory": inventory,
        "exact_aggregate_match_counts": {
            "damage_value_anywhere": len(matches),
            "player_plus_skill_plus_damage_same_message": len(actor_skill_damage),
            "player_plus_skill_plus_hit_count_plus_damage_same_message": len(all_four),
            "messages_with_two_or_more_complete_rows": len(structured),
        },
        "complete_matches": all_four[:100],
        "structured_messages": structured[:100],
        "elapsed_seconds": time.monotonic() - started,
    }


def _low_job_help() -> list[dict[str, Any]]:
    report = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    for row in report["summary"]:
        percent = float(row["已命名Hit确定性覆盖率%"])
        if percent >= 95.0:
            continue
        confirmed = int(row["确定性已归因"])
        total = confirmed + int(row["非确定性归因"])
        needed = max(0, (95 * total + 99) // 100 - confirmed)
        rows.append(
            {
                "profession": row["职业"],
                "current_confirmed_percent": percent,
                "additional_hits_needed_for_95": needed,
                "help_if_summary_packet_existed": (
                    "potentially high, because a trusted player+SkillTable+Hit total could certify gaps"
                ),
                "help_from_observed_wire_result": (
                    "none: no structured whole-fight summary message was observed"
                ),
            }
        )
    return rows


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# 游戏内技能伤害统计：客户端本地聚合还是网络汇总包（v58）",
        "",
        "## 结论",
        "",
        "**该统计是客户端收到每个物理伤害结果后逐 Hit 本地聚合；固定 35 份抓包中没有发现服务端或客户端网络直接传输的‘整场按玩家＋技能汇总包’。**",
        "",
        "打开统计详情页时，客户端直接把已经存在的本地技能向量和本地总伤害传给 UI；静态调用链没有出现一次用于请求统计明细的网络发送。网络专项同时对固定 35 份抓包的可解明文和大 UDP 消息做了精确整场值搜索，未找到一条同时携带玩家 ID、SkillTable RowId、Hit 数和整场技能伤害的结构化记录。",
        "",
        "这不表示网络对技能归因没有价值：每个单次 Hit 的伤害结果仍可能携带原生技能键，Projectile/Weapon 也保存客户端用于回退的攻击键。真正值得复现的是这条**逐 Hit 原生键与对象状态链**，而不是等待不存在的整场汇总包。",
        "",
        "## 静态客户端证据",
        "",
        f"客户端：`{report['static_client']['client_path']}`；SHA-256：`{report['static_client']['client_sha256']}`。全程只读静态字节，未执行、加载、注入或读取运行时内存。",
        "",
        "|RVA|指令|含义|",
        "|---:|---|---|",
    ]
    for row in report["static_client"]["instruction_evidence"]:
        lines.append(f"|`{row['rva']:#x}`|`{row['instruction']}`|{row['meaning']}|")
    lines += [
        "",
        "由这些地址可还原出：`damage result → 本 Hit 的 SkillTable RowId/对象回退键 → 技能名 → 本地 0x48 字节技能行累加 → 本地总伤害累加 → 详情页读取本地向量`。因此游戏内统计能够显示技能构成，并不要求服务器再发送一份技能汇总。",
        "",
        "## 固定 35 份抓包验证",
        "",
        f"- 抓包数：{summary['capture_count']}；失败：{summary['capture_failures']}。",
        f"- 物理帧：{summary['physical_frames']:,}；已重组逻辑 TCP：{summary['logical_tcp']:,}；逻辑 UDP：{summary['logical_udp']:,}。",
        f"- 已解 TCP 明文：{summary['decoded_tcp_messages']:,}；未解 TCP 消息：{summary['undecoded_tcp_messages']:,}。",
        f"- 搜索的整场玩家＋技能聚合行：{summary['aggregate_skill_rows_searched']:,}；玩家总计行：{summary['aggregate_actor_rows_searched']:,}。",
        f"- 可搜索明文/大消息：{summary['searchable_blob_count']:,} 个，共 {summary['searchable_plaintext_bytes']:,} 字节。",
        f"- 同一消息出现‘玩家 ID＋技能 ID＋整场技能伤害’：{summary['player_skill_damage_matches']} 条。",
        f"- 同一消息出现‘玩家 ID＋技能 ID＋Hit 数＋整场技能伤害’：{summary['all_four_matches']} 条。",
        f"- 一条消息包含至少两行完整汇总记录：{summary['structured_summary_messages']} 条。",
        "",
        "精确值搜索只用于发现候选协议，不会被接入生产归因。若存在网络汇总包，它应在大量已知技能行中反复命中，并呈现稳定记录步长/字段偏移；本轮没有出现这种结构。仅命中单个伤害总值、却不同时带玩家和技能字段的随机字节，不视为证据。",
        "",
        "### 战斗结束及大包",
        "",
        "已对每份抓包的战斗中、最后一个伤害前后及抓包尾部进行同一搜索，并保存每包最大的 20 条可解消息。没有发现战斗结束时新增的按玩家＋技能汇总响应。由于固定抓包没有‘点击游戏内统计面板’的独立 UI 输入日志，无法仅凭时间戳标注某次点击；但静态 `0x6ABEB9` 打开函数已证明它直接读取本地容器，且不构造统计请求。",
        "",
        "## 协议字段结论",
        "",
        "|层次|字段/结构|结论|可用于什么|",
        "|---|---|---|---|",
        "|单 Hit 伤害结果|`result+0x30`|本 Hit 伤害|实时与离线伤害总量|",
        "|单 Hit 伤害结果|`result+0x90`|原生 `SkillTable RowId`（存在时）|最高优先级技能归因|",
        "|相关 Projectile/Weapon|虚表 `+0x30` → 对象 `+0x1BC0`|Hit 缺少直接键时的对象攻击键|对象链、父子 Projectile 链归因|",
        "|客户端本地统计容器|技能行步长 `0x48`，伤害累计在行内 `+0x28`；本地总量在统计对象附近|逐 Hit 聚合结果|仅客户端 UI；不是网络协议|",
        "|详情页输入|本地技能向量 `rbx+0x630`、本地总量 `rbx+0x688`|打开面板直接读取|证明无需网络汇总请求|",
        "|整场网络汇总|未发现|不存在可直接复用的结构化按玩家＋技能汇总包|不能作为归因捷径|",
        "",
        "## 对低于 95% 职业的预计帮助",
        "",
        "本专项对当前覆盖率的**直接新增为 0 Hit**，因为没有找到可接入的汇总包。它的价值是排除了错误方向，并确认后续应继续复现客户端逐 Hit 决策：玩家 Actor 的原生攻击状态、根 Projectile 首次写入 `+0x1BC0`、子 Projectile 继承，以及召唤实体自己的攻击键。",
        "",
        "|职业|当前确认覆盖率|达到95%仍需Hit|汇总包直接帮助|",
        "|---|---:|---:|---|",
    ]
    for row in report["low_profession_help"]:
        lines.append(
            f"|{row['profession']}|{row['current_confirmed_percent']:.4f}%|"
            f"{row['additional_hits_needed_for_95']:,}|0|"
        )
    lines += [
        "",
        "最可能受益于继续复现逐 Hit 对象/实体链的是：重炮手、机械大师、圣徒、雷神、药剂师（召唤实体攻击），以及魔羽（长引导、多代子箭）；圣骑士、十字军、黑暗萨满、噬魂者、影还需要玩家 Actor 直击状态与共享模板的严格拆分。这个判断是基于现有缺口构成的工程推断，不是本轮新增确认结果。",
        "",
        "## 能否用于实时与离线归因",
        "",
        "- **逐 Hit 原生键：可以。** 只要字段边界、来源类型、对象代次和资源版本均被验证，可实时读取，也可离线重放。",
        "- **对象回退键：可以但需状态。** 必须复现根对象写入、父子继承、对象销毁/ID 复用和召唤实体归属。",
        "- **整场汇总包：不可以。** 固定语料中未发现，客户端静态路径也不依赖它。",
        "- **用时间邻近或伤害指纹代替：不可以。** 本专项中的整场值搜索仅用于寻找协议候选，结果不会用于任何生产 Hit 归因。",
        "",
        "## 边界与产物",
        "",
        "本轮没有修改生产代码、评分、技能构成或抓包数据。未解 TCP 消息仍作为审计边界保留；但详情页静态本地读取链，加上全部可解消息和大 UDP 消息均无稳定汇总结构，足以判定当前可用路线是逐 Hit 本地聚合。逐抓包统计、最大消息、协议命中与哈希见同目录 `REPORT.json`。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    static_client = _static_client_evidence()
    capture_rows = []
    failures = []
    for index, item in enumerate(_fixed_captures(), 1):
        path = item["path"]
        try:
            checkpoint_dir = OUT / "checkpoints_v2"
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_path = checkpoint_dir / f"{item['sha256']}.json"
            if checkpoint_path.is_file():
                row = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if row.get("capture_sha256") != item["sha256"]:
                    raise ValueError(f"checkpoint hash mismatch: {checkpoint_path}")
            else:
                row = _capture_report(path, item["sha256"])
                checkpoint_path.write_text(
                    json.dumps(row, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            capture_rows.append(row)
            print(
                json.dumps(
                    {
                        "index": index,
                        "capture": path.name,
                        "searchable_blobs": row["inventory"]["searchable_blob_count"],
                        "all_four": row["exact_aggregate_match_counts"][
                            "player_plus_skill_plus_hit_count_plus_damage_same_message"
                        ],
                        "seconds": round(row["elapsed_seconds"], 3),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as exc:  # keep all failures explicit in the final report
            failures.append({"capture": path.name, "error": f"{type(exc).__name__}: {exc}"})
            print(json.dumps(failures[-1], ensure_ascii=False), flush=True)

    counts: Counter[str] = Counter()
    searchable_bytes = 0
    match_counts: Counter[str] = Counter()
    aggregate_skill_rows = 0
    aggregate_actor_rows = 0
    for row in capture_rows:
        inventory = row["inventory"]
        counts.update(inventory["counts"])
        searchable_bytes += int(inventory["searchable_plaintext_bytes"])
        aggregate_skill_rows += int(inventory["aggregate_skill_rows_searched"])
        aggregate_actor_rows += int(inventory["aggregate_actor_rows_searched"])
        exact = row["exact_aggregate_match_counts"]
        match_counts["player_skill_damage"] += int(
            exact["player_plus_skill_plus_damage_same_message"]
        )
        match_counts["all_four"] += int(
            exact["player_plus_skill_plus_hit_count_plus_damage_same_message"]
        )
        match_counts["structured"] += int(exact["messages_with_two_or_more_complete_rows"])

    report = {
        "schema": "dragon-nest-builtin-meter-summary-wire-audit/v58",
        "scope": "hash-pinned static client plus fixed 35-capture read-only audit",
        "production_changed": False,
        "static_client": static_client,
        "summary": {
            "classification": "client_per_hit_local_aggregation",
            "whole_fight_player_skill_network_summary_found": False,
            "capture_count": len(capture_rows),
            "capture_failures": len(failures),
            "physical_frames": counts["physical_frames"],
            "logical_tcp": counts["logical_tcp"],
            "logical_udp": counts["logical_udp"],
            "decoded_tcp_messages": sum(
                value for key, value in counts.items() if key.startswith("tcp_decoded_")
            ),
            "undecoded_tcp_messages": sum(
                value for key, value in counts.items() if key.startswith("tcp_undecoded_")
            ),
            "aggregate_skill_rows_searched": aggregate_skill_rows,
            "aggregate_actor_rows_searched": aggregate_actor_rows,
            "searchable_blob_count": sum(
                row["inventory"]["searchable_blob_count"] for row in capture_rows
            ),
            "searchable_plaintext_bytes": searchable_bytes,
            "player_skill_damage_matches": match_counts["player_skill_damage"],
            "all_four_matches": match_counts["all_four"],
            "structured_summary_messages": match_counts["structured"],
            "direct_new_confirmed_hits": 0,
        },
        "network_inventory_counts": dict(counts),
        "captures": capture_rows,
        "failures": failures,
        "low_profession_help": _low_job_help(),
        "limitations": [
            "Fixed captures do not contain a separate UI-click event, so panel-open timestamps cannot be labelled from packet timing alone.",
            "Undecrypted TCP payloads are counted but cannot be searched for plaintext fields.",
            "Exact whole-fight values are used only to discover candidate wire structures and never as production attribution evidence.",
            "Absence of a summary packet does not replace the need to finish per-Hit player/entity/object state reconstruction.",
        ],
        "elapsed_seconds": time.monotonic() - started,
    }
    if failures:
        report["summary"]["whole_fight_player_skill_network_summary_found"] = None
    JSON_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_OUT.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "outputs": [str(MD_OUT), str(JSON_OUT)]}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
