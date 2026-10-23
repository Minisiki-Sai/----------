"""Read-only follow-up audit; writes reports only, never scores/cloud records."""
from __future__ import annotations
import ast
import base64
import hashlib
import json
import math
import statistics
import zlib
from collections import Counter, defaultdict
from html import escape as esc
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / 'outputs/verification'
OUT = V / 'priority_price_deep_20261019'
OUT.mkdir(exist_ok=True)
AB = V / 'all_jobs_published_ab_20261019/detailed_attribution'
CLOUD = V / 'all_jobs_completion_20261019'

def read(p):
    return json.loads(p.read_text(encoding='utf8'))

def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(str(h))+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'

def m(v):
    return '—' if v is None else f'{v/1e6:,.3f}'

def pct(v):
    return '—' if v is None else f'{v:.2f}%'

def ratio(a,b):
    return a/b if b else None

def describe_casts(rows):
    powers=[x['power'] for x in rows]
    return dict(rows=len(rows), positive=sum(x>0 for x in powers), total=math.fsum(powers),
                median=statistics.median(powers) if powers else 0,
                accelerated=sum(x.get('action_speed_percent',0)>0 for x in rows))

ab=read(AB/'attribution.json')
pop=read(CLOUD/'analysis.json')
prior=read(V/'skill_price_risks_20261019/analysis.json')
reps=read(CLOUD/'representative_timelines.json')
focus={str(x['id']):x['skill'] for x in prior['pending']}
cloud_cases=[]
for rec in reps:
    sid=str(rec['id'])
    if sid not in focus: continue
    skill=focus[sid]
    c=json.loads(zlib.decompress(base64.b64decode(rec['timeline']['p'])))
    item=dict(id=sid,job=rec['job'],nest=rec['nest'],skill=skill,
              metric=next(x for x in pop['rows'] if str(x['id'])==sid and x['skill']==skill))
    for lane in ('actual_timeline','theory_timeline'):
        rows=[]
        for original in c['detail'][lane]:
            if original['skill_name']!=skill: continue
            row=dict(original)
            # These are OBSERVED state intervals, not a theory replay snapshot.
            row['observed_states_at_same_clock']=sorted({b.get('label','') for b in c['snapshot']['player_buff_series']
                if any(s<=row['seconds']<e for s,e in b.get('intervals',[]))
                and any(k in b.get('label','')+b.get('skill_name','') for k in ('九天','幻影','精神爆发','时间加速','勇敢','神秘','火焰之力'))})
            row['possible_ice_targets_at_same_clock']=sorted({b['target_id'] for b in c['snapshot'].get('boss_effects',[])
                if any(s<=row['seconds']<e for s,e in b.get('intervals',[]))})
            rows.append(row)
        item[lane]=rows
        item[lane+'_summary']=describe_casts(rows)
    # Reconciliation: timeline sum must equal uploaded scoring skill total.
    skillrow=next(x for x in rec['skills'] if x.get('skill_name',x.get('skill'))==skill)
    item['uploaded_skill']=skillrow
    for lane,field in [('actual_timeline','actual_power'),('theory_timeline','theory_power')]:
        if field in skillrow:
            assert math.isclose(item[lane+'_summary']['total'],skillrow[field],abs_tol=.1,rel_tol=1e-8),(sid,lane)
    cloud_cases.append(item)

skills={'九尾激光','偷袭点','致命劈砍','热情似火','火焰风暴','极度打击','旋风','幻象攻击'}
locals=[]
for player in ab['players']:
    matched=[s for s in player['skills'] if s['skill'] in skills and s.get('new',{}).get('actual_count',0)>0]
    if not matched: continue
    ledger=read(AB/player['cast_ledger'])
    for skill in matched:
        rows=[x for x in ledger if x['skill']==skill['skill'] and x.get('new')]
        groups=defaultdict(list)
        for row in rows: groups[(row.get('final_v2_step') or {}).get('method','unclassified')].append(row)
        summary={k:dict(count=len(g),power=math.fsum(x['new']['power'] for x in g)) for k,g in groups.items()}
        total=math.fsum(x['new']['power'] for x in rows)
        assert math.isclose(total,skill['new']['actual_power'],abs_tol=.1,rel_tol=1e-8)
        locals.append(dict(case=player['case'],capture=player['capture'],job=player['job'],actor=player['source_id'],
            skill=skill['skill'],old=skill['old'],new=skill['new'],rule=skill.get('rule'),
            ledger_file=str(AB/player['cast_ledger']),rows=rows,groups=summary,
            v2_final_step=skill.get('v2_final_step'),static_definition=skill.get('static_definition')))

# Reinspect raw per-cast exported evidence, not just the rendered summary.
raw=[]
evidence_root=V/'all_jobs_published_ab_20261019/state/speed_0808_v4.85/USERPROFILE/Documents/DragonNestDpsCapture/score_hit_evidence/evidence'
seen=set()
for p in sorted(evidence_root.glob('*.json')):
    for row in read(p)['cast_rows']:
        if (row['job'],row['skill_name']) not in {('狙翎','偷袭点'),('暗','幻象攻击')}: continue
        key=(row['source_id'],row['skill_name'],row['cast_timestamp_ms'],tuple(row.get('target_ids',[])))
        if key in seen: continue
        seen.add(key)
        raw.append(dict(file=str(p),**{k:v for k,v in row.items() if k not in {'burst_parts','action_speed_segments'}}))

# Isolated code-path probe: current V2 common multiplier + current route finalizer.
# This is NOT a battle re-score; stable panel, no Buff/debuff changes.
from dragon_nest_builder.live_dps.score_v2 import override_multiplier, make_context, value_actual
from dragon_nest_builder.live_dps.scoring import _ValuedCast, SkillProfile
tree=ast.parse((ROOT/'dragon_nest_builder/mixins/skill_timeline_output.py').read_text(encoding='utf8'))
node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_skill_burst_apply_ripper_successive_scores')
env={'norm':lambda x:str(x or '').strip(),'as_number':lambda x,d=0:float(x) if x is not None else d}
exec(compile(ast.Module(body=[node],type_ignores=[]),'<diagnostic:current route method>','exec'),env)
owner=NS(second_job=NS(currentText=lambda:'烈'),_skill_burst_skill_key=lambda x:x,
         build={'_score_v2_profiles':{'极度打击':dict(full_multiplier=1.3,boss_factor=1.,reference_factor=1.,normalized_body_ceiling=True)}})
base=override_multiplier(owner,'极度打击','烈')
segments=[dict(start=0.,end=1.,skill='极度打击',effective_score=100*base),dict(start=1.,end=2.,skill='极度打击',effective_score=100*base)]
env[node.name](owner,segments)
probe=dict(common_v2_multiplier=base,unit_panel=100,segments=segments,expected_route_prices=[130,110],
           observed_route_prices=[x['effective_score'] for x in segments],
           note='隔离组合测试，非全场重算；展示V2覆盖倍率与仍假定1.1基准的后处理叠乘。')
assert math.isclose(probe['observed_route_prices'][0],153.63636363636,rel_tol=1e-10)
assert probe['observed_route_prices'][1]==130
repo=NS(variants_for=lambda *args:[NS(form='隔离测试首发',hits=[NS(weight=.2,optional=False),NS(weight=1.1,optional=False)])])
store=NS(skill_damage_multiplier=lambda *args:1.1)
cast_evidence=[dict(source_id='probe',job='烈',skill_id='6208',skill_name='极度打击',cast_timestamp_ms=stamp,
    actual_hits=hits,matched_hits=hits,expected_hits=hits,expected_weight=weight,realized_weight=weight,
    complete=True,eligible=True,v2_tier='A',multiplier_rate_percent=100.,hit_rate_percent=100.)
    for stamp,hits,weight in [(0,2,1.3),(1000,1,1.1)]]
rule=make_context({'cast_rows':cast_evidence},repo,store)['actors']['probe']['极度打击']
valued=[_ValuedCast(seconds=i,key='极度打击',source='隔离固定面板',power=130.,expected_duration=1.,utilized_duration=1.,utilization_ratio=1.,proportional_output=False)for i in (0,1)]
actual_probe=value_actual(valued,{'极度打击':SkillProfile('极度打击','极度打击',130.,1.,1.)},{'_score_v2':{'profiles':{'极度打击':rule}}},'烈')
probe['actual_path_prices']=[x.power for x in actual_probe]
probe['actual_points']=rule['casts']
assert probe['actual_path_prices']==[130.,130.]

findings={
'偷袭点':('P0 · 已确认计价分流与匹配缺口',
 '本地 0808 竞速样本：54条新实战时间轴，15条绑定后判0，39条没有对应倍率点而回退；118.458M正伤害全部来自后者。更深一层，重新读取原逐次证据发现26条记录明明 actual_hits=1，却 matched_hits=0、extra_hits=1、realized_weight=0。不是没有收到伤害记录，而是收到的Hit没有落进原生槽位。',
 '不能把这些0%解释成玩家真的没打中，也不能把回退计价当作已证明打满。应核对偷袭点的出生、引爆/延迟命中、主体与衍生对象窗口，再把实际已命中的记录从“槽外额外Hit”还原到对应释放。云端#33824与本地不是同一场；云端6条正伤害是否都属于同一分支，仍缺该场逐Hit账本。'),
'致命劈砍':('P0 · 两次极端高价，回退/体型校准优先',
 '上一份只突出46.658s；这次逐次核对确认还包括215.273s，分别21.964M、21.679M。其余10次中位数约1–2M。云端恰好只有10次可靠倍率、12次评分释放；Boss实现率70.37%，完整路线实现率却只有10.58%。这提示必须查“无证据两次是否撤掉体型折减、回到完整参考”的分支，而非直接说Buff很强。',
 '两次高价均为0%动作加速，而且一次在爆发窗、一次不在；第一发与54.081s的低价发都记录勇敢/时间加速/神秘状态。已能排除“只有那一次在加速窗”这种简单解释，不能据此排除状态数值、命中或目标差异。没有该云端场次原生权重/绑定结果，尚不能把两次高价确认为回退，也不能删除其伤害。'),
'极度打击':('P0 · 新确认倍率基准叠乘风险，可复现',
 'V2把技能公共倍率覆盖成1.3；路线后处理仍以“公共倍率1.1”为前提，首发再乘1.3/1.1。隔离组合测试中，面板100时应为首发130、连放110，实际组合得到153.636、130，两条都高18.18%。冻结v4.85理论轴也导出了首发ripper_successive_factor=1.181818和连续factor=1的字段。',
 '进一步直接调用make_context/value_actual，首发1.3/1.3和连续1.1/1.1都为100%，实际路径分别得到130、130；理论组合却为153.636、130。因此即使首发100%命中，同条件实际/理论也只有84.62%，这确实能制造类似十字技能的比例偏差。源码实战普通伤害先拷贝到ordinary_rows，后面的周期重算不回写这些普通价格；理论则统一执行路线后处理。先统一倍率基准和路线字段，再核对实战连续输入容差。尚未做完整重排，不能报修复后的总分。'),
'幻象攻击':('P0/P1 · 参考未闭合，并有回退高价',
 '本地0808样本：114条时间轴中106条绑定、8条回退。回退仅占7.0%条目，却贡献15.097M、约15.0%的该技能评分伤害；最高价727.279s的3.443M正是回退。已绑定记录平均完整倍率32.61%，常见6–9Hit，而完整模型写25Hit/20.8权重。',
 '原证据明确写“基准中有25个Hit的出伤时间未确认”，因此不能先把约三成倍率全部解释为玩家漏打。需要核实EX衍生来源、时间标尺和主体/对象权重。云端#34310显示6.97%仍计79.22%伤害，集合问题比本地更严重，但不能将本地8条回退数直接套给云端。'),
'九尾激光':('P1 · 云端集合失配，本地非普遍失效',
 '云端#33434评分49次、倍率可靠15次，不能把15次平均5.98%乘到49次。对照本地0916，94次中88次有绑定，6次回退29.570M；绑定占93.6%，说明不是九尾在所有抓包都无法匹配。',
 '本地120.550s和121.603s各27Hit、权重1.49997，均100%、同价5.228M，至少这些连续释放并未出现“次数多就自动丢第一段”的问题。仍需针对试炼的目标范围、充能连续释放归属查那34次集合差，不可用全局倍率修补或据此宣布试炼记录正常。'),
'热情似火':('P1 · 理论时机/状态有实证差异，未证实同类快照Bug',
 '云端7次实战、7次可靠倍率，排除了此例“大量未知回退”这一主因。实战前四发55.617/339.613/443.106/860.637s均处于已上传九天之舞2阶区间；理论前四发71.517/190.350/305.177/454.865s在同一实测时钟上均不覆盖该状态。实战5/7次有动作加速，理论10/10次为0%。',
 '这提供排轴窗口不一致的证据，不等于理论自己没有另排九天，也不等于加速会直接按百分比提高本技能伤害。云端没有理论侧每次的完整状态数值快照；不能把全部+42.31个百分点都归给九天。应在相同装备/状态下回放同一组释放时刻，再对比真正的最优轴。'),
'火焰风暴':('P1 · 高峰时机及原生上限共同影响',
 '云端实战5/6次在加速/爆发窗口，理论仅3/7次；理论前三发均无加速，分别10.847、13.452、15.011M，第四发468.635s进入40%窗口后达到40.919M，并不一直偏低。云端已上传可靠倍率本身113.67%，不能假设都是100%。',
 '本地0813五发全部有绑定，不是未知回退导致：4次40%加速期望权重1.32/15Hit，另一次零加速参考1/11Hit，但都有超过完整参考的倍率。需检查跨窗口加速、接触/额外Hit及循环边界；不能把>100%自动清零/截断，也不能只因实际高于理论就判定玩家数据异常。'),
'旋风':('P1 · 已确认参考受观测额外Hit影响',
 '旧实验逐次证据显示，部分兼容的额外接触Hit被追加为非可选期望Hit；由此得到的expected/native不再只是动作加速因子。例如1011毁灭者52.453s，40%加速原生参考1.228，而观测侧参考1.266。',
 '这是参考构造口径问题，不能用统一1.4或简单替换成某个固定倍率解决。还存在0%加速参考>1的记录。应先分开固定原生上限、加速循环数与额外接触，再核对时长边界。此前实验已排除旋风，未擅自启用修正。'),
'火球':('P1 · 分阶段/三向路线与额外伤害待对齐','云端25次实战、36次理论，首发实际0.77M而理论5.70M；完整路线换算与单次蓄力形态不能用一个比例概括。','本次保留全部逐次时间、加速、区间及价格；云端没有每次原生投射物与灼烧之手拆账，暂不能确认具体哪次错价。'),
'旋转冲击':('P1 · 形态样本不足','评分27次而可靠倍率只有1次，无法由这1次代表全部普通/6怒气形态。','必须先在每次释放上标普通或真旋转、对应技能版本和怒气，再用各自倍率与动作时长比较。本次不能把大差距判为已确认漏打。'),
'冲击之手':('P2 · 需圣物数量与逐Hit合并核查','一次释放价格随在场圣物数改变；云端10次实际37.60M对20次理论202.73M。当前代码对有绑定证据的实战固定3圣物参考后乘倍率，理论仍按自己的圣物状态。','需核对原生Hit参考是否也准确表达3圣物，而不是重复乘数量。现在仅凭伤害汇总不能断定理论202.73M成立；不再把“圣物多”当作已证明正确的结论。'),
'灵魂之门':('P2 · 周期/烙印仍待逐Hit闭合','同场单次实际4.90–35.37M、理论6.56–37.24M，不能仅由10/13次解释。','已列全周期价格与状态；必须查烙印、小/大幽灵和大幽灵双Hit、生命周期边界。现有云端数据不足以分配这些因素的定量贡献。'),
'重磅鸭':('P2 · 补召次数不是完整周期数','9条实际时间轴仅5条正伤害，补召与输出周期不同。','需要将每只鸭的出生、存续、攻击、自爆对应到周期，不能把每次补召当成一次打满的理论伤害。本次不把零价补召直接当算法漏伤害。'),
}

payload=dict(scope=dict(local_capture_count=len(ab['scope']['cases']),local_actor_records=len(ab['players']),
                        local_focus_skill_records=len(locals),local_focus_cast_rows=sum(len(x['rows'])for x in locals),
                        cloud_focus_records=len(cloud_cases),cloud_focus_cast_rows=sum(len(x[k])for x in cloud_cases for k in ('actual_timeline','theory_timeline')),
                        raw_evidence_rows=len(raw),fresh_cloud_query=False,full_battle_rescore=False),
             cloud_cases=cloud_cases,local_cases=locals,raw_evidence=raw,isolated_route_probe=probe,
             findings={k:dict(priority=v[0],finding=v[1],boundary_and_next=v[2]) for k,v in findings.items()},
             speed_checks=prior['current_speed_checks'])

html=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>异常技能逐次计价复核</title><style>body{font:16px/1.7 system-ui, sans-serif;color:#203c48;background:#f3f7f8;margin:0}main{max-width:1450px;margin:auto;padding:28px}h1,h2{color:#006a73}section,details{background:white;border:1px solid #d4e3e6;border-radius:10px;padding:18px;margin:18px 0}summary{cursor:pointer;font-weight:650}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #d9e3e6;padding:9px;vertical-align:top}th{background:#e5f2f3;white-space:nowrap}tr:nth-child(even){background:#f8fbfc}.scroll{overflow:auto;max-height:680px}thead{position:sticky;top:0}.warn{border-left:5px solid #b97625;padding:12px;background:#fff6e5}a{color:#007884}.num{font-variant-numeric:tabular-nums}p{max-width:1250px}</style><main><h1>比例异常技能：逐次计价深度复核</h1><p>2026-10-19 · 只读诊断 · 不改评分、不重新上传、不发布安装包</p>']
html.append('<p class="warn">本次不是又按整场均值猜原因：核对11条云端重点记录的全部实际/理论释放、已导出12场96人的重点技能逐次账本，并重新读取部分原始逐次证据。云端是此前缓存，不是实时重新查询；本次没有重跑全场评分。所有金额均为评分标准伤害（M=百万），不是抓包原始伤害。</p>')
html.append(table(['核查内容','数量'],[(k,v)for k,v in payload['scope'].items()]))
html.append('<h2>一、这次新增的关键结论</h2>'+table(['优先级 / 技能','确认到哪一步','不能越过的边界'],[(v[0]+' · '+k,v[1],v[2])for k,v in findings.items()]))
html.append('<h2>二、为何“次数×显示倍率”仍不等于伤害完成率</h2><section><p>正确账本应为：实际伤害=Σ（该次对应形态的完整价格×该次实得倍率比例），理论伤害=Σ（理论时刻对应形态的完整价格）。两边都应使用同一套装备、面板、物魔通道和状态计价规则；不同只来自实际/理论释放时刻、实际状态、命中与形态。</p><p>这里“100%”必须注明分母：1.1倍连续释放的100%，不等于1.3倍首发的100%。同样，15次可靠样本的平均倍率，也不等于49次评分释放的平均倍率。未知记录保留旧价，是当前软件策略，不是“真实打满”的证据。</p><p>本报告把：①明确匹配倍率；②明确零；③未知回退；④无法核实的状态/目标分别列开。没有用一个整体系数把两边强行拉平。</p></section>')
html.append('<h2>三、极度打击：隔离复现倍率基准叠乘</h2><section>'+table(['路线','应有价格：面板100','当前理论组合输出','实际路径输出（均100%命中）','同条件伤害完成率'],[['首发',130,probe['observed_route_prices'][0],130,'84.62%'],['连续',110,130,130,'100%（两边均高估）']])+'<p>路径：score_v2.override_multiplier → 公共倍率1.3；SkillTimelineOutputMixin._skill_burst_apply_ripper_successive_scores → 首发再乘1.3/1.1、连续不乘。实战make_context/value_actual则分别用1.3/1.3、1.1/1.1得100%，都乘公共完整价格130。测试调用当前覆盖和实际换算函数，并抽取当前路线函数执行，不使用人工重写的算法。</p><p>这次已能证明一个和十字问题类似的具体比例错误：完全相同状态、首发打满，理论153.636而实际130。实战普通价格在extreme_engine.py中先写入ordinary_rows；后面的周期后处理不会回写已拷贝的普通价格，理论timeline_runtime.py则统一执行路线后处理。它解释了部分不对称，不意味着全场评分一定相差15.38点。</p><p>另一个可核实的连续边界：0813硬核37.339s结束于38.172333s，而下一次38.155s已开始，偏差−17.333ms；逐Hit证据把下一次认作1Hit、1.1倍、100%。理论路线后处理仅接受端点差≤0.001ms，不能直接用这个条件识别实战连续输入。端点例子说明不能复用过严条件，不是声称实际价格当前一定经过了该后处理。</p></section>')

html.append('<h2>四、云端重点记录：每次实际与理论价格</h2><p>“同刻实测状态”来自上传的实际Buff区间，仅用于找线索；不是理论排轴自己的状态快照。技能释放时刻无状态，也不证明整个伤害期间都无状态。目标减益尚无逐Hit归属，不把任何Boss上的叠冰直接套给该次技能。</p>')
for c in cloud_cases:
    metric=c['metric'];a=c['actual_timeline_summary'];t=c['theory_timeline_summary'];name=c['skill']
    html.append(f'<details open><summary>#{c["id"]} · {esc(c["job"])} · {esc(name)} · {esc(c["nest"])}</summary>')
    html.append(table(['评分次数 实际/理论','可靠倍率次数','显示倍率 / 原生倍率','次数×倍率','标准伤害比（未封顶）','实际/理论M','实际/理论时间轴条数'],[[f'{metric["actual_count"]}/{metric["theory_count"]}',metric['valid_casts'],pct(metric['rate'])+' / '+pct(metric['native_rate']),pct(metric['predicted']),pct(metric['actual']),m(a['total'])+' / '+m(t['total']),f'{a["rows"]}/{t["rows"]}']]))
    html.append('<p>'+esc(findings[name][1])+'</p><p>'+esc(findings[name][2])+'</p>')
    if name=='致命劈砍':
        ordered=sorted(c['actual_timeline'],key=lambda x:x['power'],reverse=True);top=ordered[:2];rest=ordered[2:]
        diag=dict(top2_power=sum(x['power']for x in top),share=sum(x['power']for x in top)/a['total']*100,remaining=sum(x['power']for x in rest),other_median=statistics.median(x['power']for x in rest))
        c['outlier_diagnostic']=diag
        html.append('<p class="warn">新增核实：两次高价合计'+m(diag['top2_power'])+'M，占本技能实际标准伤害'+pct(diag['share'])+'；其余10次中位数'+m(diag['other_median'])+'M。仅为敏感性演示：剔除两次后剩余'+m(diag['remaining'])+'M/理论'+m(t['total'])+'M='+pct(diag['remaining']/t['total']*100)+'，不是建议删除，也不是修复后评分。说明异常高度集中于两次，应该先查两次绑定/回退，而不是改全技能系数。</p>')
    for lane,title in [('actual_timeline','实际'),('theory_timeline','理论')]:
        html.append('<details><summary>'+title+'全部逐次账本</summary>'+table(['序号','时刻s','价格M','动作加速%','爆发窗','期望/利用时长s','同刻实测状态（非理论快照）'],[(i+1,f'{x["seconds"]:.3f}',m(x['power']),x.get('action_speed_percent',0),'是'if x.get('in_burst_window')else'否',f'{x.get("expected_duration",0):.3f}/{x.get("utilized_duration",0):.3f}','、'.join(x['observed_states_at_same_clock']))for i,x in enumerate(c[lane])])+'</details>')
    html.append('</details>')

html.append('<h2>五、本地抓包交叉验证：真实绑定、回退与逐次版本对照</h2><p>这些本地样本与上面的云端记录不是同一场，不能互相替代。账本“换算前”多数由公式反推，只有明确标记“直接导出”的才是观察值；零输出不可反推。时间轴条数包含准备/零价等行，不必等于最终评分释放次数。</p>')
html.append(table(['场次','职业 / 技能','评分实际次数','时间轴行数','已绑定换算','零值行','未知回退行 / M','回退占该技能伤害'],[(x['case'],x['job']+' / '+x['skill'],x['new']['actual_count'],len(x['rows']),x['groups'].get('matched_final_v2_conversion',{}).get('count',0),x['groups'].get('zero_hit_cannot_invert',{}).get('count',0),str(x['groups'].get('unknown_reference_fallback',{}).get('count',0))+' / '+m(x['groups'].get('unknown_reference_fallback',{}).get('power',0)),pct(100*x['groups'].get('unknown_reference_fallback',{}).get('power',0)/x['new']['actual_power'])if x['new']['actual_power']else'—')for x in locals]))
for x in locals:
    html.append(f'<details><summary>{esc(x["case"])} · {esc(x["job"])} · {esc(x["skill"])} · {len(x["rows"])}条逐次账本</summary><p>抓包：{esc(x["capture"])}；角色实体：{esc(x["actor"])}</p>')
    html.append(table(['版本','实际/理论次数','实际/理论M','伤害完成率'],[(ver,f'{d["actual_count"]}/{d["theory_count"]}',m(d['actual_power'])+' / '+m(d['theory_power']),pct(d['actual_power']/d['theory_power']*100)if d['theory_power']else'—')for ver,d in [('4.78',x['old']),('4.85',x['new'])]]))
    rows=[]
    for y in x['rows']:
        h=y.get('hit_evidence')or{};f=y.get('final_v2_step')or{};direct=y.get('direct_v2_observation')or{}
        rows.append((f'{y["seconds"]:.3f}',m((y.get('old')or{}).get('power')),m(y['new']['power']),str(h.get('matched_hits','—'))+'/'+str(h.get('expected_hits','—')),str(h.get('actual_weight','—'))+'/'+str(h.get('expected_weight','—')),pct(h.get('full_rate')),f.get('method','未知'),m(direct.get('before',f.get('before'))),'直接导出核对'if direct else'代数反推，非重新回放'))
    html.append(table(['时刻s','4.78实际M','4.85实际M','匹配/期望Hit','实得/期望权重','该次倍率','计价路径','V2末步前M','前值证据'],rows)+'</details>')

html.append('<h2>六、原逐次证据中的具体缺口</h2><section>')
flower=[x for x in raw if x['skill_name']=='偷袭点'];ghost=[x for x in raw if x['skill_name']=='幻象攻击']
html.append('<p>重新读取0808缓存中'+str(len(flower))+'条去重的偷袭点证据，其中'+str(sum(x.get('actual_hits',0)>0 and x.get('matched_hits')==0 and x.get('realized_weight')==0 for x in flower))+'条“有实际Hit但匹配权重为0”。此处证据集合经过Boss筛选，与全场54条时间轴不是同一个集合，不能直接相减当作丢包数。</p>')
html.append(table(['技能','时刻s','实际/匹配/额外Hit','期望/实得权重','模型','证据备注'],[(x['skill_name'],x['cast_timestamp_ms']/1000,f'{x.get("actual_hits")}/{x.get("matched_hits")}/{x.get("extra_hits")}',f'{x.get("expected_weight")}/{x.get("realized_weight")}',x.get('form'),x.get('reasons'))for x in flower+ghost[:12]]))
html.append('<p>幻象攻击例子：8.815s原记录6Hit，期望25Hit/20.8倍率，实得权重6，计为28.85%；备注同时承认25个理论Hit的时间未确认。这不证明其真实上限应改为6，证明的是当前证据不足以把整段差额都归责于玩家。</p></section>')
html.append('<h2>七、加速参考共性问题（与本次个别高价分开）</h2>'+table(['职业','技能','理论40%加速因子','当前原生参考因子','原生满打/理论价格','0%→40%参考Hit'],[(x['job'],x['skill'],x['theory_default_factor'],round(x['native_complete_factor'],6),pct(x['relative_full_price']),f'{x["reference_hits_zero"]}→{x["reference_hits_40"]}')for x in prior['current_speed_checks']])+'<p>这是当前两个模块之间的一致性检查，不是独立游戏真值证明；实验开关默认未开启。重力开关当前参考为4→5Hit、因子1.25，不能继续沿用旧实验的1.0。旋风不在这六项统一修正名单内。</p>')
html.append('<h2>八、建议修复顺序与验收</h2><section><ol><li>先修极度打击公共倍率/路线倍率唯一归属：固定状态下首发130、连续110；重复结算不叠乘；实际输入小偏差与客户端路线证据一致。</li><li>偷袭点先修Hit槽位/引爆归属，再统一显示与评分的可信样本集合。验收必须保留原伤害总量，并分别展示已证实、零命中、未知，不用0%或100%代替未知。</li><li>致命劈砍追两次极端值：逐Hit目标、体型参考、物魔通道、未知回退。没有原包时需保存上传的逐次计价诊断，而不是只保存汇总。</li><li>幻象攻击闭合主体/衍生对象的25Hit时钟后再谈完成率；九尾优先查特定目标/巢穴的缺绑定，不做全职业硬系数修补。</li><li>热情似火/火焰风暴用同一释放时刻、同一状态做“实际路径/理论路径”对照。确认同价后再比较排轴优劣；不能用实际时刻的Buff列表代替理论状态。</li><li>最终全场重排回归：按职业输出/辅助/特色分别检查，总分变化、耗时和冻结版本对照同时报告。单项敏感性分析不能冒充新评分。</li></ol></section>')
html.append('<h2>证据范围</h2><p>本次新增诊断脚本：tools/report_priority_price_deep_20261019.py；analysis.json保留完整数字、来源文件、逐次表及隔离测试。引用的历史评分版本为冻结v4.78/v4.85；当前源码测试单列，避免把当前修复混作已发布行为。本次没有逐包重解码所有抓包，也没有修改生产评分或CloudBase。</p><p><a href="analysis.json">完整机器可读账本</a> · <a href="../skill_price_risks_20261019/全职业技能计价比例异常清单_20261019.html">上一版异常清单</a></p></main></html>')
payload['provenance']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [AB/'attribution.json',CLOUD/'representative_timelines.json',ROOT/'dragon_nest_builder/live_dps/score_v2.py',ROOT/'dragon_nest_builder/mixins/skill_timeline_output.py']}
(OUT/'analysis.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf8')
dest=OUT/'异常技能逐次计价深度复核_20261019.html'
dest.write_text(''.join(html),encoding='utf8')
print(json.dumps(dict(report=str(dest),scope=payload['scope'],probe=probe),ensure_ascii=False))
