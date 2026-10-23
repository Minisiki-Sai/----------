"""Print concise candidate summaries from the large v52 object-lineage audit."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_dungeon_map_catalog import parse_dnt
from tools.build_attack_instance_catalog import parse_act

REPORT = ROOT / "outputs/native_wire_v52_20260909/minus_one_object_lineage_v52_full.json"


def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    by_candidate: Counter[tuple[object, ...]] = Counter()
    by_current: Counter[tuple[object, ...]] = Counter()
    by_job: Counter[str] = Counter()
    for row in report["target_instances"]:
        if row.get("classification") != "zero_conflict_validated":
            continue
        hits = int(row["target_hits"])
        job = next(iter(row.get("jobs", {})), "")
        candidate = row.get("object_candidate") or {}
        canonical = int(candidate.get("canonical_skill", 0) or 0)
        by_job[job] += hits
        by_candidate[
            (
                job,
                canonical,
                candidate.get("skill_name", ""),
                candidate.get("layout", ""),
            )
        ] += hits
        for displayed, count in row.get("current_display_skills", {}).items():
            by_current[(job, displayed, canonical)] += int(count)

    print("BY_JOB")
    for key, count in by_job.most_common():
        print(f"{key}\t{count}")
    print("BY_CANDIDATE")
    for key, count in by_candidate.most_common():
        print("\t".join(map(str, (*key, count))))
    print("CURRENT_TO_CANDIDATE")
    for key, count in by_current.most_common():
        print("\t".join(map(str, (*key, count))))

    downstream: Counter[tuple[object, ...]] = Counter()
    downstream_examples: dict[tuple[object, ...], dict[str, object]] = {}
    for row in report["target_instances"]:
        if row.get("classification") != "production_parser_present_downstream_gap":
            continue
        hits = int(row["target_hits"])
        job = next(iter(row.get("jobs", {})), "")
        first = (row.get("first_observations") or [{}])[0]
        key = (
            job,
            int(first.get("template_id", 0) or 0),
            str(first.get("production_layout", "") or ""),
            str(first.get("layout", "") or ""),
        )
        downstream[key] += hits
        downstream_examples.setdefault(key, {
            "capture": row.get("capture"),
            "pair": row.get("pair"),
            "current_display_skills": row.get("current_display_skills"),
            "methods": row.get("methods"),
            "diagnostics": row.get("diagnostics"),
            "observation": first,
        })
    print("PRODUCTION_DOWNSTREAM_GAPS")
    for key, count in downstream.most_common(80):
        print("\t".join(map(str, (*key, count))))
        print(json.dumps(downstream_examples[key], ensure_ascii=False, sort_keys=True))

    catalog_path = ROOT / "outputs/damage_attribution/attack_instance_catalog_v576.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    print("STATIC_PROJECTILE_92")
    for row in catalog.get("projectile_evidence", []):
        if int(row.get("projectile_template_id", 0) or 0) == 92:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    resource = Path(r"E:\ext0811.rar\Resource00\Resource00\resource")
    projectile_rows = {
        int(row.get("RowId", 0) or 0): row
        for row in parse_dnt(resource / "ext/weapontable_projectile.dnt")
        if int(row.get("RowId", 0) or 0) in {92, 2025}
    }
    file_rows = {
        int(row.get("RowId", 0) or 0): row
        for row in parse_dnt(resource / "ext/filetable.dnt")
    }
    for template_id in (92, 2025):
        print(f"DNT_PROJECTILE_{template_id}")
        row = projectile_rows.get(template_id)
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        if not row:
            continue
        file_id = int(row.get("_ActName", 0) or 0)
        file_row = file_rows.get(file_id)
        print(f"FILETABLE_{file_id}")
        print(json.dumps(file_row, ensure_ascii=False, sort_keys=True))
        file_name = str((file_row or {}).get("_FileName", "") or "")
        if not file_name:
            continue
        matches = tuple(resource.rglob(Path(file_name).name))
        if len(matches) != 1:
            print(f"ACT_PATH_AMBIGUOUS_{template_id}", [str(path) for path in matches])
            continue
        act_path = matches[0]
        print(f"ACT_{template_id}_{act_path.name}")
        parsed = parse_act(act_path)
        for action in parsed.actions:
            print(json.dumps({
                "name": action.name,
                "next_action": action.next_action,
                "length_frames": action.length_frames,
                "signals": [
                    {
                        "type": signal.signal_type,
                        "start": signal.start_frame,
                        "end": signal.end_frame,
                        "order": signal.order,
                        "payload0": int.from_bytes(signal.payload[:4], "little"),
                        "payload": signal.payload.hex(),
                        "strings": list(signal.strings),
                    }
                    for signal in action.signals
                ],
            }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
