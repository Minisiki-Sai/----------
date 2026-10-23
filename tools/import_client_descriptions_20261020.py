"""Sync changed Reborn descriptions only; preserve all calculator data."""
from pathlib import Path
from collections import defaultdict
import html
import json
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_dungeon_map_catalog import parse_dnt

CLIENT = Path(r"D:\DragonNest\Reborn\Resource_unpacked_all")
AUDIT = Path(r"E:\DragonNest_update_20261020_Reborn")
WORK = ROOT / "work/update_20261020_descriptions"


def render(row, names):
    template = names.get(str(row.get("_SkillExplanationID")), "")
    params = str(row.get("_SkillExplanationIDParam", "")).split(",")
    text = re.sub(r"\{(\d+)\}", lambda m: params[int(m[1])] if int(m[1]) < len(params) else m[0], template)
    text = re.sub(r"#[ywbngr]", "", text).replace("\\n", "\n")
    return '<div class="uistring">' + html.escape(text).replace("\n", "<br/>") + "</div>"


def main():
    names = json.loads((AUDIT / "current_names.json").read_text(encoding="utf-8"))
    changes = json.loads((AUDIT / "text_changes.json").read_text(encoding="utf-8"))
    changed_ids = {str(r["id"]) for r in changes}
    rows = defaultdict(list)
    for path in CLIENT.glob("Resource*/resource/ext/skillleveltable*.dnt"):
        if not path.stat().st_size or "monster" in path.name:
            continue
        for row in parse_dnt(path):
            if str(row.get("_SkillExplanationID")) in changed_ids:
                rows[(str(row.get("_SkillIndex")), row.get("_SkillLevel"), row.get("_ApplyType"))].append(row)
    updated, skipped = [], []
    folder = ROOT / "dragon_nest_builder/data/modular/tables/skills/dngamer_skill_sim_reborn_split/jobs"
    for path in sorted(folder.glob("job_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        before = len(updated)
        for group, modes in data.get("skill_levels", {}).items():
            for mode, skills in modes.items():
                if mode not in ("pve", "pvp"):
                    continue
                for sid, entries in skills.items():
                    for entry in entries:
                        candidates = rows.get((sid, entry.get("_SkillLevel"), 0 if mode == "pve" else 1), [])
                        exact = [r for r in candidates if r.get("RowId") == entry.get("RowId")] if "RowId" in entry else []
                        limited = [r for r in candidates if r.get("_LevelLimit") == entry.get("_LevelLimit")]
                        candidates = exact or limited or candidates
                        descriptions = {render(r, names) for r in candidates}
                        if len(descriptions) > 1:
                            skipped.append([path.name, group, mode, sid, entry["_SkillLevel"]])
                        if len(descriptions) != 1:
                            continue
                        description = descriptions.pop()
                        if entry.get("desc") != description:
                            entry["desc"] = description
                            updated.append([path.name, group, mode, sid, entry["_SkillLevel"]])
        if len(updated) > before:
            backup = WORK / "before" / path.relative_to(ROOT)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    WORK.mkdir(parents=True, exist_ok=True)
    total = []
    for backup in (WORK / "before").rglob("job_*.json"):
        old = json.loads(backup.read_text(encoding="utf-8"))
        new = json.loads((ROOT / backup.relative_to(WORK / "before")).read_text(encoding="utf-8"))
        for group, modes in new["skill_levels"].items():
            for mode, skills in modes.items():
                for sid, entries in skills.items():
                    for index, entry in enumerate(entries):
                        if entry.get("desc") != old["skill_levels"][group][mode][sid][index].get("desc"):
                            total.append([backup.name, group, mode, sid, entry["_SkillLevel"]])
    report = {"updated": total, "ambiguous_preserved": skipped}
    (WORK / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"updated": len(updated), "files": len({r[0] for r in updated}), "ambiguous": len(skipped)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
