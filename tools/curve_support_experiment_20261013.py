"""Opt-in offline support-curve experiment; never loaded by production.

Freeze all damage actions and accelerator casts. Ordinary support actions may
move only into genuinely free cast slots, with the real segmented CD integrator.
Theory damage screens candidates; the unchanged final support evaluator accepts
them. No fake actual buffs and no cap on auxiliary scores.
"""
import copy
import time
from bisect import bisect_left
from collections import defaultdict

ELIGIBLE=frozenset({'幽灵之魂','怨恨之像'})

def replacement_map(details,effects,source,canonical):
    identities={(e.expectation.skill_key,e.expectation.effect_key):e.expectation.kind
                for e in effects if e.source_id==source}
    return {(canonical(d.skill_name),d.effect_key,
             identities[(canonical(d.skill_name),d.effect_key)]):d.theory_intervals
            for d in details if (canonical(d.skill_name),d.effect_key) in identities}

def merged(rows):
    out=[]
    for a,b in sorted(rows):
        if b<=a: continue
        if out and a<=out[-1][1]+1e-9: out[-1]=(out[-1][0],max(out[-1][1],b))
        else: out.append((a,b))
    return out

def free_slots(occupied,windows):
    occupied=merged(occupied); out=[]
    for a,b in merged(windows):
        at=a
        for x,y in occupied:
            if y<=at: continue
            if x>=b: break
            if x>at: out.append((at,min(x,b)))
            at=max(at,y)
            if at>=b: break
        if at<b: out.append((at,b))
    return out

def first_slot(slots,ready,length):
    return next((max(a,ready) for a,b in slots if max(a,ready)+length<=b+1e-9),None)

def make_schedule(slots,ready_at,cast_length,duration,event_rows,*,delay=10.0):
    """Two schedules: immediate legal cast or peak within delay of that cast."""
    times=[t for t,v in event_rows]; prefix=[0.0]
    for _,v in event_rows: prefix.append(prefix[-1]+v)
    def value(t):
        a=bisect_left(times,t+cast_length)
        b=bisect_left(times,t+cast_length+duration)
        return prefix[b]-prefix[a]
    casts=[]; ready=0.0
    while True:
        earliest=first_slot(slots,ready,cast_length)
        if earliest is None: break
        choices={earliest}
        if delay>0:
            high=earliest+delay
            for a,b in slots:
                lo=max(a,earliest); hi=min(b-cast_length,high)
                if hi<lo: continue
                choices.update((lo,hi))
                l=bisect_left(times,lo+cast_length)
                r=bisect_left(times,hi+cast_length+duration+1e-8)
                for t in times[l:r]:
                    for point in (t-cast_length,t-cast_length-duration+1e-6):
                        if lo<=point<=hi: choices.add(point)
        chosen=max(choices,key=lambda t:(value(t),-t))
        assert chosen>=ready-1e-8
        casts.append((chosen,chosen+cast_length))
        ready=max(chosen+cast_length,ready_at(chosen))
        if len(casts)>10000: raise RuntimeError('non-progressing cooldown')
    return casts,sum(value(a) for a,b in casts)

def local_delay_schedule(original,slots,ready_at,duration,event_rows,can_shift,delay=10.0):
    """Preserve cast count and each cast's speed regime; only delay, never advance.

    A delayed cast must leave the following original cast off cooldown, avoiding
    an unbounded ripple into the frozen damage axis.
    """
    times=[t for t,v in event_rows]; prefix=[0.0]
    for _,v in event_rows: prefix.append(prefix[-1]+v)
    def value(a,b):
        return prefix[bisect_left(times,b+duration)]-prefix[bisect_left(times,b)]
    casts=[]
    for i,(start,end) in enumerate(original):
        length=end-start
        earliest=max(start,ready_at(casts[-1][0])) if casts else start
        next_start=original[i+1][0] if i+1<len(original) else float('inf')
        choices={start}
        for a,b in slots:
            lo=max(a,earliest); hi=min(b-length,start+delay,next_start-length)
            if hi<lo-1e-9: continue
            candidates={lo,hi}
            l=bisect_left(times,lo+length)
            r=bisect_left(times,hi+length+duration+1e-8)
            for t in times[l:r]:
                candidates.update((t-length,t-length-duration+1e-6))
            for t in candidates:
                if (lo<=t<=hi and ready_at(t)<=next_start+1e-8
                    and can_shift(start,end,t,t+length)):
                    choices.add(t)
        chosen=max(choices,key=lambda t:(value(t,t+length),-t))
        casts.append((chosen,chosen+length))
    return casts,sum(value(a,b) for a,b in casts)

def replace_support(plan,skill,casts):
    rows=plan.get('segments',[])
    template=next(s for s in rows if s.get('skill')==skill and s.get('kind')=='support')
    kept=[s for s in rows if not (s.get('skill')==skill and s.get('kind')=='support')]
    for a,b in casts:
        index=next((i for i,s in enumerate(kept) if float(s.get('start',0))>a),len(kept))
        kept.insert(index,{**template,'start':a,'end':b,'detail':'离线实验：固定输出轴，曲线选择辅助时机'})
    changed={**plan,'segments':kept}
    # Keep display rows synchronized with the cast projection used by scoring.
    windows=plan.get('effect_windows',[])
    matching=[w for w in windows if w.get('source')==skill]
    remaining=[w for w in windows if w.get('source')!=skill]
    seen=set()
    for w in matching:
        e=w.get('team_effect',{})
        signature=repr(e.get('attrs',w.get('attrs',{})))
        if signature in seen: continue
        seen.add(signature)
        duration=max(0.0,float(w['end'])-float(w['start']))
        # Direct short buffs have glyph duration resolved by scorer; these
        # display rows are not used as authoritative scoring durations.
        for a,b in casts:
            remaining.append({**w,'start':b,'end':min(float(plan['window']),b+duration),
                              'cast_start':a,'cast_end':b})
    changed['effect_windows']=remaining
    return changed

def optimize(engine,snapshot,output_windows,targets,plans,context):
    from dragon_nest_builder.live_dps.theory_plan_builder import _team_score_theory_damage_objective
    from dragon_nest_builder.live_dps.scoring import canonical_skill
    from dragon_nest_builder.mixins.skill_timeline_schedule import SkillTimelineScheduleMixin,bind_host_globals
    from dragon_nest_builder.shared import display_value
    from dragon_nest_builder.data_loader import norm
    # Saved-bundle probes do not boot the GUI host which normally binds these.
    bind_host_globals({'display_value':display_value,'norm':norm})
    started=time.perf_counter()
    report={'skills':[],'accepted':0,'extra_axes':0,'experimental':True}
    offsets={source:float(p.get('_score_time_offset',0)) for source,p in plans.items()}
    offset=min(offsets.values(),default=0)
    windows=tuple((max(0,a-offset),b-offset) for a,b in output_windows if b>offset)
    weights,streams=_team_score_theory_damage_objective(plans,snapshot.players,windows)
    events=defaultdict(float)
    for source,rows in streams.items():
        total=sum(v for _,v in rows)
        for t,v in rows: events[t]+=v*weights[source]/max(total,1e-9)
    event_rows=sorted(events.items())
    class Cooldown(SkillTimelineScheduleMixin):
        def _skill_burst_mode_boundary_points(self): return ()
        def _skill_burst_sim_cdr_percent_at(self,t,attrs): return 0.0
        def _skill_burst_skill_key(self,skill): return skill
        def _skill_summon_min_cooldown(self,skill): return None
        def _skill_burst_is_refreshable_periodic_damage(self,skill): return False
        def _skill_glyph_effect_value(self,skill,effect): return 0.0 # expectation already glyph-adjusted
    by_source=defaultdict(list)
    for instance in context.effects:
        if instance.expectation.skill_key in ELIGIBLE and instance.expectation.kind=='buff':
            by_source[instance.source_id].append(instance)
    for source,instances in by_source.items():
        if source not in plans: continue
        player=next(p for p in snapshot.players if p.source_id==source)
        plan=plans[source]
        # One authoritative baseline projection, not independently fabricated
        # ideal coverage for the provider's other effects.
        _,_,details=engine._support_scores(snapshot,player,player.job,output_windows,
            theory_plan=plan,boss_target_ids=targets,counterfactual_context=context)
        # Detail.kind is a localized display label (e.g. 团队Buff), whereas
        # the evaluator identity uses the internal buff/debuff kind.
        replacements=replacement_map(details,context.effects,source,canonical_skill)
        eligible_keys={canonical_skill(d.skill_name) for d in details if d.included_in_score}
        def exact(mapping):
            _,parts=engine._counterfactual_support_contributions(context,
                replacement_source_id=source,replacement_intervals=mapping)
            return sum(parts.get((source,*key),0.0) for key in mapping)
        old_value=exact(replacements)
        for instance in instances:
            raw=instance.expectation; skill=raw.skill_key
            if skill not in eligible_keys: continue
            e=engine._support_expectations_with_glyphs((raw,),plan)[0]
            original=[s for s in plan.get('segments',[]) if s.get('skill')==skill and s.get('kind')=='support']
            if not original or e.cooldown<=e.duration or e.duration<=0: continue
            # Only non-damaging independent buff casts: summons/objects with
            # their own DPS and conditional combo branches need separate work.
            if any(float(s.get('effective_score',0))!=0 for s in original):
                report['skills'].append(dict(source=source,skill=skill,accepted=False,
                    skipped='Damaging support cast: cannot move its buff without its own damage.'))
                continue
            length=max(float(s['end'])-float(s['start']) for s in original)
            occupied=[(float(s['start']),float(s['end'])) for s in plan['segments']
                      if s not in original and not s.get('timeline_hidden')
                      and s.get('kind')!='invalid' and float(s.get('end',0))>float(s.get('start',0))]
            slots=free_slots(occupied,windows)
            cd=Cooldown(); cdr=[]
            for w in plan.get('external_windows',[]):
                effect=w.get('team_effect',{}); amount=float(effect.get('attrs',{}).get('冷却缩减%',0))
                if amount: cdr.append((w['start'],w['end'],amount,str((effect.get('provider'),w.get('source')))))
            attrs={'__cdr_windows__':cdr}
            ready=lambda t:cd._skill_burst_cooldown_ready_at(skill,e.cooldown,t,attrs)
            candidates=[]
            for delay in (0,10):
                casts,weight=make_schedule(slots,ready,length,e.duration,event_rows,delay=delay)
                candidates.append((weight,delay,casts))
            original_casts=[(float(s['start']),float(s['end'])) for s in original]
            original_casts.sort()
            # Keep native per-cast animation length only within the same
            # accelerator windows, rather than guessing cross-haste duration.
            speed_windows=[]
            for w in plan.get('external_windows',[]):
                effect_attrs=w.get('team_effect',{}).get('attrs',{})
                if any('动作速度' in str(k) and float(v or 0)!=0 for k,v in effect_attrs.items()):
                    speed_windows.append((float(w['start']),float(w['end'])))
            def same_speed(a,b,c,d):
                lo=min(a,c); hi=max(b,d)
                return not any(lo+1e-8<x<hi-1e-8 for pair in speed_windows for x in pair)
            casts,weight=local_delay_schedule(original_casts,slots,ready,e.duration,
                                               event_rows,same_speed)
            candidates.append((weight,10,casts))
            # The two approximate whole-fight schedules include lost later casts.
            _,delay,casts=max(candidates,key=lambda item:(item[0],-item[1]))
            candidate=replace_support(plan,skill,casts)
            for segment in candidate['segments']:
                if segment.get('skill')==skill and segment.get('kind')=='support':
                    segment['cooldown_ready_at']=ready(float(segment['start']))
                    segment['cdr']=cd._skill_burst_cooldown_cdr_at(float(segment['start']),attrs,cdr)
            modifier=engine._combat_modifier_windows(snapshot,source)
            end=max(snapshot.elapsed_seconds,snapshot.dps_elapsed_seconds,snapshot.boss_effect_current_seconds)
            intervals=engine._planned_support_intervals(candidate,e,end,modifier)
            intersections=tuple((max(a,x),min(b,y)) for a,b in intervals for x,y in output_windows
                                if min(b,y)>max(a,x))
            key=(skill,e.effect_key,e.kind)
            if key not in replacements: continue
            trial={**replacements,key:tuple(merged(intersections))}
            score=exact(trial)
            accepted=score>old_value+1e-9
            record=dict(source=source,job=player.job,skill=skill,before_gain=old_value,
                        candidate_gain=score,accepted=accepted,delay_search_seconds=delay,
                        before_casts=len(original),candidate_casts=len(casts))
            record['moved_casts']=sum((a,b)!=pair for (a,b),pair in zip(casts,original_casts))
            if accepted:
                # Replace contents of the provider's shared plan, not its identity.
                # Captured factual axes and all damage segments remain untouched.
                old_damage=[s for s in plan['segments'] if s.get('kind')=='damage']
                assert old_damage==[s for s in candidate['segments'] if s.get('kind')=='damage']
                plan.update(candidate); replacements=trial; old_value=score
                report['accepted']+=1
            report['skills'].append(record)
    report['seconds']=time.perf_counter()-started
    return report
