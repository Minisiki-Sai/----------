"""Offline accounting audit of completed published-engine runs.

Does not import production scoring modules, rerun axes, change scores, or upload.
"""
from __future__ import annotations
import collections
import hashlib
import json
import marshal
from pathlib import Path
import statistics
import types
from run_published_comparison_batch_20261019 import ROOT, OUT, CASES, ENGINES

DEST = OUT / 'detailed_attribution'
DEST.mkdir(exist_ok=True)

def load(p):
    return json.loads(p.read_text(encoding='utf-8'))

def functions(c):
    found={c.co_qualname:c}
    for v in c.co_consts:
        if isinstance(v, types.CodeType): found.update(functions(v))
    return found

def fingerprint(c):
    def const(v):
        if isinstance(v, types.CodeType):return ('code',fingerprint(v))
        if isinstance(v, tuple):return ('tuple',tuple(const(x) for x in v))
        if isinstance(v, frozenset):return ('frozenset',tuple(sorted((const(x) for x in v),key=repr)))
        return (type(v).__qualname__,v)
    return (c.co_code,c.co_names,c.co_varnames,c.co_freevars,c.co_cellvars,
            c.co_argcount,c.co_posonlyargcount,c.co_kwonlyargcount,c.co_flags,
            c.co_exceptiontable,tuple(const(x) for x in c.co_consts))

def code_provenance():
    result=[]
    for rel,names in [
        ('live_dps/score_v2',['value_actual','native_reference','state_factor','factor_at','actual_reference_attrs','override_multiplier','speed_output_factor']),
        ('live_dps/score_burst',['output_score','score_weight','burst_contributions','filter_burst_windows']),
        ('live_dps/score_rotation',['ScoreRotationMixin._value_actual_casts']),
        ('mixins/skill_timeline_state',['SkillTimelineStateMixin._skill_burst_summon_capacity_full']),
        ('mixins/skill_timeline_output',['SkillTimelineOutputMixin._skill_burst_prorate_window_score']),
        ('live_dps/score_engine',[]),('live_dps/score_support_counterfactual',[])]:
        src=ROOT/'dragon_nest_builder'/f'{rel}.py'
        pyc=ENGINES['4.85']/'dragon_nest_builder'/f'{rel}.pyc'
        published=functions(marshal.loads(pyc.read_bytes()[16:]))
        current={opt:functions(compile(src.read_text(encoding='utf-8-sig'),str(src),'exec',dont_inherit=True,optimize=opt)) for opt in (0,1,2)}
        selected=names or [k for k in published if k.endswith(('._score_player','._support_scores'))]
        matches={k:[opt for opt,tree in current.items() if k in tree and fingerprint(published[k])==fingerprint(tree[k])] for k in selected}
        result.append({'module':rel,'published_sha256':hashlib.sha256(pyc.read_bytes()).hexdigest(),
                       'functions':{k:bool(opts) for k,opts in matches.items()},'matching_compile_optimization':matches})
    return result

# Use the actual published pure calibration functions, not a current-source guess.
v2_code=functions(marshal.loads((ENGINES['4.85']/'dragon_nest_builder/live_dps/score_v2.pyc').read_bytes()[16:]))
pure={}
for key in ('state_factor','factor_at'):
    pure[key]=types.FunctionType(v2_code[key],pure,key,(1.,))
factor_at=pure['factor_at']

def length(ws):return sum(b-a for a,b in ws)
def numeric(d,key,default=0.):return float(d.get(key) or default)
def byskill(rows):
    d=collections.defaultdict(list)
    for row in rows:d[row['skill_name']].append(row)
    return d

def ratio_parts(a0,t0,a1,t1):
    # Exact symmetric decomposition; attribution convention, not a causal rerun.
    if t0<=0 or t1<=0:return None
    return (100*.5*(1/t0+1/t1), -100*.5*(a0+a1)/(t0*t1))

def count_price(n0,p0,n1,p1):
    if n0>0 and n1>0:
        u0,u1=p0/n0,p1/n1
        return {'count':(n1-n0)*(u0+u1)/2,'price':(u1-u0)*(n0+n1)/2,
                'old_mean':u0,'new_mean':u1,'mode':'symmetric'}
    return {'count':p1-p0,'price':0.,'old_mean':p0/n0 if n0 else None,
            'new_mean':p1/n1 if n1 else None,'mode':'entry_exit_not_separable'}

def scoring_skill(row):
    result=dict(row)
    if row.get('excluded_from_score'):
        for k in ('actual_count','theory_count','actual_power','theory_power'):result[k]=0
    return result

def compact_survival(row):
    data=row.get('survival_detail') or {}
    return {k:v for k,v in data.items() if isinstance(v,(int,float,str,bool)) or k=='hp_shadow'}

def inverse_v2(cast,rule,point):
    """Invert only the final value_actual arithmetic; zero-hit input is not recoverable."""
    power=cast['power']
    if not rule:return {'before':power,'after':power,'method':'no_v2_rule'}
    speed=numeric(cast,'action_speed_output_multiplier',1.)
    if not point:
        undo=factor_at(rule,cast['seconds'],speed)*rule.get('full_factor',1.)
        before=power*undo if undo>0 and abs(undo-1)>1e-12 else power
        return {'before':before,'after':power,'method':'unknown_reference_fallback'}
    if point['actual_ratio']==0:
        return {'before':None,'after':power,'method':'zero_hit_cannot_invert'}
    if rule.get('actual_only'):
        fraction=max(0.,numeric(cast,'utilization_ratio'))
        before=power*fraction/point['actual_ratio'] if fraction>1e-9 else power
    else:
        denom=factor_at(rule,cast['seconds'],speed)*(max(1e-12,speed) if point['speed_sensitive'] else 1.)
        before=power*denom/point['actual_ratio']
        if power==0:return {'before':None,'after':0.,'method':'zero_power_cannot_invert'}
    return {'before':before,'after':power,'method':'matched_final_v2_conversion'}

def analyze_player(case,name,a,b,x,y):
    sid=y['source_id']
    pair={'case':case,'capture':name,'source_id':sid,'name':y['name'],'job':y['job'],
          'old_job':x['job'],'versions':{}}
    metrics=['grade','role','role_reason','rotation_job','total_score','output_score','support_score','specialty_score',
             'output_weight','support_weight','specialty_weight','output_contribution','support_contribution',
             'specialty_contribution','survival_penalty','actual_standard_power','theory_standard_power',
             'theory_damage_completion','burst_usage','healing_completion','healing_cast_completion',
             'healing_effective_realization','healing_rescue_completion','healing_damage_reduction_completion',
             'effective_healing','healing_total_casts','healing_meaningful_casts','healing_evaluated_casts',
             'tank_completion','tank_taunt_completion','tank_shell_completion','tank_attack_down_completion',
             'tank_taunt_coverage','tank_taunt_theory_coverage','tank_shell_coverage','tank_shell_theory_coverage',
             'tank_shell_party_size','tank_shell_theory_casts','tank_attack_down_coverage','tank_attack_down_theory_coverage',
             'notes','glyphs','healing_rescue_detail','tank_taunt_evidence','tank_shell_evidence','tank_attack_down_evidence']
    for version,r,full in [('4.78',x,a),('4.85',y,b)]:
        z={k:r.get(k) for k in metrics}
        z['output_seconds']=length(r['output_windows'])
        z['output_windows']=r['output_windows']
        z['panel']=full['plans'].get(sid,{}).get('summary',{})
        z['scoring_equipment']=r.get('scoring_equipment')
        z['survival']=compact_survival(r)
        z['burst_diagnostics']={k:v for k,v in (r.get('hit_realization') or {}).get('burst_diagnostics',{}).items() if not k.endswith('_parts')}
        z['burst_windows']=r.get('burst_windows',[])
        damage=r['theory_damage_completion'];burst=min(100.,max(0.,r['burst_usage']))
        # Confirm formula numerically, rather than assume every version has 30%.
        choices=[w for w in (0.,.3) if abs(r['output_score']-((1-w)*damage+w*burst))<1e-7]
        z['validated_burst_weight']=choices[-1] if choices else None
        z['total_clamp']=r['total_score']-(sum(numeric(r,k) for k in ('output_contribution','support_contribution','specialty_contribution'))-r['survival_penalty'])
        pair['versions'][version]=z
    pair['total_decomposition']={k:numeric(y,k)-numeric(x,k) for k in ('output_contribution','support_contribution','specialty_contribution')}
    pair['total_decomposition']['survival']=x['survival_penalty']-y['survival_penalty']
    pair['total_decomposition']['clamp']=pair['versions']['4.85']['total_clamp']-pair['versions']['4.78']['total_clamp']
    pair['total_error']=sum(pair['total_decomposition'].values())-(y['total_score']-x['total_score'])
    for version,r in [('4.78',x),('4.85',y)]:
        for field,weight,contrib in [('output_score','output_weight','output_contribution'),('support_score','support_weight','support_contribution')]:
            pair['versions'][version][contrib+'_overflow_adjustment']=numeric(r,contrib)-numeric(r,field)*numeric(r,weight)
    factors=ratio_parts(x['actual_standard_power'],x['theory_standard_power'],y['actual_standard_power'],y['theory_standard_power'])
    oldskills={s['skill_name']:s for s in x['skill_details']};newskills={s['skill_name']:s for s in y['skill_details']}
    rules={s['skill']:s for s in (y.get('hit_realization') or {}).get('skills',[])}
    points={(s['skill'],round(s['seconds']*1000)):s for s in (y.get('hit_realization') or {}).get('casts',[])}
    timelines={v:byskill(r['actual_timeline']) for v,r in [('4.78',x),('4.85',y)]}
    theorylines={v:byskill(r['theory_timeline']) for v,r in [('4.78',x),('4.85',y)]}
    skills=[];cast_ledger=[]
    for skill in dict.fromkeys([*newskills,*oldskills]):
        u=oldskills.get(skill,{});v=newskills.get(skill,{})
        su,sv=scoring_skill(u),scoring_skill(v)
        ac=count_price(numeric(su,'actual_count'),numeric(su,'actual_power'),numeric(sv,'actual_count'),numeric(sv,'actual_power'))
        tc=count_price(numeric(su,'theory_count'),numeric(su,'theory_power'),numeric(sv,'theory_count'),numeric(sv,'theory_power'))
        row={'skill':skill,'old':u,'new':v,'actual_change':ac,'theory_change':tc,'rule':rules.get(skill),
             'scored_old':su,'scored_new':sv,'ratio_attribution':{},'timeline_reconciliation':{},'v2_final_step':{},'matched_casts':{}}
        for version,detail in [('4.78',su),('4.85',sv)]:
            for side,ts,key in [('actual',timelines,'actual_power'),('theory',theorylines,'theory_power')]:
                row['timeline_reconciliation'][version+'_'+side]=sum(c['power'] for c in ts[version][skill])-numeric(detail,key)
        if factors:
            row['ratio_attribution']={'actual_count':ac['count']*factors[0],'actual_price':ac['price']*factors[0],
                                      'theory_count':tc['count']*factors[1],'theory_price':tc['price']*factors[1]}
        oldcasts={round(c['seconds']*1000):c for c in timelines['4.78'][skill]}
        newcasts={round(c['seconds']*1000):c for c in timelines['4.85'][skill]}
        methods=collections.Counter();before=after=0.;unknown_zero=0;pointrows=[]
        matched=[t for t in newcasts if t in oldcasts]
        row['matched_casts']={'same_timestamp':len(matched),'old_only':len(oldcasts.keys()-newcasts.keys()),
                              'new_only':len(newcasts.keys()-oldcasts.keys()),
                              'old_power':sum(oldcasts[t]['power'] for t in matched),
                              'new_power':sum(newcasts[t]['power'] for t in matched)}
        for stamp in sorted(oldcasts.keys()|newcasts.keys()):
            oc,nc=oldcasts.get(stamp),newcasts.get(stamp)
            point=points.get((skill,stamp)) if nc else None
            inv=inverse_v2(nc,rules.get(skill),point) if nc else None
            cast_ledger.append({'skill':skill,'seconds':stamp/1000,'old':oc,'new':nc,'hit_evidence':point,'final_v2_step':inv})
            if not nc:continue
            methods[inv['method']]+=1
            if inv['before'] is not None:before+=inv['before'];after+=inv['after']
            else:unknown_zero+=1
            if point:pointrows.append(point)
        row['v2_final_step']={'recovered_input':before,'same_rows_output':after,'net_change':after-before,
                              'noninvertible_zero_rows':unknown_zero,'methods':dict(methods),
                              'matched_evidence_casts':len(pointrows),
                              'mean_full_rate':statistics.mean(p['full_rate'] for p in pointrows) if pointrows else None,
                              'mean_boss_rate':statistics.mean(p['boss_rate'] for p in pointrows) if pointrows else None,
                              'mean_hit_rate':statistics.mean(p['hit_rate'] for p in pointrows if p.get('hit_rate') is not None) if any(p.get('hit_rate') is not None for p in pointrows) else None,
                              'estimated_casts':sum(bool(p.get('estimated')) for p in pointrows)}
        skills.append(row)
    pair['skills']=sorted(skills,key=lambda s:abs(sum(s['ratio_attribution'].values())),reverse=True)
    pair['output_ratio_error']=sum(sum(s['ratio_attribution'].values()) for s in skills)-(y['theory_damage_completion']-x['theory_damage_completion'])
    pair['skill_total_errors']={ver:{key:sum(numeric(scoring_skill(s),key) for s in r['skill_details'])-r[total]
                                      for key,total in [('actual_power','actual_standard_power'),('theory_power','theory_standard_power')]}
                               for ver,r in [('4.78',x),('4.85',y)]}
    supports=[]
    os={(s['skill_name'],s['effect_key']):s for s in x['support_details']};ns={(s['skill_name'],s['effect_key']):s for s in y['support_details']}
    totals=[]
    for r in (x,y):
        totals.append((sum(numeric(s,'actual_contribution') for s in r['support_details'] if s['included_in_score']),
                       sum(numeric(s,'theory_contribution') for s in r['support_details'] if s['included_in_score'])))
    sf=ratio_parts(*totals[0],*totals[1])
    for key in dict.fromkeys([*ns,*os]):
        u,v=os.get(key,{}),ns.get(key,{})
        inc0=bool(u.get('included_in_score'));inc1=bool(v.get('included_in_score'))
        da=numeric(v,'actual_contribution')*inc1-numeric(u,'actual_contribution')*inc0
        dt=numeric(v,'theory_contribution')*inc1-numeric(u,'theory_contribution')*inc0
        supports.append({'skill':key[0],'effect':key[1],'old':u,'new':v,
                         'actual_term':da*sf[0] if sf else None,'theory_term':dt*sf[1] if sf else None})
    pair['supports']=sorted(supports,key=lambda s:abs((s['actual_term'] or 0)+(s['theory_term'] or 0)),reverse=True)
    pair['support_totals']=totals
    pair['support_ratio_errors']=[(100*aa/tt-r['support_score']) if tt>0 else None for (aa,tt),r in zip(totals,(x,y))]
    pair['support_delta_error']=sum((s['actual_term'] or 0)+(s['theory_term'] or 0) for s in supports)-(y['support_score']-x['support_score']) if sf else None
    pair['replay_player']={}
    heals={}
    for ver,full in [('4.78',a),('4.85',b)]:
        rp=next((p for p in full['replay']['players'] if p['source_id']==sid),{})
        pair['replay_player'][ver]={k:rp.get(k) for k in ('damage','healing','hits','incoming_damage','active_seconds')}
        for sk in rp.get('skills',[]):
            if sk.get('healing',0)>0:heals.setdefault(sk['name'],{})[ver]=sk
    pair['healing_skills']=heals
    path=DEST/f'{case}_{sid.replace("0x", "")}_casts.json'
    path.write_text(json.dumps(cast_ledger,ensure_ascii=False,indent=2),encoding='utf-8')
    pair['cast_ledger']=path.name
    pair['casts']=len(cast_ledger)
    return pair

def main():
    result={'code_provenance':code_provenance(),'cases':[],'players':[]}
    for case,name in CASES:
        a=load(OUT/f'{case}_v4.78.json');b=load(OUT/f'{case}_v4.85.json')
        old={r['source_id']:r for r in a['scoreRowsDetailed']};new={r['source_id']:r for r in b['scoreRowsDetailed']}
        assert old.keys()==new.keys()
        battle={'case':case,'name':name,'versions':{}}
        for ver,full in [('4.78',a),('4.85',b)]:
            replay=full['replay']
            battle['versions'][ver]={k:replay.get(k) for k in ('elapsed_seconds','total_damage','total_healing','unattributed_healing_total','factual_healing_total','dungeon_map','instance_completion')}
            battle['versions'][ver]['incoming_events']=len(replay.get('incoming_damage_events',[]))
            battle['versions'][ver]['boss_encounters']=replay.get('boss_encounters')
            battle['versions'][ver]['schema']=full['schema']
            battle['versions'][ver]['capture_sha256']=full['captureSha256']
        result['cases'].append(battle)
        for sid in new:
            r=analyze_player(case,name,a,b,old[sid],new[sid]);result['players'].append(r)
        print(case,'players',len(new),'skills',sum(len(r['skills']) for r in result['players'] if r['case']==case),flush=True)
    checks={
        'players':len(result['players']),
        'skills':sum(len(r['skills']) for r in result['players']),
        'support_effects':sum(len(r['supports']) for r in result['players']),
        'cast_pairs':sum(r['casts'] for r in result['players']),
        'max_total_error':max(abs(r['total_error']) for r in result['players']),
        'max_output_ratio_error':max(abs(r['output_ratio_error']) for r in result['players']),
        'max_support_delta_error':max(abs(r['support_delta_error'] or 0) for r in result['players']),
        'max_skill_total_error':max(abs(v) for r in result['players'] for z in r['skill_total_errors'].values() for v in z.values()),
        'max_skill_timeline_error':max(abs(v) for r in result['players'] for s in r['skills'] for v in s['timeline_reconciliation'].values()),
        'max_support_ratio_error':max(abs(v) for r in result['players'] for v in r['support_ratio_errors'] if v is not None),
        'unvalidated_output_formula':[(r['case'],r['name'],v) for r in result['players'] for v,z in r['versions'].items() if z['validated_burst_weight'] is None],
    }
    result['validation']=checks
    assert checks['players']==48
    assert checks['max_total_error']<1e-8 and checks['max_output_ratio_error']<1e-8
    assert checks['max_support_delta_error']<1e-8 and checks['max_support_ratio_error']<1e-8
    assert checks['max_skill_total_error']<.01 and checks['max_skill_timeline_error']<.01
    assert not checks['unvalidated_output_formula']
    (DEST/'attribution.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False,indent=2))
    print(json.dumps(result['code_provenance'],ensure_ascii=False,indent=2))

if __name__=='__main__':main()
