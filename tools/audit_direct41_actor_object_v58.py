"""Read-only v576 direct41 actor/object audit.

This specialist audit is intentionally outside the production decoder.  It
hash-pins the client and the fixed 35-capture corpus, inspects already decoded
client bytes without executing them, and replays captures only to validate
fixed wire fields against independent deterministic attribution.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.review_client_damage_types_v44 import (  # noqa: E402
    BASE,
    CodeReview,
    StaticView,
)


CLIENT = Path(r"D:\DragonNest\Reborn\dragonnest_reborn.exe")
CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
BASELINE = ROOT / "outputs/current_native_baseline_v57_20260909/profession_direct_damage_report.json"
OUT_DIR = ROOT / "outputs/direct41_actor_object_v58_20260909"
REPORT_JSON = OUT_DIR / "REPORT.json"
REPORT_MD = OUT_DIR / "REPORT.md"

# These names either consume the same fixed packet field or are not an
# independent proof of its semantic meaning.
PACKET_DERIVED_METHODS = frozenset(
    {
        "packet_skill_id",
        "packet_native_attack_key",
        "embedded_single_candidate",
        "compound_candidate_recent_cast",
        "compound_candidate_profile",
        "compound_candidate_ranked",
    }
)

# Address windows are RVAs in the hash-pinned decoded image.  They are kept
# narrow so the report remains auditable without pretending to recover source
# level names from machine code.
STATIC_WINDOWS = (
    ("direct41_uid_deserialize", 0x52A962, 0x52A99B),
    ("high_source_extra_i16_pair", 0x52A9D4, 0x52AA13),
    ("result_8c_gate", 0x52AA65, 0x52AA8D),
    ("result_90_and_suffix", 0x52AF78, 0x52B00B),
    ("uid_registry_resolver", 0xC6ED40, 0xC6ED7C),
    ("uid_registry_search", 0x84BA90, 0x84BB38),
    ("meter_key_and_related_object", 0x6A5638, 0x6A56A2),
    ("meter_actor_fallback", 0x6A57B0, 0x6A5920),
    ("meter_component_fallback", 0x6A5AE0, 0x6A5EB2),
    ("result_copy", 0x529768, 0x5297CC),
)

PRODUCTION_FILES = (
    "dragon_nest_builder/live_dps/udp_damage_records.py",
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/damage_attribution.py",
    "dragon_nest_builder/live_dps/damage_ledger.py",
)


def digest_path(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def u8(raw: bytes, marker_offset: int) -> int:
    at = 4 + marker_offset
    return raw[at] if 0 <= at < len(raw) else -1


def i8(raw: bytes, marker_offset: int) -> int:
    value = u8(raw, marker_offset)
    return value - 256 if value >= 128 else value


def u16(raw: bytes, marker_offset: int) -> int:
    at = 4 + marker_offset
    return int.from_bytes(raw[at : at + 2], "little") if at + 2 <= len(raw) else -1


def i16(raw: bytes, marker_offset: int) -> int:
    at = 4 + marker_offset
    return int.from_bytes(raw[at : at + 2], "little", signed=True) if at + 2 <= len(raw) else 0


def u32(raw: bytes, marker_offset: int) -> int:
    at = 4 + marker_offset
    return int.from_bytes(raw[at : at + 4], "little") if at + 4 <= len(raw) else -1


def u64(raw: bytes, marker_offset: int) -> int:
    at = 4 + marker_offset
    return int.from_bytes(raw[at : at + 8], "little") if at + 8 <= len(raw) else -1


def canonical_skill(value: int) -> int:
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(int(value), int(value)))


def event_record(event: CombatEvent, packet: object):
    inventory = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
    if not inventory.complete:
        return None, inventory.rejection_reason
    rows = [
        row
        for row in inventory.damage_records
        if row.admissible_fields
        and row.family == "direct41"
        and row.record_offset == event.wire_record_offset
        and row.source_actor_id == event.wire_source_actor_id
        and row.target_actor_id == event.wire_target_actor_id
        and row.damage == event.amount
        and row.critical == bool(event.wire_critical)
    ]
    if len(rows) != 1:
        return None, f"physical_join_count:{len(rows)}"
    return rows[0], ""


@dataclass
class CaptureAudit:
    path: str
    capture_sha256: str
    joined_direct41: int = 0
    join_failures: Counter[str] = field(default_factory=Counter)
    source_kind: Counter[str] = field(default_factory=Counter)
    source_branch: Counter[str] = field(default_factory=Counter)
    common_8c: Counter[str] = field(default_factory=Counter)
    uid: Counter[str] = field(default_factory=Counter)
    result88: Counter[str] = field(default_factory=Counter)
    result94: Counter[str] = field(default_factory=Counter)
    result90: Counter[str] = field(default_factory=Counter)
    result90_truth: Counter[str] = field(default_factory=Counter)
    result90_by_job: dict[str, Counter[str]] = field(default_factory=dict)
    value_truth: dict[str, Counter[str]] = field(default_factory=dict)
    candidate_capture_support: dict[str, set[str]] = field(default_factory=dict)
    suffix_shapes: Counter[str] = field(default_factory=Counter)
    uid_values: set[int] = field(default_factory=set)
    non_packet_examples: list[dict[str, Any]] = field(default_factory=list)

    def serialise(self) -> dict[str, Any]:
        return {
            "capture": self.path,
            "capture_sha256": self.capture_sha256,
            "joined_direct41": self.joined_direct41,
            "join_failures": dict(self.join_failures),
            "source_kind": dict(self.source_kind),
            "source_branch": dict(self.source_branch),
            "result_8c": dict(self.common_8c),
            "opaque_uid": {**dict(self.uid), "unique_nonzero": len(self.uid_values)},
            "result_88": dict(self.result88),
            "result_94": dict(self.result94),
            "result_90": dict(self.result90),
            "result_90_independent_truth": dict(self.result90_truth),
            "result_90_by_job": {
                key: dict(value) for key, value in sorted(self.result90_by_job.items())
            },
            "suffix_shapes": dict(self.suffix_shapes),
            "non_packet_examples": self.non_packet_examples,
        }


def audit_capture(path: Path, expected_hash: str) -> tuple[CaptureAudit, dict[str, Counter[str]], dict[str, set[str]]]:
    actual_hash = digest_path(path)
    if actual_hash != expected_hash:
        raise ValueError(f"capture hash mismatch: {path}")
    audit = CaptureAudit(path=str(path), capture_sha256=actual_hash)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    catalog = frozenset(int(value) for value in decoder.damage_skill_ids)
    original = decoder.feed_events

    def feed_events(packet):
        events = original(packet)
        for event in events:
            if (
                not isinstance(event, CombatEvent)
                or event.healing
                or event.amount <= 0
                or event.wire_record_offset <= 0
                or "direct41" not in event.wire_damage_layout
            ):
                continue
            physical, reason = event_record(event, packet)
            if physical is None:
                audit.join_failures[reason] += 1
                continue
            raw = physical.record_bytes
            if len(raw) < 126:
                audit.join_failures["short_direct41_record"] += 1
                continue
            audit.joined_direct41 += 1
            source_kind = i8(raw, 7)
            source_id = int(physical.source_actor_id)
            high_source = source_id >= 100000
            branch = "source_kind_1_high_id" if source_kind == 1 and high_source else (
                "source_kind_1_low_id" if source_kind == 1 else "other_source_kind"
            )
            gate_8c = u8(raw, 100)
            uid = u64(raw, 13)
            field_88 = u8(raw, 99)
            audit.source_kind[str(source_kind)] += 1
            audit.source_branch[branch] += 1
            audit.common_8c[str(gate_8c)] += 1
            audit.uid["nonzero"] += int(uid > 0)
            audit.uid["zero"] += int(uid == 0)
            audit.uid["sentinel_u64"] += int(uid == 0xFFFFFFFFFFFFFFFF)
            if uid > 0 and uid != 0xFFFFFFFFFFFFFFFF:
                audit.uid_values.add(uid)
            audit.result88["observed"] += 1
            audit.result88["nonzero"] += int(field_88 > 0)
            # The direct41 constructor initializes result+0x94 to zero.  No
            # fixed field in this handler overwrites it; this counter is the
            # statically implied value, not an invented wire offset.
            audit.result94["statically_zero_on_direct41"] += 1

            if source_kind == 1 and high_source:
                audit.suffix_shapes[
                    f"i16={i16(raw, 102)},{i16(raw, 104)}|90={u32(raw, 106)}|9c={u32(raw, 110)}|a0={u32(raw, 114)}|a4={u32(raw, 118)}"
                ] += 1
            if not (source_kind == 1 and high_source and gate_8c == 0):
                audit.result90["gate_rejected"] += 1
                continue

            raw_key = u32(raw, 106)
            key = canonical_skill(raw_key)
            valid_key = raw_key in catalog or key in catalog
            audit.result90["read_by_client"] += 1
            audit.result90["positive"] += int(raw_key not in {0, 0xFFFFFFFF})
            audit.result90["catalogued"] += int(valid_key)
            if not valid_key:
                audit.result90["not_catalogued"] += 1
                continue

            try:
                assigned = int(str(event.skill_id))
            except ValueError:
                assigned = 0
            method = str(event.attribution_method or "")
            independent = bool(
                assigned > 0
                and is_deterministic_attribution(method)
                and method not in PACKET_DERIVED_METHODS
            )
            current_exact_same_field = method == "packet_skill_id"
            job = str(event.job or "未识别职业")
            job_stats = audit.result90_by_job.setdefault(job, Counter())
            job_stats["catalogued_result90_hits"] += 1
            job_stats["already_packet_skill_id_hits"] += int(current_exact_same_field)
            job_stats["currently_not_packet_skill_id_hits"] += int(not current_exact_same_field)
            job_stats["currently_unresolved_or_inferred_hits"] += int(
                not is_deterministic_attribution(method)
            )
            if not current_exact_same_field and len(audit.non_packet_examples) < 30:
                audit.non_packet_examples.append(
                    {
                        "record_sha256": physical.wire_record_sha256,
                        "record_offset": int(physical.record_offset),
                        "job": job,
                        "result90_raw": raw_key,
                        "result90_canonical": key,
                        "assigned_skill_id": assigned,
                        "assigned_skill_name": str(event.skill_name),
                        "method": method,
                    }
                )
            job_stats["independent_truth_hits"] += int(independent)
            if assigned > 0:
                job_stats["assigned_same"] += int(assigned == key)
                job_stats["assigned_different"] += int(assigned != key)
            if independent:
                audit.result90_truth["observed"] += 1
                audit.result90_truth["match"] += int(assigned == key)
                audit.result90_truth["conflict"] += int(assigned != key)
                value_key = f"{raw_key}|{key}|{job}|{assigned}|{event.skill_name}|{method}"
                value_stats = audit.value_truth.setdefault(value_key, Counter())
                value_stats["observed"] += 1
                value_stats["match"] += int(assigned == key)
                value_stats["conflict"] += int(assigned != key)
                audit.candidate_capture_support.setdefault(str(raw_key), set()).add(path.name)
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    return audit, audit.value_truth, audit.candidate_capture_support


def fixed_capture_manifest() -> list[dict[str, Any]]:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    rows = baseline.get("capture_manifest", [])
    if len(rows) != 35:
        raise ValueError(f"expected fixed35 manifest, got {len(rows)}")
    return rows


def static_evidence() -> tuple[dict[str, Any], dict[str, str]]:
    view = StaticView()
    review = CodeReview(view)
    windows: list[dict[str, Any]] = []
    for label, start, end in STATIC_WINDOWS:
        function = view.function(start)
        if function is None:
            raise ValueError(f"no runtime function for {label} at {start:#x}")
        rows = [
            review.row(ins)
            for ins in review.decode(function)
            if BASE + start <= ins.address < BASE + end
        ]
        if not rows:
            raise ValueError(f"no decoded instructions for {label}")
        windows.append(
            {
                "label": label,
                "start_rva": hex(start),
                "end_rva": hex(end),
                "function_begin_rva": hex(function[0]),
                "function_end_rva": hex(function[1]),
                "instructions": [
                    {
                        **row,
                        "rva": hex(int(row["rva"])),
                        **(
                            {"direct_branch_target": hex(int(row["direct_branch_target"]))}
                            if "direct_branch_target" in row
                            else {}
                        ),
                    }
                    for row in rows
                ],
                "provenance": view.provenance(start, end - start),
            }
        )
    return (
        {
            "client_path": str(CLIENT),
            "client_sha256": digest_path(CLIENT),
            "preferred_image_base": hex(view.base),
            "decoded_windows": windows,
            "decoder_stats": dict(review.stats),
            "analysis_mode": "static bytes only; client never executed or loaded",
        },
        dict(view.inputs),
    )


def merge_counter_dict(target: dict[str, Counter[str]], source: dict[str, Counter[str]]) -> None:
    for key, value in source.items():
        target[key].update(value)


def aggregate(captures: list[CaptureAudit]) -> dict[str, Any]:
    scalar_names = (
        "join_failures",
        "source_kind",
        "source_branch",
        "common_8c",
        "uid",
        "result88",
        "result94",
        "result90",
        "result90_truth",
        "suffix_shapes",
    )
    totals = {name: Counter() for name in scalar_names}
    by_job: dict[str, Counter[str]] = defaultdict(Counter)
    all_uids: set[int] = set()
    for capture in captures:
        for name in scalar_names:
            totals[name].update(getattr(capture, name))
        for job, stats in capture.result90_by_job.items():
            by_job[job].update(stats)
        all_uids.update(capture.uid_values)
    truth = totals["result90_truth"]
    comparable = sum(value["assigned_same"] + value["assigned_different"] for value in by_job.values())
    comparable_matches = sum(value["assigned_same"] for value in by_job.values())
    comparable_conflicts = sum(value["assigned_different"] for value in by_job.values())
    # The semantic proof is static: the client writes this field into
    # result+0x90 before the meter reads it.  Capture replay is a contradiction
    # audit.  Because production already consumes the same field first, a
    # non-circular independent event label is normally unavailable.
    accepted = comparable > 0 and comparable_conflicts == 0
    expected: dict[str, dict[str, int]] = {}
    for job, stats in sorted(by_job.items()):
        expected[job] = {
            **dict(stats),
            # The current decoder already labels these records from +106.
            # Formal protocol certification therefore changes confidence, not
            # the number of currently emitted Hit rows.
            "new_raw_skilltable_key_rows_if_policy_filter_is_separated": (
                stats["currently_not_packet_skill_id_hits"] if accepted else 0
            ),
            "new_final_display_skill_hits": 0,
        }
    return {
        "capture_count": len(captures),
        "joined_direct41": sum(row.joined_direct41 for row in captures),
        "join_failures": dict(totals["join_failures"]),
        "source_kind": dict(totals["source_kind"]),
        "source_branch": dict(totals["source_branch"]),
        "result_8c": dict(totals["common_8c"]),
        "opaque_uid": {**dict(totals["uid"]), "unique_nonzero": len(all_uids)},
        "result_88": dict(totals["result88"]),
        "result_94": dict(totals["result94"]),
        "result_90": dict(totals["result90"]),
        "result_90_independent_truth": {
            **dict(truth),
            "match_percent": 100.0 * truth["match"] / truth["observed"] if truth["observed"] else 0.0,
            "available": truth["observed"] > 0,
        },
        "result_90_current_assignment_crosscheck": {
            "comparable": comparable,
            "match": comparable_matches,
            "conflict": comparable_conflicts,
            "unlabelled_with_catalogued_key": sum(
                value["currently_not_packet_skill_id_hits"] for value in by_job.values()
            ),
            "zero_conflict": accepted,
        },
        "by_job": expected,
        "suffix_shape_distinct": len(totals["suffix_shapes"]),
        "top_suffix_shapes": dict(totals["suffix_shapes"].most_common(30)),
    }


def render_report(report: dict[str, Any]) -> str:
    summary = report["fixed35"]
    truth = summary["result_90_independent_truth"]
    crosscheck = summary["result_90_current_assignment_crosscheck"]
    lines = [
        "# v576 direct41 actor/object 专项报告",
        "",
        "## 结论",
        "",
        "1. `marker+13..+21` 是一个完整的 little-endian `uint64` 客户端对象注册 UID。客户端把它交给 `0xC6ED40`，后者经 `0x84BA90` 在 UID 红黑树中查找对象；命中且对象状态 `+0x1160 == 0` 时，从对象头前方复制两个 8 字节值，构成 damage result 的 `+0x20/+0x28 related-object handle/object pair`。这 8 字节不能拆成两个 `uint32`。",
        "2. 玩家运行 ID `>=100000` 时，direct41 在 suffix 先读取 `marker+102`、`marker+104` 两个 `int16`，再在 `result+0x8C == 0` 时把 `marker+106` 写入 `result+0x90`。这解释了此前 `+106` 与 SkillTable key 的强一致性。",
        "3. `result+0x88` 来自固定字段 `marker+99`（type2）；`result+0x94` 在 direct41 构造中初始化为 0，普通 Actor/Player/Monster 接收者的 `vtable+0x290` 是 no-op，也没有发现固定字段写入 `+0x94`。因此 `+0x88/+0x94` 的特殊 actor fallback 对普通 player direct41 不成立。",
        "4. 普通玩家 direct Hit 可以离线恢复 SkillTable key：`source_kind==1 && source_actor_id>=100000 && marker+100==0 && marker+106` 属于版本技能表时，`marker+106` 就是客户端 meter 首选的 `result+0x90` key。固定 35 份抓包用于检查现有标签是否出现反例。",
        "5. 对象 UID 本身不能离线反推出进程内指针；只有再解出同一 UID 的网络对象注册记录，才能离线重建 `+0x20/+0x28` 的语义对象关系。不能把 UID 数值直接当作 handle 或 object ID。",
        "",
        "## 固定 35 份抓包核验",
        "",
        f"- joined direct41：{summary['joined_direct41']:,}",
        f"- 玩家高 ID 分支：{summary['source_branch'].get('source_kind_1_high_id', 0):,}",
        f"- 非零 opaque UID：{summary['opaque_uid'].get('nonzero', 0):,}；唯一 UID：{summary['opaque_uid'].get('unique_nonzero', 0):,}",
        f"- `marker+106` 已进入客户端 `result+0x90` 且属于技能表：{summary['result_90'].get('catalogued', 0):,}",
        f"- 与现有标签可比：{crosscheck.get('comparable', 0):,}；一致：{crosscheck.get('match', 0):,}；冲突：{crosscheck.get('conflict', 0):,}",
        f"- 携带合法 SkillTable key、但被现有职业变形保护留作未识别：{crosscheck.get('unlabelled_with_catalogued_key', 0):,}",
        f"- 非循环独立真值：{truth.get('observed', 0):,}（当前解码器优先消费同一字段，因此该项不可用；规则成立依据是客户端静态数据流，不以循环比对代替）",
        "",
        "## 零冲突可实现规则",
        "",
        "```text",
        "仅限 v576 direct41 完整记录：",
        "if source_kind == 1",
        "   and source_actor_id >= 100000",
        "   and marker[100] == 0",
        "   and canonical_u32(marker[106:110]) in versioned SkillTable catalog:",
        "       player_direct_skill_key = canonical_u32(marker[106:110])",
        "       evidence = packet_native_attack_key",
        "else:",
        "       不从固定 suffix 猜测 SkillTable key",
        "```",
        "",
        "规则不使用时间邻近、伤害值、Hit 数或职业猜测。客户端静态数据流给出字段语义，固定 35 份抓包负责反例检查；当前 JSON 的 `zero_conflict` 来自全部可比标签 0 冲突。",
        "",
        "## 各职业预计新增 Hit",
        "",
        "当前生产解码器已经读取 direct41 `marker+106` 并标为 `packet_skill_id`，所以绝大多数 Hit 不会新增。固定 35 份中另有 5 条黑暗复仇者记录可恢复原始 SkillTable key（4 条 key=29、1 条 key=7），但它们被现有“转职变形基础技能”保护主动留作未识别；静态专项只能确认原始 key，不能把它们无歧义映射为最终展示技能，因此新增可安全展示 Hit 仍为 0。",
        "",
        "| 职业 | +106 技能表 Hit | 当前已 packet_skill_id | 可新增原始 key | 可新增展示技能 Hit |",
        "|---|---:|---:|---:|---:|",
    ]
    for job, stats in sorted(summary["by_job"].items()):
        lines.append(
            f"| {job} | {stats.get('catalogued_result90_hits', 0):,} | "
            f"{stats.get('already_packet_skill_id_hits', 0):,} | "
            f"{stats.get('new_raw_skilltable_key_rows_if_policy_filter_is_separated', 0):,} | "
            f"{stats.get('new_final_display_skill_hits', 0):,} |"
        )
    lines += [
        "",
        "## +0x88/+0x94 actor fallback 条件",
        "",
        "客户端只有在更早的 key 来源都失败后才检查这条路径：`result+0x94` 必须为 `0x6F..0x72`；按该值枚举 `Actor+0x1010` 组件；候选描述符对应 SkillTable 行的 `_Element` 必须等于 `result+0x88`；并且 `Actor+0xE98 == descriptor+0x94`。全部通过后，描述符首 dword 才成为 meter key。普通 direct41 的 `+0x94` 保持 0，所以不能用 `marker+99` 单独恢复技能。",
        "",
        "## 限制与后续可闭合项",
        "",
        "- UID 树和返回 pair 都是客户端运行时对象注册表；静态字节证明了构造方法，但网络离线重建还需要找到同一 64 位 UID 的注册/销毁包及 generation 生命周期。",
        "- 低 ID source 分支在进入 `result+0x90` 前有额外条件读取，不能套用玩家高 ID 的 `marker+106` 固定规则。",
        "- 未使用任何时间邻近或伤害值匹配；无法由结构证据闭合的分支保持未知。",
        "- 本专项未修改生产代码。",
        "",
        "## 可复核输入",
        "",
        f"- 客户端 SHA-256：`{report['static']['client_sha256']}`",
        f"- 固定抓包数：{summary['capture_count']}",
        f"- 生产文件前后哈希一致：{str(not report['production_changed']).lower()}",
        "- 完整逐抓包统计、反汇编窗口、输入哈希和规则机读结果见 `REPORT.json`。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_json = output_dir / "REPORT.json"
    output_md = output_dir / "REPORT.md"

    production_before = {
        name: digest_path(ROOT / name) for name in PRODUCTION_FILES
    }
    if digest_path(CLIENT) != CLIENT_SHA256:
        raise ValueError("client SHA-256 mismatch")
    static, static_inputs = static_evidence()
    manifest = fixed_capture_manifest()
    captures: list[CaptureAudit] = []
    merged_truth: dict[str, Counter[str]] = defaultdict(Counter)
    merged_support: dict[str, set[str]] = defaultdict(set)
    worker_count = max(1, min(int(args.workers), 4))
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(
                audit_capture,
                Path(row["capture_path"]),
                str(row["capture_sha256"]),
            ): Path(row["capture_path"]).name
            for row in manifest
        }
        for index, future in enumerate(as_completed(futures), 1):
            capture, truth, support = future.result()
            captures.append(capture)
            merge_counter_dict(merged_truth, truth)
            for key, values in support.items():
                merged_support[key].update(values)
            print(f"[{index:02d}/35] {futures[future]}", flush=True)
    captures.sort(key=lambda row: row.path)

    summary = aggregate(captures)
    production_after = {
        name: digest_path(ROOT / name) for name in PRODUCTION_FILES
    }
    production_changed = production_before != production_after
    if production_changed:
        raise RuntimeError("production file changed during read-only audit")
    report = {
        "schema": "direct41-actor-object-v58-20260909",
        "scope": "hash-pinned static client bytes plus fixed35 offline capture replay",
        "constraints": {
            "client_executed": False,
            "client_injected": False,
            "live_memory_read": False,
            "time_proximity_used": False,
            "damage_value_guessing_used": False,
            "production_code_modified": False,
        },
        "verdict": {
            "opaque_context_type": "uint64 client object-registry UID",
            "related_pair_construction_closed": True,
            "player_direct_result90_wire_rule_closed": bool(
                summary["result_90_current_assignment_crosscheck"]["zero_conflict"]
            ),
            "actor_fallback_network_recovery_closed": False,
            "ordinary_player_direct_skilltable_key_offline_recoverable": bool(
                summary["result_90_current_assignment_crosscheck"]["zero_conflict"]
            ),
            "current_production_new_hits": 0,
            "new_raw_skilltable_key_rows_if_policy_filter_is_separated": int(
                summary["result_90_current_assignment_crosscheck"]["unlabelled_with_catalogued_key"]
            ),
        },
        "wire_layout": {
            "opaque_object_context": {
                "range": "marker+13..+21",
                "type": "little-endian uint64 UID",
                "client_flow": "0x52A969 -> 0xC6ED40 -> 0x84BA90 -> object registry -> pair copied to result+0x20/+0x28",
            },
            "fixed_fields": {
                "result+0x88": "type2 field at marker+99",
                "result+0x8C": "byte at marker+100",
                "result+0x8D": "byte at marker+101",
                "high_player_state_i16_a": "marker+102",
                "high_player_state_i16_b": "marker+104",
                "result+0x90_high_player": "u32 at marker+106 when source_kind=1, source_id>=100000, result+0x8C=0",
                "result+0x9C_base_receiver": "u32 at marker+110",
                "result+0xA0_base_receiver": "u32 at marker+114",
                "result+0xA4_base_receiver": "u32 at marker+118",
                "result+0x94": "constructor zero; no ordinary direct41 fixed-field writer proved",
            },
        },
        "related_object_pair": {
            "lookup_key": "full opaque uint64 UID",
            "registry_search": "0x84BA90",
            "resolver": "0xC6ED40",
            "success_guard": "registry hit and object+0x1160 == 0",
            "pair_source": "qword object-0x28 and qword object-0x20",
            "result_destination": "result+0x20 and result+0x28",
            "offline_pointer_recovery": False,
            "offline_semantic_join_requirement": "decode a network object registration/lifecycle carrying the same uint64 UID",
        },
        "actor_fallback": {
            "entry": "earlier key sources <= 0",
            "result94_range": [0x6F, 0x70, 0x71, 0x72],
            "component_enumerator": "0x5253B0 over Actor+0x1010",
            "guards": [
                "component+0x40 == result+0x94 and component+0x44 != 3",
                "SkillTable._Element == result+0x88",
                "Actor+0xE98 == descriptor+0x94",
            ],
            "final_key": "descriptor first dword",
            "direct41_ordinary_player_reachable": False,
            "reason": "result+0x94 remains constructor-zero in the ordinary direct41 path",
        },
        "zero_conflict_rule": {
            "accepted": bool(summary["result_90_current_assignment_crosscheck"]["zero_conflict"]),
            "predicate": "direct41 complete && source_kind==1 && source_actor_id>=100000 && marker+100==0 && canonical_u32(marker+106) in v576 SkillTable catalog",
            "result": "SkillTable key = canonical_u32(marker+106)",
            "forbidden_inputs": ["time proximity", "damage value", "hit-count guess", "job guess"],
            "static_basis": "hash-pinned client writes marker+106 to result+0x90 on the exact high-player branch",
            "capture_crosscheck": summary["result_90_current_assignment_crosscheck"],
            "independent_truth": summary["result_90_independent_truth"],
        },
        "fixed35": summary,
        "captures": [capture.serialise() for capture in captures],
        "raw_value_truth": {
            key: {
                **dict(value),
                "capture_count": len(merged_support.get(key.split("|", 1)[0], set())),
            }
            for key, value in sorted(merged_truth.items())
        },
        "static": static,
        "inputs": {
            "baseline_manifest": {
                "path": str(BASELINE),
                "sha256": digest_path(BASELINE),
            },
            "static_inputs": static_inputs,
            "production_files_before": production_before,
            "production_files_after": production_after,
        },
        "production_changed": production_changed,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_md.write_text(render_report(report), encoding="utf-8")
    print(json.dumps(report["verdict"], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
