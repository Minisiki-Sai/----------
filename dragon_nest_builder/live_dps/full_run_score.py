"""Whole-run V2: Boss-calibrated output, one whole-run non-output assessment."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import copy, uuid
from .scoring import canonical_skill, grade_for_score, _overflow_balanced_score_contributions
from .score_survival import _apply_survival_penalty_cap
from .score_v2 import RULE_VERSION

def merged_intervals(values):
    result=[]
    for start,end in sorted(values):
        if end <= start: continue
        if result and start <= result[-1][1]+1e-8:
            result[-1]=(result[-1][0],max(end,result[-1][1]))
        else: result.append((start,end))
    return tuple(result)

def aggregate_output(base, parts):
    """Only output uses per-Boss weights. Keep global support/death evidence once."""
    total=sum(max(0.,row.theory_standard_power) for _,row in parts)
    if total <= 0:
        raise ValueError(f'{base.name} 的整场理论输出为空，无法生成 v2 评分')
    def weighted(field):
        return sum(getattr(row,field)*max(0.,row.theory_standard_power) for _,row in parts)/total
    skills=defaultdict(list)
    for boss,row in parts:
        for item in row.skill_details: skills[canonical_skill(item.skill_name)].append(item)
    merged_skills=[]
    for rows in skills.values():
        first=rows[0]
        actual=sum(r.actual_power for r in rows); theory=sum(r.theory_power for r in rows)
        count=sum(r.actual_count for r in rows); tc=sum(r.theory_count for r in rows)
        duration=sum(r.actual_duration for r in rows); expected=sum(r.expected_duration for r in rows)
        levels={r.actual_level for r in rows if r.actual_level is not None}
        merged_skills.append(replace(first,actual_power=actual,theory_power=theory,
            actual_count=count,theory_count=tc,completion=100*actual/theory if theory else 0.,
            actual_duration=duration,expected_duration=expected,
            duration_utilization=duration/expected if expected else 1.,
            usage_rate=count/tc if tc else None,
            high_value=any(r.high_value for r in rows),excluded_from_score=all(r.excluded_from_score for r in rows),
            actual_level=next(iter(levels)) if len(levels)==1 else None,
            level_status=first.level_status if len(levels)<=1 else 'multiple_levels',
            level_candidates=tuple(sorted(set(x for r in rows for x in r.level_candidates)|levels))))
    output=weighted('output_score')
    output_points,support_points=_overflow_balanced_score_contributions(output,base.support_score,base.output_weight,base.support_weight)
    gross=output_points+support_points+base.specialty_contribution
    survival=_apply_survival_penalty_cap(base.survival_detail,gross,base.role,
        canonical_incoming_damage=base.survival_detail.incoming_damage if not base.survival_detail.hp_shadow.available else None)
    total_score=max(0.,gross-survival.penalty)
    actual=sum(r.actual_standard_power for _,r in parts)
    improvements=[];recoverable=0.
    for boss,row in parts:
        weight=max(0.,row.theory_standard_power)/total
        damage_weight=.7 if row.burst_windows else 1.
        recoverable += weight*damage_weight*max(0.,100-row.theory_damage_completion)
        for skill in row.skill_details:
            if skill.excluded_from_score or skill.theory_power<=0: continue
            missing=max(0.,skill.theory_power-skill.actual_power)
            gain=100*damage_weight*missing/total
            if gain<=.02: continue
            improvements.append(dict(direction='输出分',priority='高' if gain>=5 else '中' if gain>=2 else '低',
                item=f'{boss} / {skill.skill_name}',current=f'{skill.actual_count}/{skill.theory_count}次；完成 {skill.completion:.1f}%',
                advice='该Boss阶段争取补齐施放与多段命中，并改善增益窗口；未知倍率记录仍按原有估值。',
                gain=f'整场输出分理论可追回约 +{gain:.2f}',sort_value=gain))
        if row.burst_windows:
            gain=weight*.3*max(0.,100-row.burst_usage);recoverable+=gain
            if gain>.02:
                improvements.append(dict(direction='输出分',priority='高' if gain>=5 else '中' if gain>=2 else '低',
                    item=f'{boss} / 爆发、加速窗口',current=f'兑现率 {row.burst_usage:.1f}%',
                    advice='在该Boss的爆发或加速窗口内安排高收益技能，避免空转。',
                    gain=f'整场输出分理论可追回约 +{gain:.2f}',sort_value=gain))
    evidence=dict(version=RULE_VERSION,scope='full_run',aggregation='theory_damage_weighted_output',
        non_output_basis='whole_run_v1',glyph_basis='whole_run_fixed',
        improvement_rows=sorted(improvements,key=lambda r:-r['sort_value'])[:8],recoverable_output=recoverable,
        **{key:sum(int(r.hit_realization.get(key,0)) for _,r in parts) for key in ('linked_casts','unknown_casts','extra_casts','low_casts')},
        bosses=[dict(boss=b,output_score=r.output_score,theory_standard_power=r.theory_standard_power,
            actual_standard_power=r.actual_standard_power,weight=r.theory_standard_power/total,
            hit_realization=copy.deepcopy(r.hit_realization)) for b,r in parts],
        skills=[dict(s,boss=b) for b,r in parts for s in r.hit_realization.get('skills',[])],
        casts=[dict(c,boss=b) for b,r in parts for c in r.hit_realization.get('casts',[])])
    def timeline(field):
        return tuple(sorted((cast for _,r in parts for cast in getattr(r,field)),key=lambda c:(c.seconds,c.skill_name)))
    notes=tuple(n for n in base.notes if '输出分' not in n and '理论轴' not in n)
    notes += ('v2整场输出分＝Σ(Boss理论标准伤害×该Boss输出分)÷ΣBoss理论标准伤害；每段独立重排理论轴。',
        '所有Boss使用整场统一选出的同一套评分纹章。辅助、职业特色、角色权重和承伤/死亡证据沿用整场统一计算；承伤扣分上限按新总分重算，不逐Boss累加。',
        f'逐次倍率可判定 {evidence["linked_casts"]} 条；其余 {evidence["unknown_casts"]} 条采用原有估值，不作为零命中。')
    return replace(base,grade=grade_for_score(total_score),total_score=total_score,output_score=output,
        actual_standard_power=actual,theory_standard_power=total,theory_damage_completion=100*actual/total,
        timing_efficiency=weighted('timing_efficiency'),high_value_usage=weighted('high_value_usage'),burst_usage=weighted('burst_usage'),
        output_contribution=output_points,support_contribution=support_points,
        survival_detail=survival,survival_penalty=survival.penalty,hit_realization=evidence,notes=notes,
        skill_details=tuple(sorted(merged_skills,key=lambda r:-r.theory_power)),
        output_windows=merged_intervals(w for _,r in parts for w in r.output_windows),
        actual_timeline=timeline('actual_timeline'),theory_timeline=timeline('theory_timeline'),
        burst_windows=tuple(replace(w,label=f'{b} / {w.label}') for b,r in parts for w in r.burst_windows))

def score_full_run(core,payload,snapshot,encounters,benchmark_nest,progress):
    from .team_score_cache import team_score_cache_key
    from .score_hit_evidence import evidence_version
    source=payload['sourceHash']
    if source.startswith('live:'): raise ValueError('v2需要已保存的抓包，请停止抓包后再计算')
    if not encounters: raise ValueError('未识别到主要Boss，无法生成整场v2评分')
    # Target identifiers shared across phases belong to the same encounter.
    # A repeated identifier must never cause its damage to be scored twice.
    seen=set()
    for e in encounters:
        if seen.intersection(e.target_ids): raise ValueError('Boss目标分组重叠，无法可靠汇总整场评分')
        seen.update(e.target_ids)
    key=team_score_cache_key(None,('full-run',RULE_VERSION,evidence_version(),
        [(e.name,sorted(e.target_ids),e.phase_windows) for e in encounters],
        payload.get('bossPreset','红龙佩达'),payload.get('glyphOverrides',{}),payload.get('equipmentOverrides',{}),core.get_build('build1')),
        capture_hash=source,data_root=Path(__file__).resolve().parents[1]/'data')
    force=bool(payload.get('forceRecompute'))
    if force: core._team_score_disk_cache.discard(key)
    cached=None if force else core._team_score_disk_cache.load(key)
    if cached is not None:
        rows,glyphs=cached
    else:
        # Global non-output rules stay global; v2 only replaces the output branch.
        baseline=core.score_replay(dict(payload,scoreRuleVersion='v1',bossTargetIds=[],localComparison=True,
            forceRecompute=force,_fullRunChild=True),lambda p,m:progress(3+int(p*.2),'整场公共评分：'+m))
        with core._state_lock: base_rows=tuple(core._score_results[baseline['scoreKey']])
        glyphs=baseline['glyphsBySource'];parts=defaultdict(list)
        for i,e in enumerate(encounters):
            result=core.score_replay(dict(payload,scoreRuleVersion='v2',bossTargetIds=sorted(e.target_ids),
                localComparison=True,forceRecompute=force,_fullRunChild=True,glyphOverrides=glyphs),
                lambda p,m,i=i,e=e:progress(23+int((i+p/100)*75/len(encounters)),f'{e.name}（{i+1}/{len(encounters)}）：{m}'))
            with core._state_lock: child_rows=tuple(core._score_results[result['scoreKey']])
            for row in child_rows: parts[row.source_id].append((e.name,row))
        rows=tuple(aggregate_output(row,parts[row.source_id]) for row in base_rows)
        rows=tuple(sorted(rows,key=lambda r:-r.total_score))
        core._team_score_disk_cache.save(key,rows,glyphs)
    score_key=uuid.uuid4().hex
    with core._state_lock:
        core._score_results[score_key]=rows
        core._score_keys_by_replay[source]=score_key
        core._score_benchmark_nests[score_key]=benchmark_nest
        while len(core._score_results)>8:
            old=next(iter(core._score_results));core._score_results.pop(old,None);core._score_benchmark_nests.pop(old,None)
    progress(100,'整场v2评分已完成')
    return dict(scoreKey=score_key,scoreRuleVersion='v2',sourceHash=source,bossTargetIds=sorted(seen),
        bossPreset=payload.get('bossPreset','红龙佩达'),scope='full_run',
        rows=[core._score_summary(r) for r in rows],glyphsBySource=copy.deepcopy(glyphs),
        equipmentBySource={r.source_id:copy.deepcopy(r.scoring_equipment) for r in rows},
        diagnostics=dict(versionCacheHit=cached is not None,scoreRuleVersion=RULE_VERSION,
            aggregation='theory_damage_weighted_output',nonOutputBasis='whole_run_v1',bossCount=len(encounters)),
        glyphInference={},theoryPlanner='v2-per-boss-full-run',theoryPlannerError='',theoryDiagnostics={},scoreSync=core._score_sync.status())
