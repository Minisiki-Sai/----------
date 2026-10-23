"""Offline bounded support-delay search with a full native Blade owner replan.

Other seven axes and all external haste schedules stay fixed. No uploads,
production monkeypatch persistence, or edits to captured events. Candidate
targets are LOWER BOUNDS, never substitutes for the native cooldown clock.
"""
import argparse
import copy
import json
import os
import pickle
import sys
import time
from bisect import bisect_left
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from curve_support_experiment_20261013 import replacement_map


def next_target_floor(real_ready, start, targets):
    """Keep true cooldowns even if targets are missed or too close together."""
    return max(real_ready, next((t for t in targets if t > start + 1e-6), real_ready))


def actual_effect_values(details):
    """Theoretical weights may reorder detail rows; compare stable identities."""
    return {(d['skill'], d['effect']): d['actual'] for d in details}


def peak_targets(casts, event_rows, duration, windows, max_delay):
    """Screen only legal delay locations; final replay resolves cast speed/CD."""
    times = [t for t, _ in event_rows]
    prefix = [0.0]
    for _, value in event_rows:
        prefix.append(prefix[-1] + value)
    result = []
    for start, end in casts:
        length = end - start
        boundary = next((b for a, b in windows if a - 1e-6 <= start < b), start)
        high = min(start + max_delay, boundary - length)
        choices = {start}
        # Half-second grid plus true damage-event boundaries; only a SCREEN.
        choices.update(start + i * .5 for i in range(1, int(max_delay * 2) + 1) if start + i * .5 <= high)
        for t in times[bisect_left(times, end):bisect_left(times, high + length + duration)]:
            choices.update(x for x in (t - length, t - length - duration + 1e-6) if start <= x <= high)
        def value(t):
            return prefix[bisect_left(times, t + length + duration)] - prefix[bisect_left(times, t + length)]
        result.append(max(choices, key=lambda t: (value(t), -t)))
    return result


@contextmanager
def delay_policy(editor_type, targets, job='刀锋舞者'):
    """Process-local research policy. Real simulation still prices every cast."""
    original_actions = editor_type._skill_burst_support_actions
    original_use = editor_type._skill_burst_apply_shared_skill_use
    from dragon_nest_builder.timeline_engine import _timeline_static_descriptor
    observed = [0]
    def actions(self, *args, **kwargs):
        rows = original_actions(self, *args, **kwargs)
        if self.second_job.currentText() != job:
            return rows
        for row in rows:
            values = targets.get(self._skill_burst_skill_key(row.get('skill')), ())
            if values:
                observed[0] += 1
                row['initial_available_at'] = max(float(row.get('initial_available_at', 0)), values[0])
        return rows
    def used(self, skill, start, base, actions, available, *args, **kwargs):
        # Native routine integrates haste, glyphs, cooldown rules and all states.
        original_use(self, skill, start, base, actions, available, *args, **kwargs)
        values = targets.get(self._skill_burst_skill_key(skill), ())
        if self.second_job.currentText() == job and values:
            for index, action in enumerate(actions):
                if self._skill_burst_skill_key(action.get('skill')) == self._skill_burst_skill_key(skill):
                    available[index] = next_target_floor(available.get(index, 0), start, values)
        if self.second_job.currentText() == job and len(args) >= 3:
            # Damaging support (e.g. Statue) is reachable from both schedulers.
            # Preserve native cooldowns and synchronize its damage readiness;
            # otherwise the damage path can bypass the delayed support gate.
            damage_available, damage_rows = args[1], args[2]
            for index, action in enumerate(actions):
                key = self._skill_burst_skill_key(action.get('skill'))
                if key not in targets:
                    continue
                for row_key in (key, damage_rows.get(key)):
                    if row_key in damage_available:
                        damage_available[row_key] = max(damage_available[row_key], available.get(index, 0))
    with patch.object(editor_type, '_skill_burst_support_actions', actions), patch.object(editor_type, '_skill_burst_apply_shared_skill_use', used):
        _timeline_static_descriptor.cache_clear()
        try:
            yield
            if targets:
                assert observed[0] > 0, 'native runtime did not apply delay policy'
        finally:
            _timeline_static_descriptor.cache_clear()


def validate(plan, baseline, source, spec, delayed_skills):
    visible = [s for s in plan['segments'] if not s.get('timeline_hidden') and s.get('kind') != 'invalid']
    for i, row in enumerate(visible):
        assert row.get('kind') != 'invalid'
        if i:
            assert row['start'] >= visible[i - 1]['end'] - 1e-6, 'overlapping owner casts'
        if row.get('skill') == '九天之舞':
            nxt = visible[i + 1]
            assert nxt['skill'] == '九天之舞2阶' and abs(row['end'] - nxt['start']) < 1e-6
            assert all(nxt['end'] <= a + 1e-6 or row['start'] >= b - 1e-6 for a, b in spec['blocked_intervals'])
    # A delayed plan must not improve by introducing earlier support casts.
    for skill in delayed_skills:
        old = [s['start'] for s in baseline['segments'] if s.get('skill') == skill]
        new = [s['start'] for s in plan['segments'] if s.get('skill') == skill]
        assert all(b >= a - 1e-6 for a, b in zip(old, new)), (skill, 'advanced cast')
    assert plan['external_windows'] == baseline['external_windows'], 'external haste or teammate buff schedule changed'


def run(bundle, output, *, job='刀锋舞者', five_only=False, data=None,
        verify_baseline=True, reset_pool=True, refresh_baseline=False):
    from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService
    from dragon_nest_builder.data_loader import DataStore
    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.live_dps.scoring import TeamScoreEngine, canonical_skill
    from dragon_nest_builder.live_dps.theory_plan_builder import _team_score_apply_fixed_axis_damage_glyphs
    from dragon_nest_builder.timeline_parallel import reset_timeline_process_pool
    data = pickle.loads(bundle.read_bytes()) if data is None else data
    store = DataStore.load()
    calculator = BuildCalculator(store)
    service = LegacyTimelineService(store, calculator)
    BuildEditor = service._source_type()
    engine = TeamScoreEngine(store)
    for key, value in data['engine_config'].items():
        setattr(engine, key, value)
    player = next(p for p in data['snapshot'].players if p.job == job)
    source = player.source_id
    baseline = data['plans'][source]
    spec = copy.deepcopy(data['specs'][source])
    spec['excluded_skill_keys'] = baseline.get('_score_excluded_skill_keys', [])
    context = data['context']
    offset = baseline['_score_time_offset']
    windows = tuple((max(0, a - offset), b - offset) for a, b in data['windows'] if b > offset)
    other_indices = [i for i, event in enumerate(context.team_damage_events) if event.source_id != source]
    external_context = replace(context,
        team_damage_events=tuple(context.team_damage_events[i] for i in other_indices),
        baseline_damage_by_event=tuple(context.baseline_damage_by_event[i] for i in other_indices))
    owner_share = 100 * sum(e.amount for e in context.team_damage_events if e.source_id == source) / context.total_team_damage
    def score(plan):
        details = engine._support_scores(data['snapshot'], player, player.job, data['windows'],
            theory_plan=plan, boss_target_ids=data['targets'], counterfactual_context=context)[2]
        mapping = replacement_map(details, context.effects, source, canonical_skill)
        modeled = []
        _, external_parts = engine._counterfactual_support_contributions(external_context,
            replacement_source_id=source, replacement_intervals=mapping, modeled_damage_out=modeled)
        external_gain = 100 * modeled[0] / context.total_team_damage
        counted = [d for d in details if d.included_in_score]
        return dict(support_score=100 * sum(d.actual_contribution for d in counted) / sum(d.theory_contribution for d in counted),
            external_modeled_damage_pp=external_gain, details=[dict(skill=d.skill_name,
                effect=d.effect_key,
                actual=d.actual_contribution, theory=d.theory_contribution,
                completion=100*d.actual_contribution/d.theory_contribution if d.theory_contribution else None)
                for d in details])
    def generate(targets):
        started = time.perf_counter()
        with delay_policy(BuildEditor, targets, job):
            editor = service.extreme_runtime(store.defaults['build1'])
            editor._extreme_monster_defense = engine._score_boss_defense
            editor._extreme_crit_resist_value = engine._score_boss_crit_resist
            editor._extreme_element_resists = {element:engine._score_boss_element_resist for element in ('火','水','光','暗')}
            fresh = editor._extreme_timeline_result_from_spec(spec)
        _team_score_apply_fixed_axis_damage_glyphs(fresh, baseline.get('_score_glyphs', []))
        # Preserve capture metadata but replace ALL native damage/effect outputs.
        return {**baseline, **fresh}, time.perf_counter() - started
    started = time.perf_counter()
    fresh, native_seconds = generate({}) if verify_baseline or refresh_baseline else (baseline, 0.0)
    # Never compare an altered baseline against the previous scored bundle.
    signature = lambda plan: [(s.get('skill'), s.get('kind'), s.get('start'), s.get('end'), s.get('effective_score')) for s in plan['segments']]
    refreshed = refresh_baseline and signature(fresh) != signature(baseline)
    if refresh_baseline:
        # Cohort bundles can predate the Nine-stage pairing fix. Rebuild the
        # owner's immediate baseline under CURRENT rules before both arms;
        # never mix the old saved score into this delay-only comparison.
        baseline = fresh
    if signature(fresh) != signature(baseline):
        print(json.dumps(dict(old_total=baseline['total'], fresh_total=fresh['total'],
            old_profile=baseline.get('_planner_settings'), fresh_profile=fresh.get('_planner_settings'),
            first_differences=[(a,b) for a,b in zip(signature(baseline),signature(fresh)) if a!=b][:3]), ensure_ascii=False), flush=True)
        raise AssertionError('fresh baseline differs from saved native score')
    before = score(baseline)
    old_row = next((r for r in data.get('rows', ()) if r.source_id == source), None)
    if old_row is not None and not refresh_baseline:
        assert abs(before['support_score'] - old_row.support_score) < 1e-7
    print(f'BASELINE_VERIFIED owner={baseline["total"]} support={before["support_score"]:.6f} native={native_seconds:.2f}s', flush=True)
    target_options = {}
    skill_pairs = (('幽灵之魂', '幽灵之魂'), ('九天之舞', '九天之舞2阶')) if job == '刀锋舞者' else (('幽灵之魂', '幽灵之魂'), ('怨恨之像', '怨恨之像'))
    tracked_skills = ('幽灵之魂', '九天之舞', '九天之舞2阶') if job == '刀锋舞者' else ('幽灵之魂', '怨恨之像')
    for limit in ((5,) if five_only else (5, 10)):
        targets = {}
        for root, effect in skill_pairs:
            expectation = next(x.expectation for x in context.effects if x.source_id == source and x.expectation.skill_key == effect)
            expectation = engine._support_expectations_with_glyphs((expectation,), baseline)[0]
            visible = [s for s in baseline['segments'] if s.get('kind') in {'damage', 'support'}]
            casts = [(s['start'], visible[i+1]['end'] if root == '九天之舞' else s['end']) for i, s in enumerate(visible) if s.get('skill') == root]
            events = []
            for i in other_indices:
                event = context.team_damage_events[i]
                t = event.timestamp_ms / 1000
                gain = engine._configured_support_effect_gain_rate_for_recipient(expectation, event.source_id, t, context.boss_hp_segments)
                events.append((t - offset, context.baseline_damage_by_event[i] * gain))
            targets[root] = peak_targets(casts, sorted(events), expectation.duration, windows, limit)
        target_options[f'both_peak_{limit}s'] = targets
        if limit == 10:
            target_options['ghost_peak_10s'] = {'幽灵之魂': targets['幽灵之魂']}
            target_options['dance_peak_10s'] = {'九天之舞': targets['九天之舞']}
    candidates = []
    best = (0.0, 'immediate', baseline, before)
    baseline_counts = Counter(s['skill'] for s in baseline['segments'] if s.get('kind') == 'damage')
    for name, targets in target_options.items():
        candidate_started = time.perf_counter()
        plan, seconds = generate(targets)
        try:
            validate(plan, baseline, source, spec, targets)
        except AssertionError as error:
            candidates.append(dict(name=name, valid=False, reason=str(error), native_seconds=seconds))
            print(f'REJECT {name} {error}', flush=True)
            continue
        after = score(plan)
        # Details are sorted by theoretical score weight, so a valid timing
        # change can reorder rows without changing any observed contribution.
        assert actual_effect_values(before['details']) == actual_effect_values(after['details']), 'actual contribution changed'
        # Fixed baseline scale. Owner's own Buff gain is already in native
        # damage; exclude self from auxiliary gain to avoid counting it twice.
        owner_delta = owner_share * (plan['total'] / baseline['total'] - 1)
        auxiliary_delta = after['external_modeled_damage_pp'] - before['external_modeled_damage_pp']
        joint_delta = owner_delta + auxiliary_delta
        counts = Counter(s['skill'] for s in plan['segments'] if s.get('kind') == 'damage')
        row = dict(name=name, valid=True, targets=targets, native_seconds=seconds,
            candidate_seconds=time.perf_counter()-candidate_started,
            owner_total=plan['total'], owner_change_percent=100*(plan['total']/baseline['total']-1),
            owner_delta_pp=owner_delta, external_support_delta_pp=auxiliary_delta, joint_delta_pp=joint_delta,
            damage_cast_count_changes={k:counts[k]-baseline_counts[k] for k in sorted(counts.keys()|baseline_counts.keys()) if counts[k]!=baseline_counts[k]},
            support_cast_counts={k:sum(s.get('skill')==k for s in plan['segments']) for k in tracked_skills},
            **after)
        candidates.append(row)
        print(f'CANDIDATE {name} joint={joint_delta:+.6f}pp owner={row["owner_change_percent"]:+.3f}% support={after["support_score"]:.4f} native={seconds:.2f}s', flush=True)
        if joint_delta > best[0] + 1e-7:
            best = (joint_delta, name, plan, after)
    report = dict(capture=data['capture'], name=player.name, job=job, source=source,
        refreshed_current_owner_baseline=bool(refreshed),
        baseline_native_seconds=native_seconds, before=before, after=best[3], selected=best[1],
        joint_delta_pp=best[0], candidates=candidates, owner_reference_team_share_percent=owner_share,
        baseline_support_cast_counts={k:sum(s.get('skill')==k for s in baseline['segments']) for k in tracked_skills},
        total_experiment_seconds=time.perf_counter()-started, extra_owner_axes=len(candidates), extra_other_player_axes=0,
        production_enabled=False, uploaded=False,
        limitations=['Bounded 5/10s delay screening, not a global optimum.',
            'Peak screening and external-team final gain use recorded damage and unchanged final scoring evaluator.',
            'Owner cost uses native full-axis damage with a fixed baseline team-share scale; not a full eight-player coupled resimulation.',
            'Measured offline experiment overhead, not measured full integrated score time.'])
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    output.with_suffix('.pickle').write_bytes(pickle.dumps(dict(plan=best[2], source=source, targets=target_options.get(best[1],{})), protocol=5))
    if reset_pool:
        reset_timeline_process_pool()
    print(f'DONE selected={best[1]} report={output}', flush=True)
    return report, best[2]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--job', choices=('刀锋舞者', '噬魂者'), default='刀锋舞者')
    parser.add_argument('--five-only', action='store_true')
    parser.add_argument('--refresh-baseline', action='store_true')
    args = parser.parse_args()
    run(args.bundle, args.output, job=args.job, five_only=args.five_only,
        refresh_baseline=args.refresh_baseline)
