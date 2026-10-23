"""All supported professions: reconcile frozen A/B results, without rescoring/upload."""
from __future__ import annotations
import argparse,collections,hashlib,json,statistics
from pathlib import Path
import audit_score_changes_20261019 as audit
import report_score_changes_20261019 as view
from run_published_comparison_batch_20261019 import ROOT,OUT as PREVIOUS,CASES as OLD_CASES,CAP,ENGINES,sha

OUT=ROOT/'outputs/verification/all_jobs_published_ab_20261019'
DEST=OUT/'detailed_attribution'
audit.DEST=DEST
view.DEST=DEST
E,f,p,m,pct,delta,table,para,detail=view.E,view.f,view.p,view.m,view.pct,view.delta,view.table,view.para,view.detail
REPORT_NAME='全职业v4.78与v4.85逐技能评分变化详解_20261019.html'
def person_file(r):return 'player_'+view.aid(r)+'.html'
view.link=lambda r:'<a href="'+person_file(r)+'">'+E(r['name'])+' · '+E(r['job'])+'</a>'

def catalog_row(catalog,job,skill):
    aliases={'猛砍姿态':'挥砍姿态','猛砍站姿':'猛砍站姿','阿兹纳之约':'阿兹那之约'}
    rows=catalog.get(job,{})
    return rows.get(skill) or rows.get(aliases.get(skill)) or {}

def within(t, windows):
    return any(float(a)-.001 <= t <= float(b)+.001 for a,b in windows)

def enrich_evidence(r, old, new, full_old, full_new):
    """Keep observations separate from hypotheses about the game."""
    casts=json.loads((DEST/r['cast_ledger']).read_text(encoding='utf-8'))
    observed={}
    for trace in full_new.get('valueActualTraces',[]):
        if trace.get('source_ids') != [r['source_id']]:continue
        after={(c['key'],round(c['seconds']*1000)):c for c in trace['after']}
        for c in trace['before']:
            key=(c['key'],round(c['seconds']*1000))
            if key in after:observed[key]={'before':c['power'],'after':after[key]['power']}
    # Ordinary positive replay references are already exported by the engine.
    # Use only explicit offsets and unique exact timestamps; never guess an
    # offset or reuse a summon/periodic reference as an ordinary cast.
    plan=full_new.get('plans',{}).get(r['source_id'],{})
    refs=collections.defaultdict(list)
    if plan.get('_score_time_offset') is not None:
        offset=max(0,float(plan['_score_time_offset']))
        for ref in (plan.get('_score_actual_cast_segments') or {}).get(r['source_id'],[]):
            refs[(ref['skill'],round((offset+ref['start'])*1000))].append(ref['_score_actual_damage'])
    rules={s['skill']:s.get('rule') or {} for s in r['skills']}
    reference_mismatches=[]
    grouped=collections.defaultdict(list)
    for c in casts:
        key=(c['skill'],round(c['seconds']*1000));direct=observed.get(key)
        ref=refs.get(key,[]);rule=rules.get(c['skill'],{});point=c.get('hit_evidence') or {}
        if len(ref)==1 and c['new'] and point and not rule.get('actual_only') and not rule.get('burst_only'):
            speed=c['new'].get('action_speed_output_multiplier',1)
            factor=audit.factor_at(rule,c['seconds'],speed)*max(1e-12,speed if point['speed_sensitive'] else 1)
            predicted=ref[0]/max(factor,1e-12)*point['actual_ratio']
            if abs(predicted-c['new']['power'])<.01:
                direct={'before':ref[0],'after':c['new']['power'],'method':'exported_replay_reference_exact_timestamp_and_v2_result_verified'}
            else:reference_mismatches.append({'skill':c['skill'],'seconds':c['seconds'],'reference':ref[0],'predicted':predicted,'final':c['new']['power']})
        if direct and c['new']:
            assert abs(c['new']['power']-direct['after'])<.01
            c['direct_v2_observation']=direct
        grouped[c['skill']].append(c)
    if observed or refs:(DEST/r['cast_ledger']).write_text(json.dumps(casts,ensure_ascii=False,indent=2),encoding='utf-8')
    r['exported_reference_mismatches']=reference_mismatches
    speed_gaps=[]
    for c in casts:
        row=c.get('new') or {};point=c.get('hit_evidence') or {};rule=rules.get(c['skill'],{})
        nominal=float(row.get('action_speed_output_multiplier',1));reference=float(point.get('state_ratio',1))
        if (point.get('speed_sensitive') and not rule.get('actual_only') and reference>0
                and nominal/reference>1.10 and 99.9<=float(point.get('full_rate',0))<=105):
            speed_gaps.append({'skill':c['skill'],'seconds':c['seconds'],'speed_percent':row.get('action_speed_percent'),
                'nominal_speed_factor':nominal,'hit_reference_speed_factor':reference,'expected_hits':point.get('expected_hits'),
                'matched_hits':point.get('matched_hits'),'full_rate':point['full_rate'],
                'actual_ratio':point.get('actual_ratio'),'before_inverse':(c.get('final_v2_step') or {}).get('before'),
                'after':row.get('power'),'estimated_hit_link':point.get('estimated'),
                'speed_model':rule.get('speed_model')})
    r['speed_reference_gaps']=speed_gaps
    for z in speed_gaps:
        theory=[c for c in new['theory_timeline'] if c['skill_name']==z['skill'] and c.get('action_speed_output_multiplier',1)>1.1]
        z['theory_accelerated_casts']=len(theory)
        z['theory_nominal_factors']=sorted({round(c['action_speed_output_multiplier'],5) for c in theory})
    for s in r['skills']:
        cs=grouped[s['skill']]
        missing=[c for c in cs if c['old'] and not c['new']]
        added=[c for c in cs if c['new'] and not c['old']]
        s['cast_selection_evidence']={
            'old_only_times':[c['seconds'] for c in missing],
            'new_only_times':[c['seconds'] for c in added],
            'old_only_outside_new_windows':[c['seconds'] for c in missing if not within(c['seconds'],new['output_windows'])],
            'new_only_outside_old_windows':[c['seconds'] for c in added if not within(c['seconds'],old['output_windows'])]}
        ds=[c['direct_v2_observation'] for c in cs if c.get('direct_v2_observation')]
        s['direct_v2_observation']={'casts':len(ds),'before':sum(d['before'] for d in ds),'after':sum(d['after'] for d in ds)}
        if s['skill'] in {'致残惩罚者攻击','神圣之锤攻击','觉醒•黑暗之刃'}:
            s['release_event_examples']={ver:[e for e in full['replay'].get('skill_cast_events',[]) if e.get('source_id')==r['source_id'] and e.get('skill_name')==s['skill']][:6]
                for ver,full in [('4.78',full_old),('4.85',full_new)]}
    r['cast_pairing_validation']={}
    for version,source in [('4.78',old),('4.85',new)]:
        actual=source['actual_timeline']
        keys=collections.Counter((c.get('skill_name'),round(c['seconds']*1000)) for c in actual)
        r['cast_pairing_validation'][version]={'timeline_rows':len(actual),'duplicate_skill_millisecond_rows':sum(n-1 for n in keys.values() if n>1)}

def plain_skill_reasons(r):
    rows=[]
    for s in r['skills']:
        a,b=s['scored_old'],s['scored_new'];facts=[]
        ac,bc=a.get('actual_count',0),b.get('actual_count',0)
        at,bt=a.get('theory_count',0),b.get('theory_count',0)
        facts.append('计分次数：实战 '+str(ac)+'→'+str(bc)+'，理论 '+str(at)+'→'+str(bt)+'。')
        e=s['cast_selection_evidence']
        if e['old_only_times']:
            facts.append('旧版独有释放 '+str(len(e['old_only_times']))+' 次，其中 '+str(len(e['old_only_outside_new_windows']))+' 次落在新版个人有效区间之外；这是区间差异证据，不直接等同于漏解析。')
        if e['new_only_times']:facts.append('新版独有释放 '+str(len(e['new_only_times']))+' 次，具体时刻保留在逐次证据中。')
        if s['old'].get('excluded_from_score') and not s['new'].get('excluded_from_score'):
            facts.append('旧版豁免，新版纳入；实战和理论都按真正计分值比较。')
        st=s['static_definition'];x,y=st['old'],st['new']
        if x and y and x.get('multiplier')!=y.get('multiplier'):
            facts.append('发布包基础倍率 '+f(x.get('multiplier'))+'→'+f(y.get('multiplier'))+'；已直接核对两版技能库。')
        if x and y and x.get('cast_time')!=y.get('cast_time'):
            facts.append('动作耗时定义改变，会影响排轴可容纳的次数。')
        v=s.get('rule') or {}
        if v.get('full_factor',1)!=1:
            facts.append('新版最终参考层又把内部倍率 '+f(v.get('old_multiplier'))+' 换为 '+f(v.get('full_multiplier'))+'，不能只看基础表。')
        step=s['v2_final_step']
        direct=s.get('direct_v2_observation',{})
        if direct.get('casts'):
            facts.append('另有 '+str(direct['casts'])+' 次可从引擎导出的同名同时刻逐次参考值核到：'+m(direct['before'])+'→'+m(direct['after'])+'；按发布公式正向核验与最终值相符，不仅依赖反推。')
        if step['matched_evidence_casts']:
            facts.append('新版同条件下，能反推的最终命中/校准换算使实战标准量变化 '+delta(step['net_change']/1e6)+' 百万（并非单纯漏Hit扣减）。')
        if v.get('basis_uncertain'):facts.append('注意：引擎明确标注完整倍率仍为近似/未闭合，不能认定玩家应当达到该理论值。')
        if r['job'] in ('重炮手','机械大师') and s['skill']=='重磅鸭':
            facts.append('已核发布字节码：旧版会被召唤物数量上限阻止继续排鸭；新版给重磅鸭放行，因为投蛋本身有直接伤害和刷新易伤，不应等旧鸭消失才可投。新版还取消用鸭子存续时长摊薄投蛋的瞬间伤害。理论次数/单价因此都可能上升，不是实战少抓包。')
        speed_examples=[z for z in r.get('speed_reference_gaps',[]) if z['skill']==s['skill']]
        if speed_examples:
            z=speed_examples[0]
            facts.append('重点内部口径差：'+str(len(speed_examples))+' 次接近满Hit时，计价名义加速仍大于Hit参考增长。例 '+f(z['seconds'],3)+'秒，'+f(z['matched_hits'],0)+'/'+f(z['expected_hits'],0)+' Hit，名义系数'+f(z['nominal_speed_factor'],2)+'、Hit参考增长'+f(z['hit_reference_speed_factor'],2)+'；不能直接归因于玩家少打Hit。')
        ra=s['ratio_attribution'];top=max(ra,key=lambda k:abs(ra[k])) if ra else None
        labels={'actual_count':'纳入的实战次数','actual_price':'实战平均单次标准价值','theory_count':'理论要求次数','theory_price':'理论平均单次标准价值'}
        if top:facts.append('最大账目变化项是“'+labels[top]+'”，分摊 '+delta(ra[top])+' 个整场完成率百分点。')
        rows.append([E(s['skill']),delta(view.impact(s))+' pp','<br>'.join(E(z) for z in facts)])
    return table(['技能','对整场完成率差的分摊','白话解释：此次直接核到什么'],rows)+para('表中“最大账目变化项”是精确加总的分摊，不是假定已隔离每个 Buff 的因果效果。若需具体某次为何价差，展开该技能的数值与全部逐次记录；未独立确定的状态来源不伪造结论。')

def suspicious_gaps(players):
    rows=[]
    for r in players:
        for s in r['skills']:
            b=s['scored_new'];n,t=b.get('actual_count',0),b.get('theory_count',0)
            rate=s['v2_final_step'].get('mean_full_rate')
            if not n or not t or rate is None or not b.get('theory_power'):continue
            rough=100*n/t*rate/100
            real=100*b.get('actual_power',0)/b['theory_power']
            if abs(rough-real)<15:continue
            rows.append({'case':r['case'],'actor':r['source_id'],'job':r['job'],'name':r['name'],'skill':s['skill'],
                'rough_count_times_mean_hit_percent':rough,'actual_damage_completion_percent':real,'gap_pp':real-rough,
                'basis_uncertain':bool((s.get('rule') or {}).get('basis_uncertain'))})
    return sorted(rows,key=lambda r:abs(r['gap_pp']),reverse=True)

def static_block(r):
    rows=[]
    for s in r['skills']:
        st=s['static_definition'];a,b=st['old'],st['new'];v=s.get('rule') or {}
        ct0=a.get('cast_time') or {};ct1=b.get('cast_time') or {}
        fields=[]
        for key,label in [('multiplier','基础倍率'),('cast_time','动作时间定义'),('element','伤害属性'),('level_data','等级面板'),('adjustment','附加规则')]:
            if a.get(key)!=b.get(key):fields.append(label+'变化')
        if not a:fields.append('旧技能库无同名条目（不等于旧评分完全没算）')
        if not b:fields.append('新技能库无同名条目')
        if v.get('full_factor',1)!=1:fields.append('V2层进一步替换基础倍率')
        if not fields:fields.append('两版导出静态定义相同；差异需看排轴、装备、状态和最终命中换算')
        rows.append([E(s['skill']),p(a.get('multiplier'),b.get('multiplier')),f(v.get('full_multiplier')),
                     p(ct0.get('cast_time'),ct1.get('cast_time')),E(a.get('element','—'))+' → '+E(b.get('element','—')),
                     '<br>'.join(E(z) for z in fields)])
    return table(['技能','基础表倍率 v4.78→v4.85','新V2完整倍率','静态动作秒数','属性','直接核到的程序变化'],rows)+para('动作秒数是基础定义，不含逐次动作加速/快放/状态选择。倍率跨物理/魔法通道时不能直接混加；“新V2完整倍率”是引擎记录，不是本次独立证明客户端完整路线。全部原始等级面板与附加规则保存在两份技能库 JSON 中。')

def integrity(full,capture,version):
    assert full['status']=='ok'
    assert full['version']==version and full['captureSha256']==sha(capture)
    assert full.get('networkDisabled') and full.get('freshReplayAndScore')
    assert full['score']['theoryPlanner']=='v401-full-team-score'
    assert not full['score'].get('theoryPlannerError')
    assert not full['score'].get('scoreSync',{}).get('enabled')
    assert all(str(ENGINES[version]).casefold() in path.casefold() for path in full['moduleOrigins'].values() if path)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--allow-partial',action='store_true');args=ap.parse_args()
    DEST.mkdir(exist_ok=True)
    candidates=json.loads((OUT/'candidate_inventory.json').read_text(encoding='utf-8'))
    catalogs={v:json.loads((OUT/f'skill_catalog_v{v}.json').read_text(encoding='utf-8'))['jobs'] for v in ('4.78','4.85')}
    jobs=candidates['jobs']
    newer=sum((json.loads((OUT/name).read_text(encoding='utf-8')) for name in ('first_cases.json','remaining_cases.json')),[])
    cases=[dict(id=k,file=n,root=str(PREVIOUS),reason='沿用上轮已校验的完整双版本评分结果') for k,n in OLD_CASES]+[dict(**c,root=str(OUT)) for c in newer]
    data={'scope':{'target_jobs':jobs,'cases':cases,'selection':'以现有本地有实际输出的副本抓包补齐职业；不按分数高低选样本。缓存仅用于候选发现，最终两版均独立解析、完整评分。'},
          'code_provenance':audit.code_provenance(),'cases':[],'players':[],'unpaired':[],'pending':[]}
    for c in cases:
        paths={v:Path(c['root'])/f"{c['id']}_v{v}.json" for v in ('4.78','4.85')}
        if not all(p.exists() for p in paths.values()):data['pending'].append(c['id']);continue
        pair={v:json.loads(path.read_text(encoding='utf-8')) for v,path in paths.items()}
        for v,full in pair.items():integrity(full,CAP/c['file'],v)
        old={r['source_id']:r for r in pair['4.78']['scoreRowsDetailed']};new={r['source_id']:r for r in pair['4.85']['scoreRowsDetailed']}
        battle={'case':c['id'],'name':c['file'],'versions':{}}
        for ver,full in pair.items():
            replay=full['replay'];battle['versions'][ver]={k:replay.get(k) for k in ('elapsed_seconds','total_damage','total_healing','instance_completion')}
            battle['versions'][ver].update(schema=full['schema'],score_seconds=full['scoreSeconds'],capture_sha256=full['captureSha256'])
        data['cases'].append(battle)
        for sid in sorted(old.keys()|new.keys()):
            if sid not in old or sid not in new:
                data['unpaired'].append(dict(case=c['id'],source_id=sid,old=old.get(sid),new=new.get(sid)));continue
            r=audit.analyze_player(c['id'],c['file'],pair['4.78'],pair['4.85'],old[sid],new[sid])
            for s in r['skills']:
                s['static_definition']={side:catalog_row(catalogs[ver],r['old_job'] if side=='old' else r['job'],s['skill']) for side,ver in [('old','4.78'),('new','4.85')]}
            enrich_evidence(r,old[sid],new[sid],pair['4.78'],pair['4.85'])
            data['players'].append(r)
        print(c['id'],'paired',len(old.keys()&new.keys()),flush=True)
        del pair
    players=data['players'];coverage={j:[r for r in players if r['job']==j and r['old_job']==j] for j in jobs}
    missing=[j for j,rs in coverage.items() if not rs]
    validation={'case_pairs':len(data['cases']),'player_pairs':len(players),'stable_jobs_covered':len(jobs)-len(missing),'missing_jobs':missing,'pending_cases':data['pending'],
                'skill_rows':sum(len(r['skills']) for r in players),'support_effect_rows':sum(len(r['supports']) for r in players),
                'max_total_error':max(abs(r['total_error']) for r in players),
                'max_output_ratio_error':max(abs(r['output_ratio_error']) for r in players),
                'max_support_ratio_error':max(abs(r['support_delta_error'] or 0) for r in players),
                'max_timeline_error':max(abs(v) for r in players for s in r['skills'] for v in s['timeline_reconciliation'].values()),
                'output_formula_unclosed':[(r['case'],r['name'],v) for r in players for v,z in r['versions'].items() if z['validated_burst_weight'] is None]}
    validation['cast_pairing_duplicate_rows']=sum(z['duplicate_skill_millisecond_rows'] for r in players for z in r['cast_pairing_validation'].values())
    validation['exported_reference_verified_casts']=sum(s['direct_v2_observation']['casts'] for r in players for s in r['skills'])
    validation['exported_reference_mismatch_casts']=sum(len(r['exported_reference_mismatches']) for r in players)
    data['validation']=validation
    unresolved=collections.defaultdict(lambda:{'samples':set(),'count':0,'notes':set()})
    for r in players:
        for s in r['skills']:
            rule=s.get('rule') or {}
            if rule.get('basis_uncertain'):
                u=unresolved[(r['job'],s['skill'])];u['samples'].add(r['capture']);u['count']+=1;u['notes'].add(rule.get('basis_note',''))
    data['unresolved_reference_groups']=[{'job':j,'skill':s,'samples':sorted(z['samples']),'sample_rows':z['count'],'notes':sorted(z['notes'])} for (j,s),z in unresolved.items()]
    data['count_hit_vs_damage_diagnostic']=suspicious_gaps(players)
    speed_groups=collections.defaultdict(lambda:{'examples':[],'samples':set()})
    for r in players:
        for z in r['speed_reference_gaps']:
            g=speed_groups[(r['job'],z['skill'])];g['samples'].add(r['capture']);g['examples'].append(dict(z,case=r['case'],actor=r['source_id'],name=r['name']))
    data['speed_reference_gap_groups']=[{'job':j,'skill':s,'samples':sorted(z['samples']),'examples':z['examples']} for (j,s),z in speed_groups.items()]
    assert validation['max_total_error']<1e-8 and validation['max_output_ratio_error']<1e-8
    assert validation['max_support_ratio_error']<1e-8 and validation['max_timeline_error']<.01
    assert not validation['output_formula_unclosed']
    assert not validation['cast_pairing_duplicate_rows'],'Must preserve simultaneous duplicate cast rows before reporting'
    if not args.allow_partial:assert not data['pending'] and not missing,(data['pending'],missing)
    (DEST/'attribution.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    out=[]
    out.append(para('范围：当前两个发布包技能库均支持的 '+str(len(jobs))+' 个二转职业；已完成 '+str(len(data['cases']))+' 份抓包的两版完整评分，共 '+str(len(players))+' 人次。以职业 ID/同一抓包的角色实体 ID 对照；同一职业多场全部保留，不只选涨分或降分样本。'))
    if data['pending'] or missing:out.append(para('进行中，不能作为全职业最终验收：待完成 '+E(data['pending'])+'；待覆盖 '+E(missing),'warn'))
    out.append(para('<strong>必须先分清两件事：</strong>计算差异可逐项对账，不代表所有游戏机制已经被证明正确。旧版与新版有技能改版、标准装备变化、理论排轴变化、Hit换算以及治疗/T规则变化。本报告逐条展示程序实际采用的数值；没有独立验证的客户端机制与逐状态反事实原因会明确保留。','warn'))
    out.append(para('新增样本用于补齐职业：0809黑典火舞、0816星期天硬核、0813硬核火舞、0830星期天、20260821YEP黑硬银弓、0808竞速最佳；另保留上轮六场。8月抓包发生在10月职业改版之前，所以“新版评分更低”不等于“当时操作不合格”。长抓包按文件中两版各自识别的完整有效区间评分，不将多轮片段猜成一次完美通关。'))
    out.append(para('计数单位是“抓包文件”和“文件内角色”，不是独立通关次数。例如0808竞速最佳包含多段Boss记录；没有把文件切短、只挑表现好的一段，也没有把它描述为一次单独战斗。'))
    out.append(para('使用的是实际发布包：v4.78 的评分规则20261013，与 v4.85 的评分规则20261017。默认装备、技能表、解析与V1/V2规则均保留该发布版自己的行为；这是一场“整版本对照”，不是只切换一个Hit开关的实验。'))
    out.append('<h2>怎么看这份报告</h2>'+para('先看下方职业表，点击职业进入该职业所有样本；再点击角色进入完整原因。每个角色包含：①总分加权对账，②整场输出与爆发项，③全部输出技能的次数/计价/Hit变化，④全部辅助效果，⑤治疗/T职责，⑥承伤扣分，⑦每一次技能的标准伤害对照。'))
    trend=[]
    for key,label in [('total_score','总分'),('output_score','输出分'),('support_score','辅助分')]:
        diffs=[r['versions']['4.85'][key]-r['versions']['4.78'][key] for r in players]
        trend.append([label,str(sum(x>.01 for x in diffs)),str(sum(x<-.01 for x in diffs)),
                      str(sum(abs(x)<=.01 for x in diffs)),delta(statistics.mean(diffs))])
    out.append(table(['所选角色配对样本','上升（>0.01分）','下降（>0.01分）','基本不变','平均变化'],trend)+
        para('同一个人在不同抓包里算不同人次；这里不代表全服分布。新旧版本复算的是同一份操作记录，所以分数变化描述的是评分基准/识别变化，不是玩家进步或退步。'))
    out.append(para('“平均单次计价”只是事后把总伤害除以次数，程序并未用一个全场统一系数替代逐次Buff/Debuff。所有百分点评价均注明是整场完成率、输出分还是最终总分，三者不能混加。'))
    out.append(detail('先分清这些数字，后面的表就容易读',table(['名称','这份报告中的含义'],[
        ['释放次数 / Hit数','一次按键或完整动作可以有多Hit；跨版本还要检查释放记录的单位是否改变。'],
        ['倍率实现率','一次释放相对于完整路线打出了多少伤害倍率；是Hit模型提供的参考，不等于整场伤害完成率。'],
        ['标准伤害','用该发布版的评分装备、技能定义和逐时刻状态重新计价的伤害，不是原包伤害，也不是游戏角色面板的直接复刻。'],
        ['伤害完成率','实战标准伤害之和 ÷ 理论标准伤害之和；次数、每次Hit、Buff/Debuff及Boss参考都会影响它。'],
        ['输出分 / 总分','输出分还包含爆发窗口项；总分继续结合辅助、特色、各项权重及承伤扣分。'],
        ['百分点（pp）','例如完成率80%降为70%，是下降10个百分点，不是下降10%的相对比例。技能分摊针对整场完成率，不能直接当作最终总分扣分。']
    ])))
    out.append(detail('先用一个小例子理解：为什么次数完成率不能直接当伤害完成率',
        para('假设理论和实战都是10次，Hit也全部打满：理论有4次覆盖强化，每次200，其余6次每次100，总计1400；实战只覆盖3次强化，总计1300。次数完成率仍是100%，伤害完成率是1300/1400=92.86%。这是逐次计价后求和，不是额外乘了一个全场惩罚系数。')+
        para('同样，如果只把实战和理论伤害都统一乘1.3，完成率不会改变。标准装备更新本身不能解释所有降分；还要核对技能/物魔通道、固定加成、覆盖时刻和理论安排是否让两边以不同比例变化。报告的“平均单次价值”是这些逐次结果的事后汇总，不是评分额外套用的整场系数。')+
        para('比较两个版本时还多了一层：技能的完整倍率、标准装备、理论排轴、个人有效区间也可能一起变化。因此本报告先核每次/每项的值，再把差额加总；不能把所有下降都归到Hit，也不能把理论次数减少直接当作排轴修好了。'),True))
    overview=[]
    for j,rs in coverage.items():
        means={v:{k:statistics.mean(r['versions'][v][k] for r in rs) if rs else None for k in ('total_score','output_score','support_score')} for v in ('4.78','4.85')}
        overview.append(['<a href="#job_'+E(j)+'">'+E(j)+'</a>',str(len(rs)),p(means['4.78']['total_score'],means['4.85']['total_score']),p(means['4.78']['output_score'],means['4.85']['output_score']),p(means['4.78']['support_score'],means['4.85']['support_score'])])
    out.append('<h2>全职业样本汇总</h2>'+table(['职业','同职业配对人次','样本平均总分','样本平均输出分','样本平均辅助分'],overview)+para('这只是所选样本的均值，不是数据库全体职业排名，也不能据一个样本断言整个职业被削弱。输出分大降但总分变化小，可能是职责或权重抵消，详见个人账。'))
    role_changes=[r for r in players if r['versions']['4.78']['role']!=r['versions']['4.85']['role']]
    out.append(detail('职责切换：先看是否换了评分构成，而不只是技能伤害',table(
        ['角色','旧职责 → 新职责','个人有效秒数','总分','旧判定依据','新判定依据'],
        [[view.link(r),E(r['versions']['4.78']['role'])+' → '+E(r['versions']['4.85']['role']),
          p(r['versions']['4.78']['output_seconds'],r['versions']['4.85']['output_seconds']),
          p(r['versions']['4.78']['total_score'],r['versions']['4.85']['total_score']),
          E(r['versions']['4.78']['role_reason']),E(r['versions']['4.85']['role_reason'])] for r in role_changes])+
        para('角色自己的有效区间变化，会改变覆盖率的分母，并可能跨过奶/输出的职责阈值。不能只看某一项分数就解释最终总分；具体区间与权重在角色页保留。')))
    for j,rs in coverage.items():
        out.append('<h2 id="job_'+E(j)+'">'+E(j)+'</h2>')
        if rs:
            changing=max(rs,key=lambda r:abs(r['versions']['4.85']['total_score']-r['versions']['4.78']['total_score']))
            out.append(para('本职业样本中总分变化最大的是 '+view.link(changing)+'（'+E(changing['capture'])+'）。以下逐场保留，不用职业均值掩盖不同队伍的变化。'))
        rows=[]
        for r in rs:
            a,b=r['versions']['4.78'],r['versions']['4.85']
            top='；'.join(E(s['skill'])+' '+delta(view.impact(s))+'pp' for s in r['skills'][:3])
            rows.append([E(r['capture']),view.link(r),p(a['total_score'],b['total_score']),p(a['output_score'],b['output_score']),p(a['support_score'],b['support_score']),p(a['specialty_score'],b['specialty_score']),top])
        out.append(table(['抓包','角色（进入详情）','总分','输出分','辅助分','特色分','影响整场完成率最大的技能'],rows))
        if j=='狂战士':out.append(para('重点：旧抓包发生在新版怒气上限、6怒气真旋转冲击、滚动攻击取消怒气增幅之前。不能把两版的技能次数和单次价值变化全部判为实战失误。','warn'))
        if j=='风行者':out.append(para('同一职业甚至同一抓包也会一升一降：0911的TL丶嘟嘟输出分67.79→72.38，双飞腿实战72→71、理论155→92，单这一项分摊+7.70个整场完成率百分点；同场米拉酱的双飞腿实战35→35、理论73→93，方向相反。不是统一给风行者加分，而是各自排轴、区间和计价一起变化。','note'))
        if j=='影':out.append(para('0808样本输出分87.12→87.70，但不能简单说倍率模型更准确。致命狂镰平均Hit实现率94.92%、平均完整倍率率129.18%；46.672秒这次是2/2 Hit，实际权重2.4、参考权重1.4，得到171.43%。引擎导出参考1.389M被换算成2.381M，已正向核验。全技能最后换算净增加274.35M。需核实际权重与完整路线是否混用了不同动作/通道，不能把超100%自动解释成多打Hit。','warn'))
        if j=='狙翎':out.append(para('0808样本输出分84.49→77.75。包围计分实战68→67、理论82→84，平均完整倍率率80.13%，分摊整场完成率−3.11个百分点；飞空连射再分摊−2.36。箭雨风暴理论67→47反而有+1.32个百分点的抵消。需要同时看下降项和抵消项，不能只挑一个技能解释整个职业。','note'))
        if j=='烈':out.append(para('已核实一个特别重要的计数差异：0809样本的致残惩罚者攻击，旧版在50.171、50.319、50.480、50.669秒分别生成攻击记录；新版在49.898秒识别动作117的一整轮攻击。发布包的新版解码明确按动作生成释放，伤害归因不再额外制造释放。该技能210→70不是同一单位下“少放140次”，不能直接用次数相除评价操作。它同时从旧理论0次变为新理论12次，完整账见该角色条目。','warn'))
        if j=='黑暗复仇者':out.append(para('0809样本中新版评分加入“觉醒•黑暗之刃”：实战14次、理论17次；旧版该角色评分明细没有这一项。加入后会同时增加实际伤害和理论伤害，并不是只补实战分子就一定涨分。','note'))
        if j in ('重炮手','机械大师'):out.append(para('重磅鸭还有独立的排轴修正：旧版会因鸭数量已满而阻止继续投蛋；新版允许投蛋并替换旧鸭，也不再按召唤存续时长摊薄投蛋伤害。这已核到发布包的函数差异。0821重炮样本实战均57次，理论由36变90；该变化不能简单归因于玩家变差。塔的持续攻击则仍须单独核对，不能把投蛋规则套到塔上。','note'))
        if j in ('十字军','圣骑士'):out.append(para('重点：神圣之锤攻击的新理论条目、物魔通道面板换算、审判力量状态与逐次计价必须分开。Buff标签缺失不能单独证明玩家没有施放。','warn'))
        if j in ('曜','暗'):out.append(para('重点：圆环的完整倍率、空中循环时间及动作速度存在条件性；仍需区分标准参考路线与特定地形实际Hit。报告不把未知飞行/高度条件当固定事实。','note'))
        if j=='暗':out.append(para('0808样本圆环强击实战两版均57次，理论63→55，但仍分摊−4.69个整场完成率百分点。新版54次估计关联的完整倍率率均值54.41%，最后换算参考926.76M→507.63M；这明确解释了程序怎样降分，但18Hit参考与实际地形、滞空条件仍需分开，不能据此断言玩家每次只打了一半。','warn'))
    changes=[]
    for j in jobs:
        for s,b in catalogs['4.85'][j].items():
            a=catalogs['4.78'][j].get(s,{})
            if a!=b:
                fields=[label for k,label in [('multiplier','倍率'),('element','属性'),('cast_time','动作'),('level_data','等级数据'),('adjustment','附加规则')] if a.get(k)!=b.get(k)]
                changes.append([E(j),E(s),p(a.get('multiplier'),b.get('multiplier')),E('、'.join(fields))])
    out.append('<h2>两个正式发布包的静态技能定义差异</h2>'+table(['职业','技能','基础倍率','改变的字段'],changes))
    out.append(para('这张表只列基础定义变化，不包含 V2 运行时参考替换。例如两版连锁闪电基础表都为5，但新评分运行时可使用8；每人详情中的“新V2完整倍率”另列。未出现在表里的技能仍可能因命中换算、装备、状态和排轴发生评分变化。'))
    entry_rows=[];entry_counts=collections.Counter()
    for r in players:
        for s in r['skills']:
            a,b=s['old'],s['new'];kind=None
            if a.get('actual_count',0)==0 and a.get('theory_count',0)>0 and not b:kind='本场零释放：新版不再列入'
            elif a.get('excluded_from_score') and b and not b.get('excluded_from_score'):kind='旧低伤害豁免：新版重新计分'
            if kind:
                entry_counts[kind]+=1
                entry_rows.append([view.link(r),E(s['skill']),kind,p(a.get('actual_count',0),b.get('actual_count',0)),
                    p(a.get('theory_count',0),b.get('theory_count',0)),delta(view.impact(s))])
    out.append(detail('两个方向相反的规则：未使用技能删除，与旧豁免技能重新纳入',
        para('这两件事不能混为一谈。“完全没使用”在新版可能退出理论轴，减少分母；“使用过但伤害很低”在旧版可能被豁免，新版却要参与实际/理论比较。所选样本对应条目数：'+E(dict(entry_counts))+'。以下次数为明细原始展示值，分摊按真正参与计分的数值计算。')+
        table(['角色','技能','纳入规则变化','实际次数旧→新','理论次数旧→新','分摊整场完成率差(pp)'],entry_rows)))
    speedrows=[]
    for g in data['speed_reference_gap_groups']:
        z=g['examples'][0];r=next(r for r in players if r['case']==z['case'] and r['source_id']==z['actor'])
        speedrows.append([E(g['job']),E(g['skill']),str(len(g['examples']))+' 次 / '+str(len(g['samples']))+' 场',view.link(r)+' '+f(z['seconds'],3)+'s',
            f(z['speed_percent'])+'%',f(z['matched_hits'],0)+' / '+f(z['expected_hits'],0),f(z['nominal_speed_factor'],3)+' / '+f(z['hit_reference_speed_factor'],3),
            str(z['theory_accelerated_casts'])+'次；'+E(z['theory_nominal_factors']),('—' if z['before_inverse'] is None else m(z['before_inverse']))+' → '+m(z['after'])])
    out.append('<h2>新增共性疑点：满Hit参考与名义加速增伤不一致</h2>'+para('以下是程序内部可直接核到的参考差异：某次完整倍率实现率约100%，但Hit参考的加速增长系数明显小于计价层名义加速系数。V2换算会撤去名义加速，理论侧仍需核对是否使用相同的完整参考。因此，“100%实现率”并不必然保留换算前的标准伤害。','warn')+
        table(['职业','技能','符合条件的次数/场数','第一个实例','动作速度','命中/期望Hit','名义增伤系数 / Hit参考增长系数','同一人理论轴的加速次数/系数','换算前（反推）→后'],speedrows)+
        para('筛选条件：非召唤物独立周期、完整倍率率99.9%～105%、名义加速/Hit参考增长>1.10。它定位的是两层参考不一致，不自动证明客户端究竟该增加多少Hit；固定起收手、循环取整、条件路线也需排除。以混沌阵为例，75.382秒的11/11 Hit在40%加速下，实际比例=1，名义系数=1.4，16.57百万参考换算为11.84百万。不能把这一层差额再说成“漏了40%的Hit”。完整实例保存于分析JSON。'))
    gaps=[]
    for z in data['count_hit_vs_damage_diagnostic']:
        r=next(r for r in players if r['case']==z['case'] and r['source_id']==z['actor'])
        gaps.append([E(r['capture']),view.link(r),E(z['skill']),pct(z['rough_count_times_mean_hit_percent']),pct(z['actual_damage_completion_percent']),delta(z['gap_pp']),'是' if z['basis_uncertain'] else '未被引擎标为近似（不等于独立验证过）'])
    out.append(detail('重点复核清单：次数比×平均倍率与伤害完成率相差至少15个百分点',para('这不是自动判错表。左列使用已匹配释放的简单平均倍率，而计分是逐次按状态、Boss及价值加权；覆盖缺失、状态差异、召唤物计数口径均可能使两者不同。这里只把需要优先解释的大差距全部列出，不把它们悄悄归成“玩家没打好”。')+table(['抓包','角色','技能','次数比×平均倍率（诊断近似）','新伤害完成率（未封顶）','两者差','完整参考仍近似？'],gaps)))
    zero_theory=[]
    for r in players:
        for s in r['supports']:
            a,b=s['old'],s['new']
            if b.get('included_in_score') and b.get('actual_contribution',0)>.1 and b.get('theory_contribution',0)==0:
                zero_theory.append([view.link(r),E(s['skill'])+' / '+E(s['effect']),
                    p(a.get('actual_contribution'),b.get('actual_contribution')),
                    p(a.get('theory_contribution'),b.get('theory_contribution')),
                    p(100*(a.get('actual_coverage') or 0),100*(b.get('actual_coverage') or 0),pct),
                    p(100*(a.get('theory_coverage') or 0),100*(b.get('theory_coverage') or 0),pct)])
    out.append(detail('辅助涨分重点检查：实际有收益，但对应理论收益为零',
        para('下列行在新版被纳入辅助计分，实际贡献超过0.1，但对应理论贡献为0。它们仍可增加总实际收益，不能用单项0分母计算一个可靠的百分比。可能涉及条件性Debuff、辅助动作没有排入理论轴或效果模型缺项，不能直接认定玩家超常发挥；角色页保留全部明细。')+
        table(['角色','辅助技能/效果','实际收益旧→新','理论收益旧→新','实际时间覆盖旧→新','理论时间覆盖旧→新'],zero_theory)+
        para('例如0821曜：总实际辅助收益20.134→2.935，理论30.599→2.375，辅助分却65.80→123.54。查克拉治愈术的新版实际双攻收益0.889仍计入，但对应理论为0；并且职责由奶/辅助转为输出。因此这次上涨主要不能解释成Buff覆盖更好。')))
    for case in data['cases']:
        out.append('<h2>'+E(case['name'])+'：全队逐角色原因</h2>')
        a,b=case['versions']['4.78'],case['versions']['4.85']
        out.append(para('回放总伤害 '+p(a['total_damage'],b['total_damage'],m)+'；回放恢复量 '+p(a['total_healing'],b['total_healing'],m)+'；评分耗时 '+p(a['score_seconds'],b['score_seconds'])+' 秒。耗时来自受控并发实验，不是独占机器性能基准。'))
        ca,cb=a.get('instance_completion') or {},b.get('instance_completion') or {}
        out.append(para('文件录制时长 '+p(a['elapsed_seconds'],b['elapsed_seconds'])+' 秒；通关识别 '+E(ca.get('label',ca.get('status','未知')))+' → '+E(cb.get('label',cb.get('status','未知')))+'。识别依据：'+E(ca.get('reason','未知'))+' → '+E(cb.get('reason','未知'))+'。未确认通关不等于能断定玩家没过本；本表只保留解析器的证据状态。'))
        for r in players:
            if r['case']!=case['case']:continue
            if r['old_job']!=r['job']:out.append(para('职业识别发生改变：'+E(r['old_job'])+' → '+E(r['job'])+'；该行保留分析，但不混入同职业统计。','warn'))
            page=para('<a href="'+REPORT_NAME+'#job_'+E(r['job'])+'">← 返回本职业目录</a> · '+E(r['capture']))
            page+=detail('先看白话逐技能原因：次数、区间、定义与命中换算',plain_skill_reasons(r),True)
            page+=view.player_block(r)
            page+=detail('此角色所有技能：发布包基础定义与 V2 参考逐项核对',static_block(r))
            (DEST/person_file(r)).write_text(view.shell(r['name']+' · '+r['job']+'：两版评分逐技能原因',page),encoding='utf-8')
        out.append(table(['角色：点击进入全部逐技能分析','总分','输出分','辅助分','特色分'],[
            [view.link(r),*[p(r['versions']['4.78'][k],r['versions']['4.85'][k]) for k in ('total_score','output_score','support_score','specialty_score')]]
            for r in players if r['case']==case['case']]))
    out.append('<h2>尚未独立证明的部分与验收范围</h2>'+para('逐项加总核验仅证明分数来自这些计算项。具体Buff/Debuff时刻是否符合游戏、所有碰撞体完整倍率、零命中的换算前价值、召唤物近似模型等，不能只靠结果反推为正确。程序记录“待闭合”的技能仍在个人详情中黄色标注；本次没有通过改标签减少未核实项。'))
    out.append('<h3>根据这次对照，优先应修什么</h3>'+table(['优先级','问题','建议修法与验收'],[
        ['1','加速后的理论完整伤害与Hit参考增长不一致','让理论排轴与逐Hit对照共享同一完整路线/循环计数函数；固定装备、Buff和Boss，100%完整路线的实际标准量应与同条件理论一致。先处理上面的明确实例，不先调一个全局分数系数。'],
        ['2','相同Hit数却采用不同倍率权重','以影的致命狂镰2/2 Hit、2.4/1.4权重为例，核对实际路线、完整参考和物魔通道是否一致；不要把倍率超100%一概当作超额命中。'],
        ['3','动作次数和伤害段数混用','持续攻击/变身普攻同时保留“动作次数”和“原生Hit次数”，跨版本对照先说明单位。用致残惩罚者与神圣之锤的动作证据作回归样本。'],
        ['4','Buff缺失被过早解释为玩家漏放','审判力量等同时核释放包、获得/移除状态和同Boss逐Hit伤害；标签缺失只能算证据缺口，不能自动认定玩家没开Buff。'],
        ['5','仍近似的完整倍率和召唤物周期','逐项处理下表的近似模型，统一面板单位、出生快照、后续增益和实际存续；不要只修改“已校准”标签。'],
        ['6','旧抓包套新职业机制后被当作操作退步','保留录制日期、职业改版与评分基准版本。A/B比较用于定位算法变化，不能拿8月旧技能机制要求当时满足10月的新机制。']]))
    out.append(para('以上是下一步修正建议。本轮按你的要求做双版本复算与原因分析，没有修改正式评分算法、安装包或云端数据。'))
    out.append(table(['职业','技能','涉及样本数','发布算法明确保留的近似/未闭合项'],[[E(z['job']),E(z['skill']),str(len(z['samples'])),'<br>'.join(E(n) for n in z['notes'])] for z in data['unresolved_reference_groups']]))
    out.append(para('逐技能分摊采用精确的对称账目拆解，不是假装为每一个因素都重跑过全队轴。各技能分摊之和等于整场伤害完成率差；辅助效果分摊之和等于辅助分差；实际加权贡献之和等于总分差。爆发窗口作为独立项核对，避免重复扣算。'))
    out.append(para('<a href="attribution.json">全部分析数据与校验</a> · <a href="../candidate_inventory.json">样本候选与职业覆盖证据</a> · <a href="../skill_catalog_v4.78.json">v4.78技能定义</a> · <a href="../skill_catalog_v4.85.json">v4.85技能定义</a>'))
    out.append('<pre>'+E(json.dumps(validation,ensure_ascii=False,indent=2))+'</pre>')
    target=DEST/REPORT_NAME
    target.write_text(view.shell('全职业典型抓包：v4.78 → v4.85 评分变化与逐技能原因',''.join(out)),encoding='utf-8')
    print(json.dumps(validation,ensure_ascii=False,indent=2));print(target)
if __name__=='__main__':main()
