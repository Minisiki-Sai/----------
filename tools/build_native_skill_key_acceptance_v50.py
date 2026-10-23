"""Build a scoped, zero-conflict native damage skill-key acceptance report.

This tool is deliberately separate from the production decoder.  It replays a
fixed capture selection through ``audit_native_meter_skill_key_v50`` and learns
only from independently deterministic attribution.  It never relabels a hit.

The resulting map is contextual, not a global ``u32 -> skill`` dictionary.  A
key is scoped by logical native field, wire family, source kind, player/entity
source class and job.  The +0xA4 field is accepted only as a fallback when the
client's +0x90 primary field was not read or was empty.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Iterable

from audit_native_meter_skill_key_v50 import (
    DEFAULT_CAPTURE_ROOT,
    DEFAULT_SELECTION,
    _capture_selection,
    _find_captures,
    audit_capture,
)
from dragon_nest_builder.live_dps.sources import _LIVE_DAMAGE_SKILL_ALIASES


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "outputs/native_wire_v50_20260909"
EMPTY_NATIVE_VALUES = frozenset({0, 0xFFFFFFFF})


def _source_class(actor_id: int) -> str:
    if 0 < actor_id <= 0x00FFFFFF:
        return "entity"
    if 0x00FFFFFF < actor_id < 0xFFFFFFFF:
        return "player"
    return "invalid"


def _candidate_key(
    *,
    field: str,
    family: str,
    source_kind: int,
    source_class: str,
    job: str,
    raw_key: int,
    canonical_skill: int,
    skill_name: str,
) -> tuple[str, str, int, str, str, int, int, str]:
    return (
        field,
        family,
        source_kind,
        source_class,
        job,
        raw_key,
        canonical_skill,
        skill_name,
    )


def _serialise_evidence(
    counters: dict[tuple[Any, ...], Counter[str]],
    methods: dict[tuple[Any, ...], Counter[str]],
    captures: dict[tuple[Any, ...], set[str]],
    actors: dict[tuple[Any, ...], set[int]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(counters):
        (
            field,
            family,
            source_kind,
            source_class,
            job,
            raw_key,
            canonical_skill,
            skill_name,
        ) = key
        rows.append(
            {
                "field": field,
                "family": family,
                "source_kind": source_kind,
                "source_class": source_class,
                "job": job,
                "raw_key": raw_key,
                "canonical_skill": canonical_skill,
                "skill_name": skill_name,
                **dict(counters[key]),
                "methods": dict(sorted(methods[key].items())),
                "capture_hashes": sorted(captures[key]),
                "source_actor_ids": sorted(actors[key]),
            }
        )
    return rows


def collect_partial(
    *,
    capture_root: Path,
    selection_path: Path,
    shard_index: int,
    shard_count: int,
) -> dict[str, Any]:
    selected = [
        row
        for index, row in enumerate(_capture_selection(selection_path))
        if index % shard_count == shard_index
    ]
    paths = _find_captures(capture_root, selected, set())
    counters: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    methods: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    captures: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    actors: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    totals: Counter[str] = Counter()
    capture_manifest: list[dict[str, Any]] = []

    for path in paths:
        audited = audit_capture(path, include_rows=True)
        capture_hash = str(audited["capture_sha256"])
        capture_manifest.append(
            {
                "capture": path.name,
                "capture_sha256": capture_hash,
                "joined_rows": int(audited["joined_rows"]),
                "independent_deterministic_rows": int(
                    audited["independent_deterministic_rows"]
                ),
            }
        )
        totals["captures"] += 1
        totals["joined_rows"] += int(audited["joined_rows"])
        totals["independent_deterministic_rows"] += int(
            audited["independent_deterministic_rows"]
        )

        for row in audited["rows"]:
            if not row["independent_deterministic"]:
                continue
            canonical = int(row["skill_id"])
            if canonical <= 0:
                continue
            native = row["native"]
            family = str(row["family"])
            source_kind = int(native["source_kind"])
            actor_id = int(row["source_actor_id"])
            source_class = _source_class(actor_id)
            if source_class == "invalid":
                totals["invalid_source_rows"] += 1
                continue
            primary_active = bool(native["primary_read"])
            primary_raw = int(native["primary_raw"])
            primary_available = (
                primary_active and primary_raw not in EMPTY_NATIVE_VALUES
            )
            fields = (
                (
                    "primary90",
                    primary_raw,
                    primary_active,
                    primary_available,
                    int(native["primary_offset"]),
                ),
                (
                    "fallbackA4",
                    int(native["fallback_raw"]),
                    True,
                    not primary_available,
                    int(native["fallback_offset"]),
                ),
            )
            valid_slots = {
                int(offset): int(value)
                for offset, value in row.get("valid_slots", {}).items()
            }
            for field, raw_key, active, structurally_eligible, offset in fields:
                if not active or raw_key in EMPTY_NATIVE_VALUES:
                    continue
                key = _candidate_key(
                    field=field,
                    family=family,
                    source_kind=source_kind,
                    source_class=source_class,
                    job=str(row["job"]),
                    raw_key=raw_key,
                    canonical_skill=canonical,
                    skill_name=str(row["skill_name"]),
                )
                counter = counters[key]
                counter["observed"] += 1
                counter["eligible_observed"] += int(structurally_eligible)
                counter["blocked_observed"] += int(not structurally_eligible)
                counter["raw_equals_canonical"] += int(raw_key == canonical)
                counter["live_alias_equals_canonical"] += int(
                    int(_LIVE_DAMAGE_SKILL_ALIASES.get(raw_key, raw_key))
                    == canonical
                )
                counter["catalogued_same"] += int(
                    valid_slots.get(offset) == canonical
                )
                counter["catalogued_conflict"] += int(
                    offset in valid_slots and valid_slots[offset] != canonical
                )
                counter["common_cc_0"] += int(native["common_cc"] == 0)
                methods[key][str(row["method"])] += 1
                captures[key].add(capture_hash)
                actors[key].add(actor_id)

    return {
        "schema": "native-skill-key-evidence-partial-v50",
        "method": (
            "offline replay; independently deterministic rows only; "
            "no production decoder mutation"
        ),
        "selection": str(selection_path),
        "shard_index": shard_index,
        "shard_count": shard_count,
        "totals": dict(totals),
        "captures": capture_manifest,
        "evidence": _serialise_evidence(counters, methods, captures, actors),
    }


def _merge_partials(partials: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counters: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    methods: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    captures: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    actors: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    manifests: dict[str, dict[str, Any]] = {}
    totals: Counter[str] = Counter()
    selections: set[str] = set()
    shard_counts: set[int] = set()
    shard_indexes: set[int] = set()

    numeric_fields = (
        "observed",
        "eligible_observed",
        "blocked_observed",
        "raw_equals_canonical",
        "live_alias_equals_canonical",
        "catalogued_same",
        "catalogued_conflict",
        "common_cc_0",
    )
    for partial in partials:
        if partial.get("schema") != "native-skill-key-evidence-partial-v50":
            raise ValueError("unexpected partial schema")
        selections.add(str(partial["selection"]))
        shard_counts.add(int(partial["shard_count"]))
        shard_indexes.add(int(partial["shard_index"]))
        totals.update({key: int(value) for key, value in partial["totals"].items()})
        for manifest in partial["captures"]:
            capture_hash = str(manifest["capture_sha256"])
            if capture_hash in manifests and manifests[capture_hash] != manifest:
                raise ValueError(f"capture manifest conflict: {capture_hash}")
            manifests[capture_hash] = manifest
        for row in partial["evidence"]:
            key = _candidate_key(
                field=str(row["field"]),
                family=str(row["family"]),
                source_kind=int(row["source_kind"]),
                source_class=str(row["source_class"]),
                job=str(row["job"]),
                raw_key=int(row["raw_key"]),
                canonical_skill=int(row["canonical_skill"]),
                skill_name=str(row["skill_name"]),
            )
            counters[key].update(
                {field: int(row.get(field, 0)) for field in numeric_fields}
            )
            methods[key].update(
                {name: int(count) for name, count in row["methods"].items()}
            )
            captures[key].update(str(value) for value in row["capture_hashes"])
            actors[key].update(int(value) for value in row["source_actor_ids"])

    if len(selections) != 1 or len(shard_counts) != 1:
        raise ValueError("partials do not share one selection/shard count")
    shard_count = next(iter(shard_counts))
    if shard_indexes != set(range(shard_count)):
        raise ValueError(
            f"incomplete shard set: got {sorted(shard_indexes)}, expected 0..{shard_count-1}"
        )
    return {
        "selection": next(iter(selections)),
        "totals": dict(totals),
        "captures": sorted(manifests.values(), key=lambda row: row["capture_sha256"]),
        "evidence": _serialise_evidence(counters, methods, captures, actors),
    }


def _evaluate(
    merged: dict[str, Any],
    *,
    min_hits: int,
    min_captures: int,
    min_actors: int,
) -> dict[str, Any]:
    rows = merged["evidence"]
    scoped_candidates: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    global_candidates: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    global_jobs: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    all_candidates: dict[tuple[Any, ...], set[int]] = defaultdict(set)

    for row in rows:
        scoped = (
            row["field"],
            row["family"],
            row["source_kind"],
            row["source_class"],
            row["job"],
            row["raw_key"],
        )
        global_scope = scoped[:4] + (row["raw_key"],)
        canonical = int(row["canonical_skill"])
        all_candidates[global_scope].add(canonical)
        if int(row["eligible_observed"]) > 0:
            scoped_candidates[scoped].add(canonical)
            global_candidates[global_scope].add(canonical)
            global_jobs[global_scope].add(str(row["job"]))

    accepted: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        if int(row["eligible_observed"]) <= 0:
            continue
        scoped = (
            row["field"],
            row["family"],
            row["source_kind"],
            row["source_class"],
            row["job"],
            row["raw_key"],
        )
        global_scope = scoped[:4] + (row["raw_key"],)
        canonical = int(row["canonical_skill"])
        reasons: list[str] = []
        if len(scoped_candidates[scoped]) != 1:
            reasons.append("职业适用域内出现多个 canonical skill")
        # Job is an intentional part of the primary-field scope: the client
        # normalises some base/internal attack rows differently for different
        # professions.  Cross-job disagreement therefore makes job scoping
        # mandatory, but is not itself a conflict inside the declared scope.
        # +0xA4 is different: it is a contextual fallback, so disagreement in
        # any equivalent field scope (including blocked primary-present rows)
        # is a hard rejection.
        if row["field"] == "fallbackA4":
            if len(global_candidates[global_scope]) != 1:
                reasons.append("+0xA4 跨职业同字段适用域指向多个技能")
            if len(all_candidates[global_scope]) != 1:
                reasons.append("+0xA4 含被阻止上下文行时指向多个技能")
            if int(row["catalogued_conflict"]) > 0:
                reasons.append("+0xA4 曾携带另一个已登记技能 ID")
        elif int(row["catalogued_conflict"]) > 0:
            reasons.append(
                "+0x90 raw 本身是另一个已登记技能；缺少客户端归一化静态证明"
            )
        if row["field"] == "primary90" and int(row["source_kind"]) != 1:
            reasons.append("+0x90 不满足客户端 source_kind=1 读取分支")
        support_reasons: list[str] = []
        if int(row["eligible_observed"]) < min_hits:
            support_reasons.append(f"Hit<{min_hits}")
        if len(row["capture_hashes"]) < min_captures:
            support_reasons.append(f"抓包<{min_captures}")
        if len(row["source_actor_ids"]) < min_actors:
            support_reasons.append(f"来源实例<{min_actors}")

        result = {
            **row,
            "capture_count": len(row["capture_hashes"]),
            "source_actor_count": len(row["source_actor_ids"]),
            "scope_canonical_candidates": sorted(scoped_candidates[scoped]),
            "global_canonical_candidates": sorted(global_candidates[global_scope]),
            "all_context_canonical_candidates": sorted(all_candidates[global_scope]),
            "global_jobs": sorted(global_jobs[global_scope]),
            "requires_job_scope": len(global_candidates[global_scope]) > 1,
            "raw_is_catalogued_as_other_skill": int(
                row["catalogued_conflict"]
            ) > 0,
            "mapping_kind": (
                "identity"
                if int(row["raw_key"]) == canonical
                else "existing_live_alias"
                if int(
                    _LIVE_DAMAGE_SKILL_ALIASES.get(
                        int(row["raw_key"]), int(row["raw_key"])
                    )
                )
                == canonical
                else "learned_normalization"
            ),
            "use_condition": (
                "source_kind=1 and common_cc=0"
                if row["field"] == "primary90"
                else "+0x90 not read or empty; fallback only"
            ),
        }
        if reasons:
            result["decision"] = "rejected"
            result["reasons"] = reasons
            rejected.append(result)
        elif support_reasons:
            result["decision"] = "insufficient_support"
            result["reasons"] = support_reasons
            insufficient.append(result)
        else:
            result["decision"] = "accepted"
            result["reasons"] = []
            accepted.append(result)

    sort_key = lambda row: (
        row["field"],
        row["job"],
        row["source_class"],
        row["family"],
        row["source_kind"],
        row["raw_key"],
        row["canonical_skill"],
    )
    accepted.sort(key=sort_key)
    insufficient.sort(key=sort_key)
    rejected.sort(key=sort_key)

    by_field: dict[str, Counter[str]] = defaultdict(Counter)
    by_job: dict[str, Counter[str]] = defaultdict(Counter)
    accepted_mapping_kinds: Counter[str] = Counter()
    for decision, decision_rows in (
        ("accepted", accepted),
        ("insufficient_support", insufficient),
        ("rejected", rejected),
    ):
        for row in decision_rows:
            by_field[row["field"]][decision] += 1
            by_field[row["field"]][f"{decision}_hits"] += int(
                row["eligible_observed"]
            )
            by_job[row["job"]][decision] += 1
            by_job[row["job"]][f"{decision}_hits"] += int(
                row["eligible_observed"]
            )
            if decision == "accepted":
                accepted_mapping_kinds[row["mapping_kind"]] += 1
                accepted_mapping_kinds[f"{row['mapping_kind']}_hits"] += int(
                    row["eligible_observed"]
                )

    return {
        "schema": "native-skill-key-zero-conflict-acceptance-v50",
        "production_decoder_changed": False,
        "scope": [
            "field",
            "wire_family",
            "source_kind",
            "player_or_entity_source",
            "job",
            "raw_key",
        ],
        "acceptance_contract": {
            "truth_rows": (
                "only attribution methods independently deterministic from the "
                "native raw field"
            ),
            "zero_conflict": (
                "one canonical skill inside the complete declared scope; "
                "+0x90 may require job scoping because client normalisation is "
                "profession-sensitive, but a raw key already catalogued as a "
                "different skill remains rejected without static client proof; "
                "+0xA4 must also agree across jobs"
            ),
            "fallback_a4": (
                "+0xA4 is usable only when +0x90 was not read or was empty; "
                "blocked context rows still participate in conflict rejection"
            ),
            "minimum_support": {
                "eligible_hits": min_hits,
                "distinct_captures": min_captures,
                "distinct_source_actors_or_entities": min_actors,
            },
            "non_claim": (
                "zero conflict is limited to the fixed corpus and is not proof "
                "that an unscoped global u32 mapping is universally valid"
            ),
        },
        "selection": merged["selection"],
        "corpus": {
            **merged["totals"],
            "unique_capture_hashes": len(merged["captures"]),
        },
        "summary": {
            "accepted": len(accepted),
            "insufficient_support": len(insufficient),
            "rejected": len(rejected),
            "accepted_by_field": {
                key: dict(value) for key, value in sorted(by_field.items())
            },
            "accepted_mapping_kinds": dict(accepted_mapping_kinds),
            "by_job": {key: dict(value) for key, value in sorted(by_job.items())},
        },
        "accepted_mappings": accepted,
        "insufficient_support": insufficient,
        "rejected": rejected,
        "capture_manifest": merged["captures"],
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    corpus = report["corpus"]
    lines = [
        "# Native damage skill key 零冲突映射验收（v50）",
        "",
        "## 结论",
        "",
        (
            f"固定语料共 {corpus['unique_capture_hashes']} 份抓包、"
            f"{corpus.get('joined_rows', 0):,} 条已连接伤害记录、"
            f"{corpus.get('independent_deterministic_rows', 0):,} 条独立确定性真值。"
        ),
        (
            f"严格验收得到 **{summary['accepted']}** 条可采纳的带适用域映射；"
            f"**{summary['insufficient_support']}** 条目前零冲突但样本不足；"
            f"**{summary['rejected']}** 条因冲突或上下文风险被拒绝。"
        ),
        (
            "可采纳项中，新增归一化映射 "
            f"**{summary['accepted_mapping_kinds'].get('learned_normalization', 0)}** 条，"
            "其余为字段本身已等于技能 ID 或已有别名的复核。"
        ),
        "",
        "该输出不能直接简化成全局 `raw u32 -> skill` 字典。每条映射必须同时匹配字段、包族、source kind、玩家/实体来源和职业。",
        "",
        "## 验收规则",
        "",
        "- 真值只来自不依赖该 raw 字段的独立确定性归因。",
        "- `+0x90` 只在客户端实际读取它的 `source_kind=1 && common_cc=0` 分支验收。",
        "- `+0xA4` 只作为 fallback：`+0x90` 未读取或为空时才允许使用；被阻止的上下文行仍用于否决冲突映射。",
        "- 完整适用域内必须只指向一个 canonical skill；`+0xA4` 还必须跨职业一致，并在计入被阻止的上下文行后仍然唯一。",
        "- 若 raw 本身是另一个已登记技能，即使统计上零冲突，也必须先取得客户端静态归一化证明，否则拒绝。",
        "- 最低样本要求见 JSON 的 `acceptance_contract.minimum_support`。",
        "- 本报告不修改正式解码器，也不改变任何 Hit、伤害、来源或目标。",
        "",
        "## 字段汇总",
        "",
        "| 字段 | 可采纳映射 | 可采纳 Hit | 样本不足 | 被拒绝 |",
        "|---|---:|---:|---:|---:|",
    ]
    for field, values in summary["accepted_by_field"].items():
        lines.append(
            f"| {field} | {values.get('accepted', 0)} | "
            f"{values.get('accepted_hits', 0):,} | "
            f"{values.get('insufficient_support', 0)} | "
            f"{values.get('rejected', 0)} |"
        )

    lines.extend(
        [
            "",
            "## 可采纳映射",
            "",
            "| 字段 | 包族 | source kind | 来源 | 职业 | raw | canonical | 技能 | 类型 | Hit | 抓包 | 来源实例 | 使用条件 |",
            "|---|---|---:|---|---|---:|---:|---|---|---:|---:|---:|---|",
        ]
    )
    for row in report["accepted_mappings"]:
        lines.append(
            f"| {row['field']} | {row['family']} | {row['source_kind']} | "
            f"{row['source_class']} | {row['job']} | {row['raw_key']} | "
            f"{row['canonical_skill']} | {row['skill_name']} | "
            f"{row['mapping_kind']} | {row['eligible_observed']:,} | {row['capture_count']} | "
            f"{row['source_actor_count']} | {row['use_condition']} |"
        )

    lines.extend(
        [
            "",
            "## 职业汇总",
            "",
            "| 职业 | 可采纳 | 可采纳 Hit | 样本不足 | 被拒绝 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for job, values in summary["by_job"].items():
        lines.append(
            f"| {job} | {values.get('accepted', 0)} | "
            f"{values.get('accepted_hits', 0):,} | "
            f"{values.get('insufficient_support', 0)} | "
            f"{values.get('rejected', 0)} |"
        )
    lines.extend(
        [
            "",
            "完整的样本不足项、冲突候选、方法分布、抓包哈希和来源实例均保存在同名 JSON 中。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--collect-shard-index", type=int)
    parser.add_argument("--collect-shard-count", type=int)
    parser.add_argument("--partial-output", type=Path)
    parser.add_argument("--partial", action="append", type=Path, default=[])
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "native_skill_key_acceptance_v50.json",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "native_skill_key_acceptance_v50.md",
    )
    parser.add_argument("--min-hits", type=int, default=8)
    parser.add_argument("--min-captures", type=int, default=2)
    parser.add_argument("--min-actors", type=int, default=2)
    args = parser.parse_args()

    collecting = args.collect_shard_index is not None or args.collect_shard_count is not None
    if collecting:
        if (
            args.collect_shard_index is None
            or args.collect_shard_count is None
            or args.collect_shard_count <= 0
            or not 0 <= args.collect_shard_index < args.collect_shard_count
            or args.partial_output is None
            or args.partial
        ):
            raise ValueError("collect mode requires a valid shard pair and --partial-output")
        partial = collect_partial(
            capture_root=args.capture_root.resolve(),
            selection_path=args.selection.resolve(),
            shard_index=args.collect_shard_index,
            shard_count=args.collect_shard_count,
        )
        args.partial_output.parent.mkdir(parents=True, exist_ok=True)
        args.partial_output.write_text(
            json.dumps(partial, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(partial["totals"], ensure_ascii=False, indent=2))
        return 0

    if not args.partial:
        raise ValueError("merge mode requires one --partial per completed shard")
    partials = [json.loads(path.read_text(encoding="utf-8")) for path in args.partial]
    merged = _merge_partials(partials)
    report = _evaluate(
        merged,
        min_hits=max(1, args.min_hits),
        min_captures=max(1, args.min_captures),
        min_actors=max(1, args.min_actors),
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
