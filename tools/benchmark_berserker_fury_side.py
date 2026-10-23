"""Isolated fury-policy experiment. Never patches production files or uploads scores.

Frozen score specs hold equipment, skill valuation, team casts and Boss windows
constant. Runtime-only monkey patches are confined to this benchmark process.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pickle
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.editor import BuildEditor


ORIGINAL_RANK = BuildEditor._skill_burst_new_scheme_1_rank_ready
FURY_SKILLS = {'践踏', '屠戮盛宴', '旋转冲击', '连骨撞击'}
CURRENT_MODE = 'old'
STATS = Counter()


def fury_rank(self, ready, candidates, available, cooldowns, supports,
              support_available, support_expires, attrs, current, window,
              previous, counts, maxes, recharges, ammo, fury, guardian):
    baseline = ORIGINAL_RANK(self, ready, candidates, available, cooldowns,
                             supports, support_available, support_expires,
                             attrs, current, window, previous, counts, maxes,
                             recharges, ammo, fury, guardian)
    if str(self.second_job.currentText()) != '狂战士':
        return baseline
    STATS['decisions'] += 1
    if CURRENT_MODE == 'old' or len(ready) < 2:
        return baseline
    key = lambda row: self._skill_burst_skill_key(row[4].get('skill'))
    ready_fury = [r for r in ready if key(r) in FURY_SKILLS]
    if not ready_fury:
        return baseline
    if CURRENT_MODE == 'gated':
        # Resource thresholds, not every producer/consumer opportunity.
        spin_due = float(available.get('旋转冲击', window)) <= current + 4
        at_cap = fury >= 8 and any(key(r) in {'践踏', '屠戮盛宴'} for r in ready_fury)
        if not (at_cap or (fury in (4, 6) and spin_due)):
            return baseline
    # Only optimize near a usable consumer or imminent cap. Do not run a deep
    # whole-rotation search at every filler action.
    if (fury < 6 and min(float(available.get(k, window)) for k in
                        ('旋转冲击', '连骨撞击')) > current + 8):
        return baseline
    best = max(baseline, key=lambda r: (r[0], r[1], r[2]))
    roots = [best]
    roots.extend(r for r in ready_fury if key(r) != key(best))
    profile = ((8.0, 3, 1, 4, 4) if CURRENT_MODE == 'light' else
               (8.0, 4, 1, 4, 4) if CURRENT_MODE == 'gated' else
               (12.0, 5, 2, 5, 5))
    old_profile = getattr(self, '_skill_burst_fast_mechanism_profile', None)
    old_policy = getattr(self, '_skill_burst_fast_mechanism_policy', False)
    STATS['searches'] += 1
    began = time.perf_counter()
    scored = []
    try:
        self._skill_burst_fast_mechanism_policy = True
        self._skill_burst_fast_mechanism_profile = profile
        for root in roots:
            rank, sequence = self._skill_burst_new_scheme_1_rollout(
                root, candidates, available, cooldowns, supports,
                support_available, support_expires, attrs, current, window,
                previous, counts, maxes, recharges, ammo, fury, guardian)
            scored.append((rank, key(root), sequence))
            STATS['roots'] += 1
    finally:
        self._skill_burst_fast_mechanism_policy = old_policy
        self._skill_burst_fast_mechanism_profile = old_profile
        STATS['search_seconds'] += time.perf_counter() - began
    # Compare all alternative roots under one identical horizon/objective.
    chosen = max(scored, key=lambda r: r[0])
    base_value = scored[0][0]
    if chosen[1] == key(best) or chosen[0] <= base_value * 1.002:
        return baseline
    STATS['changed_choices'] += 1
    self._skill_burst_new_scheme_1_plan_queue = []
    priority = max(1.0, max(r[0] for r in baseline) * 1000)
    return [(priority if key(r) == chosen[1] else r[0], *r[1:]) for r in baseline]


def stats_for(result):
    rows = [r for r in result.get('segments', ()) if r.get('kind') in ('damage', 'support')]
    counts = Counter()
    fury = 0
    expires = 0.0
    overflow = generated = spent = 0
    for r in rows:
        skill = r.get('skill', '')
        if float(r.get('start', 0)) >= expires:
            fury = 0
        if skill in ('旋转冲击', '连骨撞击'):
            boosted = '怒气强化生效' in r.get('detail', '')
            counts[skill + ('（强化）' if boosted else '（普通）')] += 1
            if boosted:
                cost = 6 if skill == '旋转冲击' else 2
                if fury < cost:
                    raise AssertionError(f'Illegal fury at {r}: {fury}')
                fury -= cost
                spent += cost
        else:
            counts[skill] += 1
        if skill in ('践踏', '屠戮盛宴') and '获得2怒气' in r.get('detail', ''):
            generated += 2
            overflow += max(0, fury + 2 - 8)
            fury = min(8, fury + 2)
            expires = float(r.get('end', 0)) + 60
    compact = [{k: r.get(k) for k in ('start', 'end', 'skill', 'kind', 'detail',
                                     'effective_score', 'display_name')} for r in rows]
    return dict(total=result.get('total'), raw_total=result.get('raw_total'),
                dps=result.get('dps'), segments=len(rows), counts=dict(counts),
                fury_generated=generated, fury_spent=spent, fury_overflow=overflow,
                digest=hashlib.sha256(json.dumps(compact, sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest(),
                opening=compact[:16])


def main():
    global CURRENT_MODE, STATS
    parser = argparse.ArgumentParser()
    parser.add_argument('--specs', default='outputs/verification/ice1018_specs_before.pkl')
    parser.add_argument('--scenario', choices=('125', 'capture'), required=True)
    parser.add_argument('--modes', nargs='+', default=['old', 'light'])
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = Path(args.specs).read_bytes()
    bundle = pickle.loads(source)
    spec = copy.deepcopy(next(s for s in bundle['specs'] if s['job'] == '狂战士'))
    if args.scenario == '125':
        spec['window'] = 125.0
        spec['blocked_intervals'] = []
        spec['player_buff_clear_times'] = ()
        spec['summon_removal_schedule'] = ()
        spec.pop('boss_hp_segments', None)
        # Same recorded equipment and team-buff timings; continuous output for
        # a simple 125s experiment. Do not use actual cast schedules for self.
    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    BuildEditor._skill_burst_new_scheme_1_rank_ready = fury_rank
    report = dict(scenario=args.scenario, specs_sha256=hashlib.sha256(source).hexdigest(),
                  window=spec['window'], modes={}, runs=[],
                  note='Frozen capture spec; identical valuation and team timings; runtime-only policy changes; no upload.')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Warm imports/data independently; fresh editor per measured axis prevents
    # accidentally reusing a cached completed result from a different policy.
    for repeat in range(args.repeats):
        order = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
        for mode in order:
            CURRENT_MODE = mode
            STATS = Counter()
            editor = BuildEditor('fury-benchmark', store, calculator,
                                 store.defaults['build1'], standardize_default=True)
            began = time.perf_counter()
            result = editor._extreme_timeline_result_from_spec(copy.deepcopy(spec))
            seconds = time.perf_counter() - began
            row = dict(mode=mode, repeat=repeat, seconds=seconds,
                       **stats_for(result), search=dict(STATS))
            report['runs'].append(row)
            for name in args.modes:
                runs = [r for r in report['runs'] if r['mode'] == name]
                if runs:
                    report['modes'][name] = dict(
                        seconds_median=statistics.median(r['seconds'] for r in runs),
                        total=runs[-1]['total'], dps=runs[-1]['dps'],
                        counts=runs[-1]['counts'], fury_overflow=runs[-1]['fury_overflow'],
                        deterministic=len({r['digest'] for r in runs}) == 1)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k:v for k,v in row.items() if k!='opening'}, ensure_ascii=False), flush=True)
            editor.deleteLater()
            app.processEvents()
    BuildEditor._skill_burst_new_scheme_1_rank_ready = ORIGINAL_RANK
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
