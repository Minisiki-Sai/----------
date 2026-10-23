"""Bounded fury planning shared by score and personal simulation runtimes."""
from __future__ import annotations

from bisect import bisect_left

FURY_POLICY_VERSION = 'key-nodes-and-evidenced-preparation-v1'
FURY_ACTIONS = frozenset({'践踏', '屠戮盛宴', '旋转冲击', '连骨撞击'})


def rank_fury_key_nodes(self, baseline, ready, candidates, damage_available_at,
                        base_cooldowns, support_actions, support_available_at,
                        support_expires_at, active_attrs, current, window,
                        previous_skill, charged_counts, charged_maxes,
                        charged_recharge_at, ammo, fury, guardian_phantoms):
    """Compare roots at 4/6 fury or imminent overflow, not on every action."""
    if (str(self.second_job.currentText()) != '狂战士' or len(ready) < 2
            or not self.build.get('_fury_key_node_search', True)
            or not self._skill_burst_new_scheme_1_enabled()):
        return baseline
    key = lambda row: self._skill_burst_skill_key(row[4].get('skill'))
    ready_fury = [r for r in ready if key(r) in FURY_ACTIONS]
    if not ready_fury:
        return baseline
    spin_due = float(damage_available_at.get('旋转冲击', window)) <= current + 4
    at_cap = fury >= 8 and any(key(r) in ('践踏', '屠戮盛宴') for r in ready_fury)
    if not (at_cap or (fury in (4, 6) and spin_due)):
        return baseline
    if fury < 6 and min(float(damage_available_at.get(k, window)) for k in
                        ('旋转冲击', '连骨撞击')) > current + 8:
        return baseline
    best = max(baseline, key=lambda r: (r[0], r[1], r[2]))
    roots = [best] + [r for r in ready_fury if key(r) != key(best)]
    missing = object()
    old_profile = getattr(self, '_skill_burst_fast_mechanism_profile', missing)
    old_policy = getattr(self, '_skill_burst_fast_mechanism_policy', missing)
    scored = []
    try:
        self._skill_burst_fast_mechanism_policy = True
        self._skill_burst_fast_mechanism_profile = (8., 4, 1, 4, 4)
        for root in roots:
            rank, _ = self._skill_burst_new_scheme_1_rollout(
                root, candidates, damage_available_at, base_cooldowns,
                support_actions, support_available_at, support_expires_at,
                active_attrs, current, window, previous_skill, charged_counts,
                charged_maxes, charged_recharge_at, ammo, fury, guardian_phantoms)
            scored.append((rank, key(root)))
    finally:
        for name, value in (('_skill_burst_fast_mechanism_policy', old_policy),
                            ('_skill_burst_fast_mechanism_profile', old_profile)):
            if value is missing:
                self.__dict__.pop(name, None)
            else:
                setattr(self, name, value)
    chosen = max(scored, key=lambda r: r[0])
    if chosen[1] == key(best) or chosen[0] <= scored[0][0] * 1.002:
        return baseline
    self._skill_burst_new_scheme_1_plan_queue = []
    priority = max(1., max(r[0] for r in baseline) * 1000)
    return [(priority if key(r) == chosen[1] else r[0], *r[1:]) for r in baseline]


def prepare_fury_in_downtime(self, state):
    """Execute one legal zero-damage preparation action, or keep the old wait.

State is the scheduler's local state; cooldown/segment containers are mutated
in place, and scalar time/fury/expiry/previous-skill values are returned. A
permission authorizes the START only; the entire action still occupies time.
"""
    if str(self.second_job.currentText()) != '狂战士':
        return None
    windows = self.build.get('_score_castability_windows', ())
    if not windows:
        return None
    cached = getattr(self, '_fury_preparation_windows', None)
    if cached is None:
        from .live_dps.castability import merge_windows
        rows = merge_windows(windows)
        cached = (rows, tuple(b for _, b in rows))
        self._fury_preparation_windows = cached
    windows, ends = cached
    unavailable = self.build.get('_score_uncastable_windows', ())
    current = state['current']
    limit = min(state['window'], state['blocked_range'][1])
    fury = state['fury'] if state['fury_expires_at'] > current + 1e-6 else 0
    if fury >= 8 and state['fury_expires_at'] > limit + 1e-6:
        return None
    cursor = max(current, state['fury_expires_at']-10) if fury >= 8 else current
    choices = []
    for metric in state['candidates']:
        skill = metric.get('skill', '')
        key = self._skill_burst_skill_key(skill)
        if key not in ('践踏', '屠戮盛宴') or self._skill_burst_fury_gain(skill) <= 0:
            continue
        ready = max(cursor, float(state['damage_available_at'].get(key, 0)))
        for left, right in windows[bisect_left(ends, ready):]:
            start = max(left, ready)
            if start >= limit-1e-6:
                break
            if start > right+1e-9:
                continue
            attrs = self._skill_burst_active_attrs(
                state['support_actions'], state['support_expires_at'], start)
            duration = self._skill_burst_scheduled_cast_time(
                skill, start, state['previous_skill'] if start == current else '', attrs)
            end = start + duration
            # Do not delay the next output window or simulate a cast while dead.
            if end > limit+1e-9:
                break
            if any(start < b and end > a for a, b in unavailable):
                continue
            choices.append((start, end, key, skill, metric, attrs))
            break
    if not choices:
        return None
    start, end, key, skill, metric, attrs = min(choices, key=lambda x: x[:3])
    fury = state['fury'] if state['fury_expires_at'] > start+1e-6 else 0
    cd = self._skill_burst_effective_cooldown(skill, float(metric['base_cooldown']), start, attrs)
    state['damage_available_at'][key] = start + cd
    self._skill_burst_apply_shared_skill_use(
        skill, start, float(metric['base_cooldown']), state['support_actions'],
        state['support_available_at'], state['support_expires_at'],
        state['damage_available_at'], state['damage_row_by_skill'],
        state['damage_base_cooldown_by_skill'], attrs, cast_end=end,
        charged_counts=state['charged_skill_charges'],
        charged_maxes=state['charged_skill_maxes'],
        charged_recharge_at=state['charged_skill_recharge_at'])
    new_fury, _ = self._skill_burst_apply_fury_use(fury, skill)
    gain = self._skill_burst_fury_gain(skill)
    state['segments'].append(dict(
        start=start, end=end, skill=skill, kind='prep', type='停手期准备',
        effective_score=0., raw_effective_score=0., channel_scores={}, dpa=0.,
        detail=f'获得{gain}怒气，当前{new_fury}；可施法参考区间内准备，不计伤害',
        cooldown_ready_at=state['damage_available_at'][key],
        non_output_preparation=True))
    self._skill_burst_new_scheme_1_plan_queue = []
    return end, new_fury, end+60., skill
