"""Check full-replay invariants and write the local identity fix report."""
from collections import Counter
import json
from pathlib import Path
import pickle
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    reports = [json.loads((ROOT / f'work/1013_job_{arm}.json').read_text(encoding='utf8'))
               for arm in ('before', 'after')]
    events = [pickle.loads((ROOT / f'work/1013_job_{arm}.pickle').read_bytes())
              for arm in ('before', 'after')]
    actor = '0x4E7E1B7F'
    players = [next(p for p in r['players'] if p['source_id'] == actor) for r in reports]
    casts = [[e for e in es if type(e).__name__ == 'SkillCastEvent' and e.source_id == actor]
             for es in events]

    def damage(es):
        return Counter((e.timestamp_ms, e.source_id, e.target_id, e.amount, e.critical)
                       for e in es if type(e).__name__ == 'CombatEvent' and not e.healing)

    def heals(es):
        # Restored casts repair healing attribution; amounts/targets must stay unchanged.
        return Counter((e.timestamp_ms, e.target_id, e.amount, e.critical)
                       for e in es if type(e).__name__ == 'CombatEvent' and e.healing)

    def statuses(es):
        return Counter((e.timestamp_ms, e.source_id, e.target_id, e.skill_id, e.effect_key,
                        e.value, e.duration_ms, e.operation)
                       for e in es if type(e).__name__ == 'BossStatusEvent')

    checks = dict(damage_records_unchanged=damage(events[0]) == damage(events[1]),
                  healing_amounts_targets_unchanged=heals(events[0]) == heals(events[1]),
                  status_intervals_unchanged=statuses(events[0]) == statuses(events[1]),
                  all_player_damage_unchanged=
                  {p['source_id']: p['damage'] for p in reports[0]['players']} ==
                  {p['source_id']: p['damage'] for p in reports[1]['players']})
    assert all(checks.values()), checks
    assert players[0]['job'] == '十字军' and players[1]['job'] == '毁灭者'
    result = dict(capture=reports[0]['capture'], actor=actor,
                  before={k: players[0][k] for k in ('name', 'job', 'damage')},
                  after={k: players[1][k] for k in ('name', 'job', 'damage')},
                  cast_records_before=len(casts[0]), cast_records_after=len(casts[1]),
                  casts_before=dict(Counter(e.skill_name for e in casts[0])),
                  casts_after=dict(Counter(e.skill_name for e in casts[1])),
                  invariants=checks,
                  cause='T+40.873s: 211-byte compound status envelope combined Mercenary header actor with BlueMoon self Magic Breath skill at offset 64; false Crusader singleton then rejected real Mercenary actions.',
                  fixes=['Require actual action structure, not 211/214 envelope offsets.',
                         'Preserve compatible first/base-job profile branches against foreign skill singletons.'],
                  notes=['Healing source/skill attribution can recover when real casts return.',
                         'No raw capture, cloud history, installer or score optimizer enablement changes.'])
    output = ROOT / 'work/1013职业误识别_修复对照.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
