"""Package legacy DNT SkillLevel identities for fallback, not the final map.

Combat level inference overlays the current profession's normal/master PvE
rows at runtime, then matches both raw skill ID and row ID. Keep the original
DNT identities here for professions/rows absent from the newer job payloads;
never globally reassign a reused row ID to one newly added skill.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.extract_skill_glyphs import RESOURCE_EXT, parse_dnt


def main() -> None:
    paths = sorted(RESOURCE_EXT.glob('skillleveltable_character*.dnt'))
    if not paths:
        raise FileNotFoundError(RESOURCE_EXT)
    rows: dict[str, set[tuple[int, int]]] = {}
    for path in paths:
        for row in parse_dnt(path):
            sid, level = int(row['_SkillIndex']), int(row['_SkillLevel'])
            if sid > 0 and 0 < level < 200:
                rows.setdefault(str(row['RowId']), set()).add((sid, level))
    result = {
        'schema_version': 1,
        'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        'rows': {key: sorted(value) for key, value in sorted(rows.items(), key=lambda p: int(p[0]))},
    }
    dest = ROOT / 'dragon_nest_builder/data/live_dps_profiles/combat_skill_level_rows_v576.json'
    dest.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'{len(rows)} exact level-row identities -> {dest}')


if __name__ == '__main__':
    main()
