"""Merge native-meter audit shards and derive conservative key mappings.

This is an offline evidence report.  A raw native result key is accepted only
when all independently deterministic observations across the fixed corpus map
it to one player-facing skill.  Timing/inferred rows are never allowed to
establish a mapping; they are only counted afterwards as possible coverage.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/native_wire_v50_20260909"
SHARDS = tuple(sorted(OUT.glob("native_meter_correlations_shard*.json")))
JSON_OUT = OUT / "native_meter_correlations_merged.json"
MD_OUT = OUT / "native_meter_correlations_merged.md"

sys.path.insert(0, str(ROOT))
from tools.audit_native_meter_skill_key_v50 import _aggregate  # noqa: E402


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_key(value: str) -> tuple[str, str, str, int, str, int, str, str]:
    family, source_class, field, raw, job, assigned, skill_name, method = value.split("|", 7)
    return family, source_class, field, int(raw), job, int(assigned), skill_name, method


def main() -> int:
    if len(SHARDS) != 3:
        raise ValueError(f"expected three correlation shards, got {len(SHARDS)}")
    documents = [_load(path) for path in SHARDS]
    captures = [capture for document in documents for capture in document["captures"]]
    if len(captures) != 35:
        raise ValueError(f"expected fixed 35-capture corpus, got {len(captures)}")
    hashes = [capture["capture_sha256"] for capture in captures]
    if len(set(hashes)) != len(hashes):
        raise ValueError("duplicate capture hash across shards")

    aggregate = _aggregate(captures)
    evidence: dict[tuple[str, str, int], dict[str, Any]] = defaultdict(
        lambda: {
            "assigned": Counter(),
            "jobs": Counter(),
            "skills": Counter(),
            "methods": Counter(),
            "captures": set(),
            "independent_hits": 0,
            "observed_hits": 0,
        }
    )
    inferred_rows: list[dict[str, Any]] = []
    for capture in captures:
        capture_hash = capture["capture_sha256"]
        for key, counts in capture.get("raw_field_correlations", {}).items():
            family, source_class, field, raw, job, assigned, skill_name, method = _parse_key(key)
            independent = int(counts.get("independent_deterministic", 0))
            observed = int(counts.get("observed", 0))
            inferred = int(counts.get("named_non_packet_derived", 0))
            group = evidence[(source_class, field, raw)]
            group["observed_hits"] += observed
            if independent:
                group["assigned"][assigned] += independent
                group["jobs"][job] += independent
                group["skills"][f"{assigned}:{skill_name}"] += independent
                group["methods"][method] += independent
                group["captures"].add(capture_hash)
                group["independent_hits"] += independent
            if inferred:
                inferred_rows.append(
                    {
                        "capture_sha256": capture_hash,
                        "family": family,
                        "source_class": source_class,
                        "field": field,
                        "raw_key": raw,
                        "job": job,
                        "assigned_skill_id": assigned,
                        "skill_name": skill_name,
                        "method": method,
                        "hits": inferred,
                    }
                )

    accepted: dict[tuple[str, str, int], int] = {}
    mapping_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    for key, group in sorted(evidence.items()):
        assignments = dict(group["assigned"])
        row = {
            "source_class": key[0],
            "field": key[1],
            "raw_key": key[2],
            "independent_hits": group["independent_hits"],
            "capture_support": len(group["captures"]),
            "assigned_skills": assignments,
            "jobs": dict(group["jobs"]),
            "skill_labels": dict(group["skills"]),
            "methods": dict(group["methods"]),
        }
        if group["independent_hits"] > 0 and len(assignments) == 1:
            canonical = next(iter(assignments))
            row["canonical_skill_id"] = canonical
            accepted[key] = canonical
            mapping_rows.append(row)
        elif group["independent_hits"]:
            rejected_rows.append(row)

    # A row may expose both native fields.  This summary is deliberately an
    # optimistic upper bound until a second physical-row pass performs a union.
    possible_by_job: Counter[str] = Counter()
    possible_by_skill: Counter[str] = Counter()
    candidate_rows: list[dict[str, Any]] = []
    for row in inferred_rows:
        canonical = accepted.get((row["source_class"], row["field"], row["raw_key"]))
        if canonical != row["assigned_skill_id"]:
            continue
        possible_by_job[row["job"]] += row["hits"]
        possible_by_skill[f"{row['job']}|{canonical}|{row['skill_name']}"] += row["hits"]
        candidate_rows.append(row)

    report = {
        "schema": "native-meter-correlation-report-v50",
        "scope": "fixed 35-capture offline replay; no production mutation",
        "shards": [str(path) for path in SHARDS],
        "summary": {
            **aggregate,
            "accepted_zero_conflict_raw_keys": len(mapping_rows),
            "rejected_conflicting_raw_keys": len(rejected_rows),
            "optimistic_candidate_hits_by_job_before_row_union": dict(possible_by_job),
            "warning": "candidate counts may double-count one physical row when both fields match",
        },
        "accepted_mappings": mapping_rows,
        "conflicting_mappings": rejected_rows,
        "optimistic_candidate_hits_by_skill_before_row_union": dict(possible_by_skill),
        "candidate_rows_before_physical_union": candidate_rows,
        "production_changed": False,
    }
    JSON_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 客户端原生技能键跨包审计（v50）",
        "",
        f"固定抓包：{aggregate['capture_count']}；物理 Hit：{aggregate['joined_rows']}；已有独立确定性 Hit：{aggregate['independent_deterministic_rows']}。",
        "",
        f"独立证据零冲突原生键：{len(mapping_rows)}；存在冲突的原生键：{len(rejected_rows)}。",
        "",
        "注意：下表的可提升 Hit 是第二次逐物理记录去重前的乐观值，同一 Hit 的两个原生字段都匹配时可能重复，不能直接作为正式覆盖率。",
        "",
        "## 未确认 Hit 中可由零冲突原生键复核的数量",
        "",
        "|职业|候选 Hit（去重前）|",
        "|---|---:|",
    ]
    for job, count in possible_by_job.most_common():
        lines.append(f"|{job}|{count}|")
    lines += ["", "## 原生键映射", "", "|来源|字段|原生键|技能|独立 Hit|抓包数|", "|---|---|---:|---|---:|---:|"]
    for row in sorted(mapping_rows, key=lambda item: item["independent_hits"], reverse=True):
        label = max(row["skill_labels"], key=row["skill_labels"].get)
        lines.append(
            f"|{row['source_class']}|{row['field']}|{row['raw_key']}|{label}|"
            f"{row['independent_hits']}|{row['capture_support']}|"
        )
    if rejected_rows:
        lines += ["", "## 冲突原生键（禁止接入）", "", "|来源|字段|原生键|候选技能|独立 Hit|", "|---|---|---:|---|---:|"]
        for row in sorted(rejected_rows, key=lambda item: item["independent_hits"], reverse=True):
            lines.append(
                f"|{row['source_class']}|{row['field']}|{row['raw_key']}|"
                f"{json.dumps(row['assigned_skills'], ensure_ascii=False)}|{row['independent_hits']}|"
            )
    MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
