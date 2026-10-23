"""Local, ledger-calibrated output-score estimates; no cloud writes or replay.

Historical skill-average standard power is reused because per-Hit ledgers and
complete recipient/Boss states are unavailable. These are not formal rescores.
"""
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def skill_key(value):
    return str(value).replace(' ', '').removesuffix('EX')


def output_windows(timeline):
    start = 0
    result = []
    quantum = timeline.get('q', 100)
    for row in timeline.get('o', ()):
        start += row[0]
        result.append((start / quantum, (start + row[1]) / quantum))
    return result


def in_windows(seconds, windows):
    return any(start <= seconds < end for start, end in windows)


def calculate(record, estimate):
    detail = record['detail']
    skills = [row for row in record['skills'] if not row.get('excluded_from_score')]
    actual = sum(row['actual_power'] for row in skills)
    old_theory = sum(row['theory_power'] for row in skills)
    old_completion = actual / old_theory * 100
    assert abs(old_completion - detail['theoryDamageCompletion']) < 1e-6
    # Recover the already-capped 30% component from the historical score.
    old_burst = (detail['outputScore'] - 0.7 * old_completion) / 0.3
    assert -1e-6 <= old_burst <= 100 + 1e-6
    old_burst = max(0.0, min(100.0, old_burst))
    assert abs(0.7 * old_completion + 0.3 * old_burst - detail['outputScore']) < 1e-6

    variant = estimate['estimates'][0]
    counts = variant['counts']
    windows = estimate['original_acceleration_windows'] + estimate['original_action_speed_windows']
    available = output_windows(detail['compactTimeline'])
    inside = lambda seconds: in_windows(seconds, windows) and in_windows(seconds, available)
    old_burst_counts = Counter(skill_key(row['skill']) for row in record['theoryDecoded']
                               if inside(row['start']))
    new_burst_counts = Counter(skill_key(row['skill']) for row in variant['segments']
                               if inside(row['start']))
    new_theory, old_burst_proxy, new_burst_proxy = 0.0, 0.0, 0.0
    budgets, retained = [], []
    ledger_keys = {row['skill_name'] for row in skills}
    for row in skills:
        skill = row['skill_name']
        old_count = row['theory_count']
        if not old_count:
            continue
        unit = row['theory_power'] / old_count
        # Do not silently remove an old scored skill if the simplified build
        # omitted it (e.g. Summon Comet). Its old budget remains unchanged.
        new_count = counts.get(skill, old_count)
        if skill not in counts:
            retained.append(skill)
        new_power = unit * new_count
        new_theory += new_power
        old_window_count = old_burst_counts[skill]
        new_window_count = new_burst_counts[skill] if skill in counts else old_window_count
        old_burst_proxy += old_window_count * unit
        new_burst_proxy += new_window_count * unit
        budgets.append({'skill': skill, 'historical_theory_count': old_count,
                        'estimated_theory_count': new_count, 'historical_unit_power': unit,
                        'old_theory_power': row['theory_power'], 'estimated_theory_power': new_power,
                        'old_window_count': old_window_count, 'new_window_count': new_window_count})
    new_completion = actual / new_theory * 100
    # Average per-cast power cannot reconstruct exact window damage. Calibrate
    # its ratio to the historical window component rather than substituting
    # uncalibrated raw counts. Capped historical windows may have hidden surplus.
    new_burst = min(100.0, old_burst * old_burst_proxy / new_burst_proxy)
    score = 0.7 * new_completion + 0.3 * new_burst
    return {'recordedAt': record['recordedAt'], 'nest': record['nest'],
            'historical_schema': record['schema'], 'historical_output_score': detail['outputScore'],
            'estimated_output_score': score, 'historical_damage_completion': old_completion,
            'estimated_damage_completion': new_completion, 'historical_capped_burst_score': old_burst,
            'estimated_burst_score': new_burst,
            'unchanged_burst_reference_score': 0.7 * new_completion + 0.3 * old_burst,
            'full_burst_cap_reference_score': 0.7 * new_completion + 30,
            'historical_window_component_was_capped': old_burst >= 100 - 1e-6,
            'actual_standard_power': actual, 'historical_theory_standard_power': old_theory,
            'estimated_theory_standard_power': new_theory,
            'old_window_power_proxy': old_burst_proxy, 'new_window_power_proxy': new_burst_proxy,
            'unmatched_historical_skills_retained': retained,
            'new_skills_without_historical_scored_power': sorted(set(counts) - ledger_keys),
            'equipment_source': estimate['equipment_source'], 'skill_budgets': budgets}


def main():
    records = json.loads((ROOT / 'work/sunday_nine_tail_cloud_20261011.json').read_text(encoding='utf-8'))
    inputs = json.loads((ROOT / 'deliverables/九尾充能差距_补齐既有辅助窗口估算_20261011.json').read_text(encoding='utf-8'))
    by_time = {row['recordedAt']: row for row in inputs['results']}
    results = [calculate(record, by_time[record['recordedAt']]) for record in records]
    report = {'method': 'historical standard-power ledger calibrated to new rotation counts and window allocation',
              'formula': '0.70 * total standard damage completion + 0.30 * capped burst-window realization',
              'is_full_rescore': False, 'cloud_records_modified': False,
              'assumptions': [
                  'actual skill standard powers and historical score exclusions remain unchanged',
                  'original Time Acceleration and effective-output windows remain unchanged',
                  'received Spirit Boost/Scorch windows are preserved; Cocktail uses current model 12% CDR in recorded intervals',
                  'historical mean standard power per skill cast is held constant',
                  'old scored skills absent from the simplified build retain their old power budget',
                  'new simulated skills without historical scored power have no invented damage budget',
                  'burst damage is ratio-calibrated; hidden overflow behind a historical 100% cap is unknown',
                  'three records use current standard equipment because no historical equipment was uploaded'],
              'results': results}
    target = ROOT / 'deliverables/九尾充能修复_五场输出分粗估_20261011.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for row in results:
        print(json.dumps({key: row[key] for key in ('recordedAt', 'nest', 'historical_output_score',
                                                   'estimated_output_score', 'estimated_damage_completion',
                                                   'estimated_burst_score')}, ensure_ascii=False))
    print('Saved estimate: ' + str(target))


if __name__ == '__main__':
    main()
