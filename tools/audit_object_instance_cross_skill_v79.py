from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)


DEFAULT_MANIFEST = (
    ROOT
    / "outputs"
    / "direct_damage_v78_fixed35_20260910"
    / "profession_direct_damage_report.json"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "object_instance_cross_skill_v79_20260910"
_OBJECT_PREFIXES = ("damage-object:", "player-root-object:")


def _identity(event) -> tuple[str, str]:
    return str(event.skill_id or "-1"), str(event.skill_name or "待识别伤害")


def _usable_hit(event) -> bool:
    return bool(
        not event.healing
        and int(event.amount or 0) > 0
        and event.has_real_hit_evidence is True
        and str(event.cast_instance_id or "").startswith(_OBJECT_PREFIXES)
    )


def _scan(path_text: str) -> dict[str, object]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    try:
        source_events = source.poll()
    finally:
        source.close()
    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    events = aggregator.snapshot(include_events=True).combat_events

    groups: dict[tuple[str, str], list[object]] = defaultdict(list)
    for event in events:
        if _usable_hit(event):
            groups[(str(event.source_id), str(event.cast_instance_id))].append(event)

    candidates = []
    conflicts = []
    for (source_id, instance_id), rows in groups.items():
        anchors: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        for row in rows:
            if is_deterministic_attribution(row.attribution_method):
                counts = anchors[_identity(row)]
                counts["hits"] += 1
                counts["damage"] += int(row.amount)
        if len(anchors) > 1:
            conflicts.append(
                {
                    "source_id": source_id,
                    "job": str(rows[0].job or ""),
                    "instance_id": instance_id,
                    "anchors": [
                        {
                            "skill_id": key[0],
                            "skill_name": key[1],
                            "hits": int(value["hits"]),
                            "damage": int(value["damage"]),
                        }
                        for key, value in anchors.items()
                    ],
                }
            )
            continue
        if len(anchors) != 1:
            continue
        anchor = next(iter(anchors))
        weak: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
        for row in rows:
            if is_deterministic_attribution(row.attribution_method):
                continue
            key = (*_identity(row), str(row.attribution_method or "unresolved"))
            weak[key]["hits"] += 1
            weak[key]["damage"] += int(row.amount)
        if not weak:
            continue
        candidates.append(
            {
                "source_id": source_id,
                "job": str(rows[0].job or ""),
                "instance_id": instance_id,
                "anchor_skill_id": anchor[0],
                "anchor_skill_name": anchor[1],
                "anchor_hits": int(anchors[anchor]["hits"]),
                "anchor_damage": int(anchors[anchor]["damage"]),
                "weak": [
                    {
                        "old_skill_id": key[0],
                        "old_skill_name": key[1],
                        "old_method": key[2],
                        "hits": int(value["hits"]),
                        "damage": int(value["damage"]),
                    }
                    for key, value in weak.items()
                ],
            }
        )
    return {
        "capture": path.name,
        "capture_path": str(path),
        "groups": len(groups),
        "candidates": candidates,
        "conflicts": conflicts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    paths = [str(Path(row["capture_path"])) for row in manifest["capture_manifest"]]
    captures = []
    with ProcessPoolExecutor(max_workers=max(1, min(4, args.workers))) as pool:
        futures = {pool.submit(_scan, path): path for path in paths}
        for future in as_completed(futures):
            row = future.result()
            captures.append(row)
            print(f"[{len(captures)}/{len(paths)}] {row['capture']}", flush=True)
    captures.sort(key=lambda row: str(row["capture_path"]))

    summary: dict[tuple[str, str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    summary_files: dict[tuple[str, str, str, str, str, str], set[str]] = defaultdict(set)
    for capture in captures:
        for candidate in capture["candidates"]:
            for weak in candidate["weak"]:
                key = (
                    str(candidate["job"]),
                    str(weak["old_skill_id"]),
                    str(weak["old_skill_name"]),
                    str(weak["old_method"]),
                    str(candidate["anchor_skill_id"]),
                    str(candidate["anchor_skill_name"]),
                )
                summary[key]["hits"] += int(weak["hits"])
                summary[key]["damage"] += int(weak["damage"])
                summary_files[key].add(str(capture["capture"]))
    rows = [
        {
            "job": key[0],
            "old_skill_id": key[1],
            "old_skill_name": key[2],
            "old_method": key[3],
            "anchor_skill_id": key[4],
            "anchor_skill_name": key[5],
            "hits": int(counts["hits"]),
            "damage": int(counts["damage"]),
            "captures": len(summary_files[key]),
            "examples": sorted(summary_files[key])[:5],
        }
        for key, counts in summary.items()
    ]
    rows.sort(key=lambda row: (-int(row["hits"]), row["job"], row["old_skill_id"]))
    result = {
        "schema": "object-instance-cross-skill-v79",
        "captures": captures,
        "summary": rows,
        "totals": {
            "captures": len(captures),
            "candidate_hits": sum(int(row["hits"]) for row in rows),
            "candidate_damage": sum(int(row["damage"]) for row in rows),
            "conflict_instances": sum(len(row["conflicts"]) for row in captures),
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "REPORT.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 同对象实例交叉技能审计 v79",
        "",
        f"- 固定抓包：{len(captures)}",
        f"- 唯一锚点可迁移 Hit：{result['totals']['candidate_hits']:,}",
        f"- 多锚点冲突实例：{result['totals']['conflict_instances']:,}",
        "",
        "|职业|旧技能|旧方法|唯一对象锚点|Hit|伤害|抓包数|",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"|{row['job']}|{row['old_skill_name']} `{row['old_skill_id']}`|"
            f"`{row['old_method']}`|{row['anchor_skill_name']} "
            f"`{row['anchor_skill_id']}`|{row['hits']:,}|{row['damage']:,}|"
            f"{row['captures']}|"
        )
    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
