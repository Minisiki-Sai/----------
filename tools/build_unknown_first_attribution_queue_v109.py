"""Build a strict unknown-first queue from a direct-damage release report.

The current display name of a non-deterministic Hit is diagnostic context only.
It must not vote for the final skill, seed a mapping, or make a rule appear
conflict-free.  This tool preserves that old label in an explicitly audit-only
field and exports the work queue using only evidence route/object family.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT
    / "outputs"
    / "direct_damage_v103_blackdragon_new33_20260910"
    / "profession_direct_damage_report.json"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "unknown_first_queue_v109_blackdragon_new33_20260910"


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "job", "priority", "required_for_95", "gap_hits", "gap_damage",
        "method", "object_family", "hits", "damage", "capture_count",
        "resolved_skill_id", "resolved_skill_name", "legacy_labels_audit_only",
        "example_captures",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build(report: dict[str, object]) -> dict[str, object]:
    summary_by_job = {row["职业"]: row for row in report["summary"]}
    groups: dict[tuple[str, str, str], dict[str, object]] = {}
    observed_gap_hits: dict[str, int] = defaultdict(int)
    observed_gap_damage: dict[str, int] = defaultdict(int)
    for row in report["details"]:
        if row["归因分类"] == "deterministic":
            continue
        job = str(row["职业"])
        method = str(row["method"])
        object_family = str(row["object模板"] or "<无对象模板证据>")
        key = (job, method, object_family)
        target = groups.setdefault(
            key,
            {
                "job": job,
                "method": method,
                "object_family": object_family,
                "hits": 0,
                "damage": 0,
                "capture_count": 0,
                "legacy_labels_audit_only": set(),
                "example_captures": set(),
                # Deliberately empty: this queue cannot promote an old name.
                "resolved_skill_id": "",
                "resolved_skill_name": "待识别伤害",
            },
        )
        hits = int(row["Hit数"])
        damage = int(row["伤害"])
        target["hits"] += hits
        target["damage"] += damage
        target["capture_count"] = max(target["capture_count"], int(row["抓包数"]))
        target["legacy_labels_audit_only"].add(
            f"{row['skill_id']} {row['技能名']}"
        )
        target["example_captures"].update(
            part.strip() for part in str(row["示例抓包"]).split("|") if part.strip()
        )
        observed_gap_hits[job] += hits
        observed_gap_damage[job] += damage

    rows: list[dict[str, object]] = []
    for target in groups.values():
        summary = summary_by_job[target["job"]]
        total = int(summary["总直接Hit"])
        deterministic = int(summary["确定性已归因"])
        required = max(0, math.ceil(total * 0.95) - deterministic)
        target.update(
            required_for_95=required,
            gap_hits=int(summary["归因缺口"]),
            gap_damage=int(summary["缺口伤害"]),
            priority=(
                "P0" if required and int(target["hits"]) >= required
                else "P1" if required
                else "P2"
            ),
            legacy_labels_audit_only=" | ".join(
                sorted(target["legacy_labels_audit_only"])
            ),
            example_captures=" | ".join(
                sorted(target["example_captures"])[:8]
            ),
        )
        rows.append(target)
    rows.sort(
        key=lambda row: (
            row["priority"], -int(row["hits"]), row["job"], row["method"],
            row["object_family"],
        )
    )

    for job, summary in summary_by_job.items():
        expected_hits = int(summary["归因缺口"])
        expected_damage = int(summary["缺口伤害"])
        if observed_gap_hits[job] != expected_hits:
            raise AssertionError(
                f"gap Hit mismatch for {job}: {observed_gap_hits[job]} != {expected_hits}"
            )
        if observed_gap_damage[job] != expected_damage:
            raise AssertionError(
                f"gap damage mismatch for {job}: {observed_gap_damage[job]} != {expected_damage}"
            )
    if any(row["resolved_skill_id"] or row["resolved_skill_name"] != "待识别伤害" for row in rows):
        raise AssertionError("unknown-first queue promoted an audit-only label")

    under95 = [
        {
            "job": row["职业"],
            "coverage_percent": row["确定性覆盖率%"],
            "total_hits": row["总直接Hit"],
            "deterministic_hits": row["确定性已归因"],
            "gap_hits": row["归因缺口"],
            "required_for_95": max(
                0,
                math.ceil(int(row["总直接Hit"]) * 0.95)
                - int(row["确定性已归因"]),
            ),
        }
        for row in report["summary"]
        if int(row["总直接Hit"] or 0) > 0
        and float(row["确定性覆盖率%"] or 0) < 95.0
    ]
    under95.sort(key=lambda row: (-row["required_for_95"], row["job"]))
    return {
        "schema": "unknown-first-attribution-queue-v109",
        "source_schema": report.get("schema_version"),
        "policy": {
            "non_deterministic_skill_identity": "unknown",
            "legacy_skill_label": "audit_only_no_vote",
            "positive_evidence": [
                "same-record fixed skill key",
                "action/ACT -> Projectile or entity generation -> damage_object_id -> direct37",
                "current-generation entity owner -> exact entity attack key -> direct37",
                "status instance owner -> periodic damage record",
            ],
            "forbidden_positive_evidence": [
                "nearest cast time",
                "legacy displayed skill name",
                "damage magnitude",
                "Hit shape alone",
                "result group alone",
            ],
        },
        "under95": under95,
        "queue": rows,
        "invariants": {
            "gap_hit_totals_match_source": True,
            "gap_damage_totals_match_source": True,
            "legacy_labels_promoted": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    result = build(report)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "REPORT.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(args.output / "unknown_queue.csv", result["queue"])
    lines = [
        "# 新黑龙语料：未知优先归因队列 v109",
        "",
        "非确定性技能名全部降为审计标签；下面的缺口没有一个因旧名称而获得技能身份。",
        "",
        "|职业|当前覆盖|达到95%仍需确认|总缺口|",
        "|---|---:|---:|---:|",
    ]
    for row in result["under95"]:
        lines.append(
            f"|{row['job']}|{row['coverage_percent']:.6f}%|"
            f"{row['required_for_95']:,}|{row['gap_hits']:,}|"
        )
    lines += [
        "",
        "完整优先队列见 `unknown_queue.csv`；旧名称仅在 "
        "`legacy_labels_audit_only` 列中保留，不能参与映射投票。",
    ]
    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "under95": len(result["under95"]),
                "queue_groups": len(result["queue"]),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
