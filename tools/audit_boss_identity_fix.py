"""Verify four full replay reports after the missing-birth identity fix."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
from dragon_nest_builder.live_dps.models import DamageTargetSnapshot


def main():
    directory = ROOT / 'outputs/audit_1004_capture_loss'
    baselines = {
        'anxiaodai01': 'anxiaodai01.json',
        'anxiaodai02': 'anxiaodai02.json',
        'bluemoon01': 'bluemoon01_fixed.json',
        'bluemoon02': 'bluemoon02.json',
    }
    reports = []
    for key, baseline in baselines.items():
        before = json.loads((directory / baseline).read_text(encoding='utf-8'))
        after = json.loads((directory / f'{key}_boss_fixed.json').read_text(encoding='utf-8'))
        for field in ('total_damage', 'raw_damage', 'unresolved_damage', 'completion', 'targets'):
            assert before[field] == after[field], (key, field)
        assert {row['id']: row['damage'] for row in before['players']} == {
            row['id']: row['damage'] for row in after['players']
        }, (key, 'player_damage')
        # Identity recovery must not add/remove raw Hits or change skill damage.
        def skill_totals(report):
            return {
                (player['id'], skill['skill_id']): (skill['damage'], skill['hits'])
                for player in report['players'] for skill in player['skills']
            }
        assert skill_totals(before) == skill_totals(after), (key, 'skill_damage_or_hits')
        targets = tuple(DamageTargetSnapshot(**row) for row in after['targets'])
        recognizer = BossRecognizer()
        samples = []
        for _ in range(100):
            started = time.perf_counter()
            result = recognizer.recognize(target_rows=targets)
            samples.append((time.perf_counter() - started) * 1000)
        # Full replay (with event evidence) and compact snapshot must agree.
        assert [(row.name, row.damage) for row in result] == [
            (row['name'], row['damage']) for row in after['bosses']
        ], (key, 'compact_vs_full')
        reports.append({
            'capture': after['capture'], 'key': key,
            'damageAndHitsUnchanged': True,
            'totalDamage': after['total_damage'],
            'bossesBefore': before['bosses'], 'bossesAfter': after['bosses'],
            'inferredBosses': [row.name for row in result if row.confidence == 'inferred'],
            'compactRecognitionMeanMs': sum(samples) / len(samples),
            'compactRecognitionMaxMs': max(samples),
        })
    names = {row['key']: [boss['name'] for boss in row['bossesAfter']] for row in reports}
    assert names['anxiaodai01'] == names['anxiaodai02']
    assert names['bluemoon01'] == names['bluemoon02']
    output = directory / 'boss_identity_fix_acceptance.json'
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{
        k: row[k] for k in ('key', 'damageAndHitsUnchanged', 'totalDamage',
                            'inferredBosses', 'compactRecognitionMeanMs', 'compactRecognitionMaxMs')
    } for row in reports], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
