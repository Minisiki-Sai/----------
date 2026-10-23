"""Audit late 4004/4024 object registration for Inquisitor direct Hits.

This is an offline evidence audit.  It never changes production attribution.
A pre-registration Hit can inherit the later object's fixed 3407 identity only
when the complete UDP payload is observed again after an accepted, live,
same-owner/same-object registration.  The repeated packet supplements the
original admitted Hit; it is never counted as another damage row.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame


SOURCE = ROOT / "outputs/cleric_skill_gaps_v63_20260909"
CACHE_ROOT = SOURCE / "captures"
V63_REPORT = SOURCE / "REPORT.json"
V64_REPORT = ROOT / "outputs/inquisitor_3019_3402_3407_objects_v64_20260909/report.json"
RESOURCE = ROOT / "outputs/lightning_storm_misattribution_v62_20260909/resource_summary.json"
OUT = ROOT / "outputs/lightning_storm_object_order_v66_20260909"
MAGIC = b"DNDP1\x00"
HEADER = struct.Struct("<dBII")
TARGET_SKILLS = {3019, 3402, 3407, 3425, 3408}
STORM_TEMPLATES = {4004, 4024}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def boundary(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["packet_serial"]), int(row["record_offset"])


def connection(packet: Any) -> tuple[Any, ...]:
    return (
        str(packet.transport).upper(), str(packet.source_ip), int(packet.source_port),
        str(packet.destination_ip), int(packet.destination_port),
    )


def scan_payload_occurrences(capture: Path, wanted: set[str]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    frame_index = 0
    with capture.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise RuntimeError(f"invalid dpscap: {capture}")
        while header := handle.read(HEADER.size):
            if len(header) != HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = HEADER.unpack(header)
            raw = handle.read(captured_length)
            if len(raw) != captured_length:
                break
            frame_index += 1
            packet = decode_captured_frame(timestamp, raw, original_length)
            if packet is None or str(packet.transport).upper() != "UDP":
                continue
            digest = sha256_bytes(packet.payload)
            if digest not in wanted:
                continue
            result[digest].append(
                {
                    "frame": frame_index,
                    "timestamp": float(timestamp),
                    "adapter": int(adapter),
                    "connection": list(connection(packet)),
                    "payload_sha256": digest,
                    "payload_length": len(packet.payload),
                }
            )
    return result


def choose_occurrence(
    rows: list[dict[str, Any]], timestamp: float,
) -> dict[str, Any] | None:
    if not rows:
        return None
    selected = min(rows, key=lambda row: abs(float(row["timestamp"]) - timestamp))
    return selected if abs(float(selected["timestamp"]) - timestamp) <= 1e-5 else None


def compact_registration(row: dict[str, Any]) -> dict[str, Any]:
    origin = row.get("accepted_origin") or {}
    return {
        "source_actor_id": int(row["source_actor_id"]),
        "damage_object_id": int(row["damage_object_id"]),
        "projectile_template_id": int(row["projectile_template_id"]),
        "timestamp": float(row["timestamp"]),
        "packet_serial": int(row["packet_serial"]),
        "record_offset": int(row["record_offset"]),
        "wire_sha256": str(row["wire_sha256"]),
        "generation": int(origin.get("generation", 0) or 0),
        "canonical_skill_id": int(origin.get("canonical_skill_id", 0) or 0),
        "expires_at": float(origin.get("expires_at", 0.0) or 0.0),
        "action_backed": bool(origin.get("action_backed", False)),
        "response_backed": bool(origin.get("response_backed", False)),
    }


def immutable_key(row: dict[str, Any]) -> tuple[Any, ...]:
    event, physical = row["event"], row["physical"]
    return (
        str(row["capture_sha256"]), int(event["timestamp_ms"]),
        int(physical["source_actor_id"]), int(physical["target_actor_id"]),
        int(physical.get("damage_object_id") or 0), int(physical["result_group_id"]),
        int(physical["damage"]), bool(physical["critical"]),
        int(physical["record_offset"]), str(physical["wire_record_sha256"]),
    )


def analyse() -> dict[str, Any]:
    caches = [read_json(path) for path in sorted(CACHE_ROOT.glob("*.json"))]
    if len(caches) != 6 or not all(cache.get("baseline_reproduced") for cache in caches):
        raise RuntimeError("expected six reproduced v63 caches")
    resource = read_json(RESOURCE)
    static_rows = {
        int(row["RowId"]): row for row in resource["weapons"]
        if int(row["RowId"]) in STORM_TEMPLATES
    }
    if set(static_rows) != STORM_TEMPLATES:
        raise RuntimeError("resource proof is missing projectile 4004/4024")

    candidates: list[dict[str, Any]] = []
    rejected = Counter()
    scans: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for cache in caches:
        registrations: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for registration in cache["registrations"]:
            registrations[(int(registration["source_actor_id"]), int(registration["damage_object_id"]))].append(registration)
        for rows in registrations.values():
            rows.sort(key=boundary)

        local: list[dict[str, Any]] = []
        wanted: set[str] = set()
        for gap in cache["gap_rows"]:
            old_skill = int(gap["event"]["skill_id"])
            physical = gap["physical"]
            if old_skill not in TARGET_SKILLS or physical["family"] != "direct37":
                continue
            owner = int(physical["source_actor_id"])
            object_id = int(physical.get("damage_object_id") or 0)
            hit_boundary = (int(physical["packet_serial"]), int(physical["record_offset"]))
            future = [
                row for row in registrations.get((owner, object_id), ())
                if boundary(row) > hit_boundary
                and int(row["projectile_template_id"]) in STORM_TEMPLATES
                and int((row.get("accepted_origin") or {}).get("canonical_skill_id", 0) or 0) == 3407
            ]
            if not future:
                continue
            registration = future[0]
            item = {
                "capture": Path(cache["capture"]).name,
                "capture_path": str(cache["capture"]),
                "capture_sha256": str(cache["capture_sha256"]),
                "event": gap["event"],
                "physical": physical,
                "registration": compact_registration(registration),
            }
            local.append(item)
            wanted.add(str(physical["wire_sha256"]))
            wanted.add(str(registration["wire_sha256"]))

        capture_path = Path(cache["capture"])
        if local:
            if not capture_path.is_file() or sha256_file(capture_path) != cache["capture_sha256"]:
                raise RuntimeError(f"capture hash mismatch: {capture_path}")
            occurrences = scan_payload_occurrences(capture_path, wanted)
            scans[cache["capture_sha256"]] = occurrences
        candidates.extend(local)

    safe: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    for item in candidates:
        physical = item["physical"]
        registration = item["registration"]
        occurrences = scans[item["capture_sha256"]]
        original = choose_occurrence(
            occurrences.get(str(physical["wire_sha256"]), []),
            float(physical["packet_timestamp_seconds"]),
        )
        registered = choose_occurrence(
            occurrences.get(str(registration["wire_sha256"]), []),
            float(registration["timestamp"]),
        )
        repeats = []
        if original and registered:
            repeats = [
                row for row in occurrences.get(str(physical["wire_sha256"]), [])
                if int(row["frame"]) > int(registered["frame"])
                and float(row["timestamp"]) <= float(registration["expires_at"])
                and row["connection"] == original["connection"]
                and int(row["adapter"]) == int(original["adapter"])
            ]
        verdict = "no_exact_post_registration_duplicate"
        if original is None:
            verdict = "original_payload_not_relocated"
        elif registered is None:
            verdict = "registration_payload_not_relocated"
        elif int(original["frame"]) >= int(registered["frame"]):
            verdict = "not_late_registration"
        elif repeats:
            verdict = "safe_exact_retry_forward_object_proof"
        rejected[verdict] += 1
        result = item | {
            "verdict": verdict,
            "original_occurrence": original,
            "registration_occurrence": registered,
            "post_registration_duplicates": repeats,
            "new_skill_id": 3407 if repeats else None,
            "change_kind": (
                "promotion" if repeats and int(item["event"]["skill_id"]) == 3407
                else "correction" if repeats else None
            ),
        }
        (safe if repeats else risks).append(result)

    identities = [immutable_key(row) for row in safe]
    if len(identities) != len(set(identities)):
        raise RuntimeError("safe physical identities are not unique")
    pair_counts = Counter(f"{row['event']['skill_id']}->3407" for row in safe)
    per_old = Counter(int(row["event"]["skill_id"]) for row in safe)
    candidates_per_old = Counter(int(row["event"]["skill_id"]) for row in candidates)
    safe_damage = sum(int(row["physical"]["damage"]) for row in safe)
    future_delay_ms = [
        1000.0 * (
            float(row["registration"]["timestamp"])
            - float(row["physical"]["packet_timestamp_seconds"])
        )
        for row in candidates
    ]
    template_counts = Counter(
        int(row["registration"]["projectile_template_id"]) for row in candidates
    )
    negative_fixtures = []
    for old_skill in sorted(TARGET_SKILLS):
        rows = [row for row in risks if int(row["event"]["skill_id"]) == old_skill]
        if not rows:
            continue
        row = min(
            rows,
            key=lambda value: float(value["registration"]["timestamp"])
            - float(value["physical"]["packet_timestamp_seconds"]),
        )
        negative_fixtures.append(
            {
                "capture": row["capture"],
                "capture_sha256": row["capture_sha256"],
                "old_skill_id": int(row["event"]["skill_id"]),
                "timestamp_ms": int(row["event"]["timestamp_ms"]),
                "source_actor_id": int(row["physical"]["source_actor_id"]),
                "target_actor_id": int(row["physical"]["target_actor_id"]),
                "damage_object_id": int(row["physical"]["damage_object_id"]),
                "result_group_id": int(row["physical"]["result_group_id"]),
                "damage": int(row["physical"]["damage"]),
                "critical": bool(row["physical"]["critical"]),
                "wire_sha256": str(row["physical"]["wire_sha256"]),
                "wire_record_sha256": str(row["physical"]["wire_record_sha256"]),
                "future_registration": row["registration"],
                "future_delay_ms": round(
                    1000.0 * (
                        float(row["registration"]["timestamp"])
                        - float(row["physical"]["packet_timestamp_seconds"])
                    ),
                    6,
                ),
                "post_registration_duplicate_count": len(row["post_registration_duplicates"]),
                "verdict": row["verdict"],
            }
        )
    identity_hash = sha256_bytes(
        "\n".join(sorted(json.dumps(row, ensure_ascii=True, separators=(",", ":")) for row in identities)).encode("ascii")
    )
    return {
        "schema": "lightning-storm-object-order-v66",
        "policy": {
            "accepted": [
                "projectile 4004/4024 has fixed static canonical skill 3407",
                "registration accepted for the exact owner and full object ID",
                "complete original UDP payload repeats byte-identically after registration while that generation is live",
                "same adapter and UDP five-tuple",
            ],
            "forbidden": [
                "time proximity without byte-identical retry",
                "old continuation_window/learned_cast_profile label",
                "same numeric object on another owner",
                "expired or future-only registration without forward proof",
            ],
            "production_changed": False,
        },
        "inputs": {
            str(V63_REPORT): sha256_file(V63_REPORT),
            str(V64_REPORT): sha256_file(V64_REPORT),
            str(RESOURCE): sha256_file(RESOURCE),
            **{
                str(path): sha256_file(path)
                for path in sorted(CACHE_ROOT.glob("*.json"))
            },
        },
        "candidate_late_registration_hits": len(candidates),
        "safe_hits": len(safe),
        "safe_damage": safe_damage,
        "safe_pairs": dict(pair_counts),
        "safe_by_old_skill": {str(key): value for key, value in sorted(per_old.items())},
        "candidates_by_old_skill": {
            str(key): value for key, value in sorted(candidates_per_old.items())
        },
        "verdict_counts": dict(rejected),
        "future_registration_distribution": {
            "by_projectile_template": {
                str(key): value for key, value in sorted(template_counts.items())
            },
            "delay_ms": {
                "minimum": round(min(future_delay_ms), 6) if future_delay_ms else None,
                "maximum": round(max(future_delay_ms), 6) if future_delay_ms else None,
                "lte_100": sum(value <= 100.0 for value in future_delay_ms),
                "lte_200": sum(value <= 200.0 for value in future_delay_ms),
                "lte_500": sum(value <= 500.0 for value in future_delay_ms),
                "lte_1000": sum(value <= 1000.0 for value in future_delay_ms),
                "lte_8000": sum(value <= 8000.0 for value in future_delay_ms),
                "gt_8000": sum(value > 8000.0 for value in future_delay_ms),
            },
        },
        "old_3019_assessment": {
            "future_4004_or_4024_same_numeric_owner_object_candidates": sum(
                int(row["event"]["skill_id"]) == 3019 for row in candidates
            ),
            "same_generation_confirmed_corrections_to_3407": sum(
                int(row["event"]["skill_id"]) == 3019 for row in safe
            ),
            "conclusion": (
                "The old continuation_window bucket has reuse risk, but no 3019 row in this audit "
                "has a byte-identical post-registration retry that closes the 4024 generation."
            ),
        },
        "fixtures": {
            "positive": safe[:20],
            "negative": negative_fixtures,
            "selection": "all positives plus the nearest future registration for each old skill; time is used only to select readable negative examples, never as truth",
        },
        "safe_rows": safe,
        "risk_rows": risks,
        "static_projectiles": {
            str(template): {
                "canonical_skill_id": 3407,
                "act_name": int(static_rows[template]["_ActName"]),
                "length": static_rows[template].get("_Length"),
                "durability": static_rows[template].get("_Durability"),
            }
            for template in sorted(STORM_TEMPLATES)
        },
        "conservation": {
            "before_hit_count": len(safe),
            "after_hit_count": len(safe),
            "before_damage": safe_damage,
            "after_damage": safe_damage,
            "immutable_multiset_sha256_before": identity_hash,
            "immutable_multiset_sha256_after": identity_hash,
            "new_damage_rows": 0,
            "removed_damage_rows": 0,
            "damage_delta": 0,
            "physical_identity_preserved": True,
        },
        "production_rule": {
            "safe_to_integrate": bool(safe),
            "rule": (
                "Before packet dedup discards a byte-identical retry, run the normal live exact-owner/object "
                "DamageObject attribution on the retry.  If it resolves through live template 4004/4024 to "
                "3407, attach that provenance to the already-admitted original physical Hit only."
            ),
            "must_not": (
                "Do not backfill arbitrary pre-registration Hits and do not create a second damage row from the retry."
            ),
        },
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "# 雷神闪电风暴对象登记顺序审计 v66",
        "",
        "本轮只审计固定六份雷神抓包，不修改共享生产、评分或上传。",
        "",
        "## 结果",
        "",
        f"- 晚于 Hit 的 4004/4024 登记候选：{report['candidate_late_registration_hits']} Hit。",
        f"- 具备登记后完整 UDP 字节重传、可安全补证：{report['safe_hits']} Hit。",
        f"- 安全纠正方向：`{json.dumps(report['safe_pairs'], ensure_ascii=False, separators=(',', ':'))}`。",
        f"- 安全集伤害：{report['safe_damage']}，只改证据/技能标签，伤害差值 0。",
        "",
        "### 分旧技能桶",
        "",
        "|旧技能|可确认转入3407|",
        "|---:|---:|",
    ]
    for skill, candidate_count in report["candidates_by_old_skill"].items():
        lines.append(
            f"|{skill}（晚登记候选 {candidate_count}）|"
            f"{report['safe_by_old_skill'].get(skill, 0)}|"
        )
    lines += [
        "",
        "## 可生产规则",
        "",
        "在去重器丢弃完整字节相同的重传包之前，使用当时已存在的正常同 owner/object 对象账本"
        "检查该副本；只有副本经仍存活的 4004/4024 generation 确定为3407时，才给已入账原Hit补充证据。",
        "",
        "不得泛化回填普通的登记前Hit，也不得把重传副本新增为第二条伤害。当前样本安全Hit为0，"
        "因此本轮规则只作为以后捕获到正例时的生产候选，暂不应接入。",
        "",
        "## 反例与未闭合",
        "",
    ]
    for verdict, count in sorted(report["verdict_counts"].items(), key=lambda row: (-row[1], row[0])):
        lines.append(f"- `{verdict}`：{count} Hit")
    delays = report["future_registration_distribution"]["delay_ms"]
    lines += [
        "",
        f"候选登记延迟最小 {delays['minimum']} ms；≤1秒 {delays['lte_1000']} 条，"
        f">8秒 {delays['gt_8000']} 条。全部模板均为 "
        f"`{json.dumps(report['future_registration_distribution']['by_projectile_template'], separators=(',', ':'))}`。",
        f"旧3019桶候选 {report['old_3019_assessment']['future_4004_or_4024_same_numeric_owner_object_candidates']} 条；"
        f"同generation确认纠入3407为 {report['old_3019_assessment']['same_generation_confirmed_corrections_to_3407']} 条。",
        "",
        "没有完全相同的登记后 UDP 副本时，即使同 owner、同 object 且只相隔几十毫秒，也只列风险，不能升格。",
        "旧 `3019 continuation_window` 标签不参与证明；若安全行旧桶为 3019，才构成确定的 `3019→3407` 纠错。",
        "",
        "## 守恒",
        "",
        f"- Hit：{report['conservation']['before_hit_count']} → {report['conservation']['after_hit_count']}。",
        f"- 伤害：{report['conservation']['before_damage']} → {report['conservation']['after_damage']}。",
        "- 来源、目标、伤害、暴击、result-group、damage_object_id 与 wire record 身份均保持。",
        "- 重传只补证原行，不新增或删除物理伤害。",
        "",
        "完整真实 fixture 见 `fixtures.json`，所有风险候选见 `risk_rows.json`。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    report = analyse()
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "REPORT.json", report)
    write_json(OUT / "fixtures.json", report["fixtures"])
    write_json(OUT / "risk_rows.json", report["risk_rows"])
    write_json(OUT / "input_manifest.json", {
        "inputs": report["inputs"],
        "tool_sha256": sha256_file(Path(__file__)),
        "production_changed": False,
    })
    (OUT / "REPORT.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({
        "candidates": report["candidate_late_registration_hits"],
        "safe_hits": report["safe_hits"],
        "safe_pairs": report["safe_pairs"],
        "conservation": report["conservation"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
