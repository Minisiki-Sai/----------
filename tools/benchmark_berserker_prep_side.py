"""Side-chat experiment only: teammate-evidenced downtime fury preparation.

No production source edits, score uploads or capture writes. Patches live solely
inside this process. Windows cover teammate animation plus a 0.5-second tail.
This intentionally measures in-battle downtime, not pre-pull initial fury.
"""
from __future__ import annotations

import argparse
import bisect
import copy
import hashlib
import inspect
import json
import pickle
import statistics
import struct
import textwrap
import time
import zlib
from collections import Counter
from pathlib import Path

import benchmark_berserker_fury_side as base


WINDOWS = []
WINDOW_ENDS = []
DEAD = []
PREP_ENABLED = False
PREP_LOG = []
HOOK_SECONDS = 0.0


def merge(rows):
    out = []
    for start, end in sorted(rows):
        if end <= start:
            continue
        if out and start <= out[-1][1] + 1e-9:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def intersect(rows, other):
    return merge((max(a, c), min(b, d)) for a, b in rows for c, d in other
                 if max(a, c) < min(b, d))


def prepare_evidence(spec, cache, bundle):
    blob = Path(cache).read_bytes()
    assert blob[:6] == b'DNRC2\0'
    n = struct.unpack_from('<I', blob, 6)[0]
    header = json.loads(blob[10:10+n])
    payload = zlib.decompress(blob[10+n:])
    assert hashlib.sha256(payload).hexdigest() == header['payload_sha256']
    snapshot = pickle.loads(payload).snapshot(include_events=True)
    source = '0x508C947F'
    offset = float(spec['build_override']['_score_v2_time_offset'])
    party = {p.source_id for p in snapshot.players} - {source}
    accepted = {'skill_action', 'skill_cast', 'status_skill_action'}
    casts = [c for c in snapshot.skill_cast_events
             if c.source_id in party and c.packet_kind in accepted]
    # Reuse the exact action lengths that the scored actual timeline displays,
    # already adjusted for recorded action speed/variant. Not buff or DOT life.
    lengths = {}
    for other in bundle['specs']:
        other_offset = float(other['build_override']['_score_v2_time_offset'])
        for actor, records in other['score_actual_casts_by_source'].items():
            for r in records:
                start = float(r.get('_score_carry_in_local_start', r['start']))
                end = float(r.get('_score_carry_in_local_end', r['end']))
                lengths[(actor, round((start + other_offset) * 1000), r['skill'])] = max(0., end-start)
    rows = []
    duration_counts = Counter()
    for c in casts:
        key = (c.source_id, c.timestamp_ms, c.skill_name)
        duration = lengths.get(key, 0.)
        duration_counts['matched' if key in lengths else 'missing'] += 1
        duration_counts['positive' if duration > 0 else 'zero_or_missing'] += 1
        start = c.timestamp_ms / 1000 - offset
        rows.append((max(0., start), min(spec['window'], start + duration + .5)))
    rows = merge(rows)
    death_start = None
    dead = []
    for e in sorted(snapshot.player_lifecycle_events, key=lambda e: e.timestamp_ms):
        if e.target_id != source:
            continue
        t = e.timestamp_ms / 1000 - offset
        if not e.active and death_start is None:
            death_start = t
        elif e.active and death_start is not None:
            dead.append((max(0., death_start), min(spec['window'], t)))
            death_start = None
    if death_start is not None:
        dead.append((max(0., death_start), spec['window']))
    blocked = spec['blocked_intervals']
    eligible = intersect(rows, blocked)
    for a, b in dead:
        eligible = [piece for c, d in eligible
                    for piece in ((c, min(d, a)), (max(c, b), d))
                    if piece[1] > piece[0]]
    return merge(eligible), merge(dead), dict(
        capture_hash=header['capture_hash'], offset=offset,
        party_size=len(party)+1, accepted_casts=len(casts),
        input_kinds=sorted(accepted), tail_width=.5, duration_counts=dict(duration_counts),
        interpretation='verified cast start through actual timeline action end + 0.5s',
        downtime_seconds=sum(b-a for a,b in blocked),
        evidenced_downtime_seconds=sum(b-a for a,b in merge(eligible)),
        known_inactive_intervals=dead,
        scope='in-battle downtime only; initial fury unchanged at zero')


def prep_step(self, state):
    global HOOK_SECONDS
    if not PREP_ENABLED:
        return None
    began = time.perf_counter()
    try:
        return _prep_step(self, state)
    finally:
        HOOK_SECONDS += time.perf_counter() - began


def _prep_step(self, s):
    current = s['current']
    limit = min(s['window'], s['blocked_range'][1])
    fury = s['fury'] if s['fury_expires_at'] > current + 1e-6 else 0
    if fury >= 8 and s['fury_expires_at'] > limit + 1e-6:
        return None
    # Do not spam generators at cap; allow a refresh near expiry in a long gap.
    cursor = max(current, s['fury_expires_at'] - 10) if fury >= 8 else current
    choices = []
    for metric in s['candidates']:
        skill = metric.get('skill', '')
        key = self._skill_burst_skill_key(skill)
        if key not in ('践踏', '屠戮盛宴') or self._skill_burst_fury_gain(skill) <= 0:
            continue
        ready = max(cursor, float(s['damage_available_at'].get(key, 0)))
        index = bisect.bisect_left(WINDOW_ENDS, ready)
        for left, right in WINDOWS[index:]:
            start = max(left, ready)
            if start >= limit - 1e-6:
                break
            if start > right + 1e-9:
                continue
            attrs = self._skill_burst_active_attrs(s['support_actions'],
                                                   s['support_expires_at'], start)
            duration = self._skill_burst_scheduled_cast_time(
                skill, start, s['previous_skill'] if start == current else '', attrs)
            end = start + duration
            # Conservatively finish before output resumes. No free cast time,
            # no damage carry-out, and no casting across known inactive states.
            if end > limit + 1e-9:
                break
            if any(start < b and end > a for a,b in DEAD):
                continue
            choices.append((start, end, key, skill, metric, attrs, right))
            break
    if not choices:
        return None
    start, end, key, skill, metric, attrs, allowed_end = min(choices, key=lambda x: x[:3])
    fury = s['fury'] if s['fury_expires_at'] > start + 1e-6 else 0
    cd = self._skill_burst_effective_cooldown(skill, float(metric['base_cooldown']), start, attrs)
    previous_ready = s['damage_available_at'].get(key, 0)
    s['damage_available_at'][key] = start + cd
    self._skill_burst_apply_shared_skill_use(
        skill, start, float(metric['base_cooldown']), s['support_actions'],
        s['support_available_at'], s['support_expires_at'], s['damage_available_at'],
        s['damage_row_by_skill'], s['damage_base_cooldown_by_skill'], attrs,
        cast_end=end, charged_counts=s['charged_skill_charges'],
        charged_maxes=s['charged_skill_maxes'], charged_recharge_at=s['charged_skill_recharge_at'])
    new_fury, _ = self._skill_burst_apply_fury_use(fury, skill)
    row = dict(start=start, end=end, skill=skill, kind='prep', type='停手期准备',
               effective_score=0., raw_effective_score=0., channel_scores={}, dpa=0.,
               detail=f'获得2怒气，当前{new_fury}；队友完整动作及结束后0.5秒内起手；不计伤害',
               cooldown_ready_at=s['damage_available_at'][key], non_output_preparation=True)
    s['segments'].append(row)
    PREP_LOG.append(dict(row, fury_before=fury, fury_after=new_fury,
                         allowed_start_until=allowed_end, previous_ready=previous_ready,
                         blocked_end=limit))
    self._skill_burst_new_scheme_1_plan_queue = []
    return end, new_fury, end + 60., skill


def install_hook():
    original = base.BuildEditor._calculate_skill_burst_sim_preview_data
    source = textwrap.dedent(inspect.getsource(original))
    before = '''        if blocked_range is not None:
            current = min(window, blocked_range[1])
            previous_skill = ""
            continue'''
    after = '''        if blocked_range is not None:
            prepared = _side_prep_step(self, locals())
            if prepared is not None:
                current, fury, fury_expires_at, previous_skill = prepared
                continue
            current = min(window, blocked_range[1])
            previous_skill = ""
            continue'''
    assert source.count(before) == 1, 'Production method changed; inspect hook before running'
    namespace = dict(original.__globals__, _side_prep_step=prep_step)
    exec(compile(source.replace(before, after), '<isolated-fury-prep-experiment>', 'exec'), namespace)
    base.BuildEditor._calculate_skill_burst_sim_preview_data = namespace[original.__name__]
    base.BuildEditor._skill_burst_new_scheme_1_rank_ready = base.fury_rank


def main():
    global WINDOWS, WINDOW_ENDS, DEAD, PREP_ENABLED, PREP_LOG, HOOK_SECONDS
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--modes', nargs='+', default=['old', 'gated', 'prep', 'prep_gated'])
    parser.add_argument('--output', default='outputs/verification/fury_side_prep_action_end.json')
    args = parser.parse_args()
    blob = Path('outputs/verification/ice1018_specs_before.pkl').read_bytes()
    bundle = pickle.loads(blob)
    spec = next(s for s in bundle['specs'] if s['job'] == '狂战士')
    cache = ('C:/Users/gcg59/Documents/DragonNestDpsCapture/replay_cache/'
             '1018黑典冰灵--c91e20ea0e40--66b999d8/'
             r'474613a2ba7f8117a44a8c35f4967486613f05d01b447e16ae2a8b6fdfafe4e6.replaycache')
    began = time.perf_counter()
    WINDOWS, DEAD, evidence = prepare_evidence(spec, cache, bundle)
    WINDOW_ENDS = [b for a,b in WINDOWS]
    evidence['cache_load_and_evidence_seconds'] = time.perf_counter() - began
    print(json.dumps(evidence, ensure_ascii=False), flush=True)
    app = base.QApplication.instance() or base.QApplication([])
    store = base.DataStore.load()
    calculator = base.BuildCalculator(store)
    install_hook()
    report = dict(specs_sha256=hashlib.sha256(blob).hexdigest(), evidence=evidence, runs=[], modes={})
    for repeat in range(args.repeats):
        for mode in args.modes if repeat % 2 == 0 else reversed(args.modes):
            PREP_ENABLED = mode.startswith('prep')
            PREP_LOG = []
            HOOK_SECONDS = 0.
            base.CURRENT_MODE = 'gated' if 'gated' in mode else 'old'
            base.STATS = Counter()
            editor = base.BuildEditor('fury-prep-experiment', store, calculator,
                                      store.defaults['build1'], standardize_default=True)
            began = time.perf_counter()
            result = editor._extreme_timeline_result_from_spec(copy.deepcopy(spec))
            elapsed = time.perf_counter() - began
            preps = [r for r in result['segments'] if r.get('kind') == 'prep']
            # The existing optimizer may internally compare several full axes.
            # Audit the selected axis, not every discarded candidate's casts.
            log_by_cast = {(r['start'], r['end'], r['skill'], r['detail']): r for r in PREP_LOG}
            selected_log = [log_by_cast[(r['start'], r['end'], r['skill'], r['detail'])]
                            for r in preps]
            assert all(r.get('effective_score', 0) == 0 and r.get('raw_effective_score', 0) == 0
                       for r in preps), 'Preparation must not cause phantom damage'
            for r in selected_log:
                assert r['start'] >= r['previous_ready'] - 1e-6
                assert r['end'] <= r['blocked_end'] + 1e-6
                assert any(a-1e-6 <= r['start'] <= b+1e-6 for a,b in WINDOWS)
                assert not any(r['start'] < b and r['end'] > a for a,b in DEAD)
            counted = copy.deepcopy(result)
            for r in counted['segments']:
                if r.get('kind') == 'prep':
                    r['kind'] = 'support'  # Include zero-damage casts in fury conservation audit.
            row = dict(mode=mode, repeat=repeat, seconds=elapsed,
                       **base.stats_for(counted), search=dict(base.STATS),
                       preparation_count=len(preps), preparation_seconds=HOOK_SECONDS,
                       preparation=selected_log)
            report['runs'].append(row)
            for name in args.modes:
                runs = [r for r in report['runs'] if r['mode'] == name]
                if runs:
                    report['modes'][name] = dict(seconds_median=statistics.median(r['seconds'] for r in runs),
                        total=runs[-1]['total'], dps=runs[-1]['dps'], counts=runs[-1]['counts'],
                        preparation_count=runs[-1]['preparation_count'],
                        fury_generated=runs[-1]['fury_generated'], fury_spent=runs[-1]['fury_spent'],
                        fury_overflow=runs[-1]['fury_overflow'],
                        deterministic=len({r['digest'] for r in runs}) == 1)
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k:v for k,v in row.items() if k not in ('opening','preparation')}, ensure_ascii=False), flush=True)
            editor.deleteLater()
            app.processEvents()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
