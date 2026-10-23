"""Audit capture-scoped damage-object identity anchors for Cleric jobs.

This is a read-only evidence tool.  A candidate is emitted only when every
independently deterministic hit for one exact
``(capture, source actor, damage_object_id)`` names the same canonical skill.
No elapsed-time window, damage amount, hit cadence, or legacy inferred skill
name participates in the candidate selection.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records  # noqa: E402
from tools.audit_cleric_unknown_native_v111 import _event_key, _record_key  # noqa: E402
from tools.audit_direct_damage_by_profession import (  # noqa: E402
    _scope_reason,
    _strict_classification,
)


TARGET_JOBS = ("圣骑士", "十字军")
DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_CAPTURE_NAMES = (
    "0806硬核第一把.dpscap",
    "0807黑经第一把.dpscap",
    "0814硬核火舞.dpscap",
    "0821硬核.dpscap",
    "0821硬核02.dpscap",
    "0821硬核03.dpscap",
    "0830星期天.dpscap",
)
DEFAULT_OUTPUT = ROOT / "outputs" / "cleric_object_identity_anchors_v130_20260910"


def _scan_capture(path_text: str) -> dict[str, object]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    pairs: dict[tuple[str, int, int], dict[str, object]] = {}

    def feed_events(packet):
        events = original(packet)
        inventory = inspect_udp_damage_records(
            packet.payload, packet.inferred_direction()
        )
        if not inventory.complete:
            return events
        event_map: defaultdict[tuple[object, ...], list[CombatEvent]] = defaultdict(list)
        for event in events:
            if isinstance(event, CombatEvent) and not event.healing and event.amount > 0:
                event_map[_event_key(event)].append(event)
        for record in inventory.damage_records:
            if (
                not record.admissible_fields
                or record.family not in {"direct37", "direct41"}
                or int(record.damage_object_id or 0) <= 0
            ):
                continue
            matches = event_map.get(_record_key(record), ())
            if len(matches) != 1:
                continue
            event = matches[0]
            if event.job not in TARGET_JOBS:
                continue
            if _scope_reason(event) != "included_positive_direct_hit":
                continue
            key = (
                str(event.job),
                int(record.source_actor_id),
                int(record.damage_object_id),
            )
            row = pairs.setdefault(
                key,
                {
                    "hits": 0,
                    "damage": 0,
                    "deterministic": Counter(),
                    "gaps": 0,
                    "gap_damage": 0,
                    "gap_methods": Counter(),
                },
            )
            row["hits"] = int(row["hits"]) + 1
            row["damage"] = int(row["damage"]) + int(record.damage)
            classification = _strict_classification(event)
            if classification == "deterministic":
                row["deterministic"][
                    (int(event.skill_id), str(event.skill_name), str(event.attribution_method))
                ] += 1
            else:
                row["gaps"] = int(row["gaps"]) + 1
                row["gap_damage"] = int(row["gap_damage"]) + int(record.damage)
                row["gap_methods"][
                    (classification, str(event.attribution_method))
                ] += 1
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    candidates = []
    conflicts = []
    unanchored = Counter()
    for (job, actor, object_id), row in pairs.items():
        by_skill: defaultdict[int, Counter[tuple[str, str]]] = defaultdict(Counter)
        for (skill_id, skill_name, method), hits in row["deterministic"].items():
            by_skill[int(skill_id)][(skill_name, method)] += hits
        gap_hits = int(row["gaps"])
        if not gap_hits:
            continue
        if len(by_skill) == 1:
            skill_id = next(iter(by_skill))
            names = Counter()
            methods = Counter()
            for (name, method), hits in by_skill[skill_id].items():
                names[name] += hits
                methods[method] += hits
            candidates.append(
                {
                    "capture": path.name,
                    "job": job,
                    "source_actor_id": actor,
                    "damage_object_id": object_id,
                    "skill_id": skill_id,
                    "skill_name": names.most_common(1)[0][0],
                    "anchor_hits": sum(names.values()),
                    "anchor_methods": dict(methods),
                    "candidate_gap_hits": gap_hits,
                    "candidate_gap_damage": int(row["gap_damage"]),
                    "gap_methods": {
                        f"{classification}|{method}": hits
                        for (classification, method), hits in row["gap_methods"].items()
                    },
                }
            )
        elif len(by_skill) > 1:
            conflicts.append(
                {
                    "capture": path.name,
                    "job": job,
                    "source_actor_id": actor,
                    "damage_object_id": object_id,
                    "anchor_skills": {
                        str(skill): sum(values.values())
                        for skill, values in by_skill.items()
                    },
                    "gap_hits": gap_hits,
                }
            )
        else:
            unanchored[job] += gap_hits
    return {
        "capture": path.name,
        "candidates": candidates,
        "conflicts": conflicts,
        "unanchored_gap_hits": dict(unanchored),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    captures = [args.capture_root / name for name in DEFAULT_CAPTURE_NAMES]
    missing = [str(path) for path in captures if not path.exists()]
    if missing:
        raise FileNotFoundError("; ".join(missing))
    results = []
    with ProcessPoolExecutor(max_workers=max(1, min(args.workers, len(captures)))) as pool:
        futures = {pool.submit(_scan_capture, str(path)): path for path in captures}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: str(row["capture"]))
    summary = Counter()
    by_skill = Counter()
    for result in results:
        for row in result["candidates"]:
            summary[(row["job"], "candidate_gap_hits")] += row["candidate_gap_hits"]
            summary[(row["job"], "candidate_gap_damage")] += row["candidate_gap_damage"]
            by_skill[(row["job"], row["skill_id"], row["skill_name"])] += row["candidate_gap_hits"]
        summary[("全部", "conflicting_object_pairs")] += len(result["conflicts"])
        for job, hits in result["unanchored_gap_hits"].items():
            summary[(job, "unanchored_gap_hits")] += hits
    payload = {
        "schema_version": "cleric-object-identity-anchor-v130",
        "policy": {
            "identity": "capture + source_actor_id + full damage_object_id",
            "skill_anchor": "all deterministic rows for the identity must agree",
            "forbidden": ["elapsed time", "damage amount", "hit cadence", "legacy inferred skill"],
            "production_ready": False,
            "reason": "capture-scoped identity does not yet prove generation boundaries when an ID is reused",
        },
        "summary": [
            {"job": job, "metric": metric, "value": value}
            for (job, metric), value in sorted(summary.items())
        ],
        "candidate_hits_by_skill": [
            {"job": job, "skill_id": skill, "skill_name": name, "hits": hits}
            for (job, skill, name), hits in by_skill.most_common()
        ],
        "captures": results,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# 圣骑士/十字军 damage_object_id 确定锚审计 v130",
        "",
        "本报告不使用时间窗、伤害数值、Hit 节奏或旧技能名。",
        "当前只做影子审计；在对象 ID 代次边界闭合前不进生产。",
        "",
        "|职业|唯一确定锚可候选 Hit|无确定锚缺口 Hit|",
        "|---|---:|---:|",
    ]
    for job in TARGET_JOBS:
        lines.append(
            f"|{job}|{summary[(job, 'candidate_gap_hits')]}|{summary[(job, 'unanchored_gap_hits')]}|"
        )
    lines += ["", "## 主要技能候选", "", "|职业|技能|Hit|", "|---|---|---:|"]
    for (job, skill, name), hits in by_skill.most_common(30):
        lines.append(f"|{job}|{skill} {name}|{hits}|")
    lines += [
        "",
        f"同一对象 ID 出现多个确定技能的冲突对：{summary[('全部', 'conflicting_object_pairs')]} 个。",
    ]
    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
