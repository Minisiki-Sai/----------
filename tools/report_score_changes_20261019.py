"""Readable, offline per-player / per-skill accounting report."""
from __future__ import annotations
import html
import json
from pathlib import Path
from audit_score_changes_20261019 import DEST

E=lambda x:html.escape('—' if x is None else str(x))
def f(x,n=2):return '—' if x is None else f'{x:,.{n}f}'
def pct(x):return '—' if x is None else f(x,1)+'%'
def m(x):return f((x or 0)/1e6,2)+' 百万'
def delta(x):return f'{x:+.2f}'
def p(a,b,fn=f):return fn(a)+' → '+fn(b)
def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+E(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def para(s,cls=''):return '<p'+(' class="'+cls+'"' if cls else '')+'>'+s+'</p>'
def detail(label,body,opened=False):return '<details'+(' open' if opened else '')+'><summary>'+label+'</summary>'+body+'</details>'
def impact(s):return sum(s['ratio_attribution'].values())
def aid(r):return r['case']+'_'+r['source_id'].replace('0x','')
def link(r):return '<a href="#'+aid(r)+'">'+E(r['name'])+' · '+E(r['job'])+'</a>'
def timefmt(x):return f(x,3)+'s'
LEVEL_STATUS={'confirmed_combat_row':'战斗记录确认','unknown':'等级来源未确认','unused_theory_level_1':'本场未用，按1级理论处理','used_level_unknown':'已使用，但等级未确认','confirmed_parent_skill':'继承父技能等级'}
METHODS={'unknown_reference_fallback':'没有对应Hit证据，使用回退参考','matched_final_v2_conversion':'匹配到逐次Hit证据，按最终公式反推','zero_power_cannot_invert':'最终值为零，不能反推','zero_hit_cannot_invert':'零命中，不能反推','no_v2_rule':'未接入V2倍率换算','actual_only_summon':'召唤物单独周期换算'}

def skill_explanation(s):
    u,v=s['scored_old'],s['scored_new'];raw0,raw1=s['old'],s['new']
    ra=s['ratio_attribution'];out=[]
    out.append(para('对<strong>整场伤害完成率</strong>的分摊影响：<strong>'+delta(impact(s))+' 个百分点</strong>。这是对两版结果的账目拆解，不是“删掉此技能重排一次”得到的因果实验。'))
    if raw0.get('excluded_from_score') and not raw1.get('excluded_from_score'):
        out.append(para('重要：旧版把它列为“不参与评分”，新版重新纳入。旧表里即使显示实际伤害，旧评分分子也不包含这部分；以下按真正计分的数值比较。旧版的低伤害豁免在 V2 中取消，这不是玩家突然少放了技能。','warn'))
    if not raw1:out.append(para('新版评分明细不再列入此技能。此处保留旧版一侧，检查它退出理论分母后的影响。'))
    if not raw0:out.append(para('旧版明细未列入此技能，新版增加该评分条目。'))
    data=[]
    for title,key in [('实际释放次数','actual_count'),('理论释放次数','theory_count')]:
        data.append([title,p(u.get(key,0),v.get(key,0),lambda n:f(n,0))])
    data.extend([['实际标准伤害',p(u.get('actual_power',0),v.get('actual_power',0),m)],
                 ['理论标准伤害',p(u.get('theory_power',0),v.get('theory_power',0),m)],
                 ['每次实际平均计价（事后平均）',p(s['actual_change']['old_mean'],s['actual_change']['new_mean'],lambda x:'—' if x is None else m(x))],
                 ['每次理论平均计价（事后平均）',p(s['theory_change']['old_mean'],s['theory_change']['new_mean'],lambda x:'—' if x is None else m(x))],
                 ['实际/评分等级',f(raw0.get('actual_level'),0)+' / '+f(raw0.get('scoring_level'),0)+' → '+f(raw1.get('actual_level'),0)+' / '+f(raw1.get('scoring_level'),0)],
                 ['等级证据',E(LEVEL_STATUS.get(raw0.get('level_status'),raw0.get('level_status','未列出')))+' → '+E(LEVEL_STATUS.get(raw1.get('level_status'),raw1.get('level_status','未列出')))]])
    out.append(table(['核对项','v4.78 → v4.85'],data))
    ac=s['actual_change'];tc=s['theory_change']
    nc0,nc1=u.get('actual_count',0),v.get('actual_count',0)
    nt0,nt1=u.get('theory_count',0),v.get('theory_count',0)
    if nc0==nc1 and nt0==nt1:
        text='两边的实际次数、理论次数都未改变；变化主要在每次技能的标准计价，而不是释放数量。'
    else:
        text=f'实际计分次数从 {nc0} 次变为 {nc1} 次，理论从 {nt0} 次变为 {nt1} 次。'
        if nt1>nt0:text+='理论要求更多次，会在其他条件相同时拉低完成率。'
        elif nt1<nt0:text+='理论要求减少，会在其他条件相同时抬高完成率。'
        if nc0!=nc1:text+='实际计分次数变化不等于网络丢包，还可能是有效区间筛选、识别/合并、豁免取消；可展开下方逐次表核对。'
    out.append(para(text))
    if s.get('release_event_examples'):
        sample_rows=[]
        for ver,events in s['release_event_examples'].items():
            for e in events:
                sample_rows.append([ver,f(e['timestamp_ms']/1000,3),E(e.get('skill_id')),E(e.get('packet_kind')),f(e.get('cast_time')),f(e.get('action_speed_percent'))])
        out.append(detail('动作/释放识别样例：两版最早六条记录',table(['版本','时刻(s)','技能ID','事件类型','识别动作秒数','动作速度%'],sample_rows)+para('这些是各版独立解析后的释放事件，不是把相邻伤害自动当作多次按键。计数定义变化时，不能按时间相近就强行一一对应。')))
    out.append(table(['分摊因素','影响整场伤害完成率（百分点）','是什么意思'],[
        ['实际次数',delta(ra.get('actual_count',0)),'这两版纳入计分的释放次数变化。新增/退出技能时无法唯一拆出单价，把该部分整体归入加入/退出。'],
        ['实际单次计价',delta(ra.get('actual_price',0)),'同一次数单位的平均标准伤害变化，包含状态、倍率及命中换算。'],
        ['理论次数',delta(ra.get('theory_count',0)),'理论轴要求释放更多/更少次导致分母变化。'],
        ['理论单次计价',delta(ra.get('theory_price',0)),'理论每次技能更贵/更便宜导致分母变化。']]))
    rule=s.get('rule') or {};step=s['v2_final_step']
    if rule:
        out.append(para('<strong>新版程序采用的倍率依据：</strong>'+E(rule.get('basis_note') or rule.get('status','未提供'))))
        if s['skill']=='冲击之手':
            out.append(para('发布函数 actual_reference_attrs 已核对：对有命中证据的实战释放，先按“圣物数量=3”建立完整参考，再由实际倍率比例换算；理论轴仍使用自己的圣物状态。这不等于识别到玩家每次都有三个圣物，不能把参考状态当作实际状态。','note'))
        if s['skill']=='旋转冲击':
            out.append(para('发布函数 actual_reference_attrs 已核对：有命中证据时，实战计价先用“怒气=6”的参考，再交给命中权重换算。尤其在改版前抓包中，需要区分旧动作、当前完整参考、实际倍率三层；不是证明玩家当时真的消耗了六怒气。','note'))
        out.append(para('新版内部原有完整倍率 '+f(rule.get('old_multiplier'),4)+' → V2 完整倍率 '+f(rule.get('full_multiplier'),4)+'；参考形态：'+E(rule.get('reference_form','未提供'))+'。注意：这里的“原有”是 v4.85 内部旧口径，不能当作已经单独证明 v4.78 的数值。'))
        if rule.get('basis_uncertain'):
            out.append(para('仍有核查边界：程序自己把这个技能标为“倍率口径未闭合/近似模型”。现在能确认它怎样影响分数，但不能据此宣布该模型与游戏机制完全一致。','warn'))
        wins=rule.get('boss_windows',[])
        uniq=[];seen=set()
        for w in wins:
            token=(w.get('boss_name'),w.get('boss_factor'),w.get('reference_factor'),w.get('reference_fallback'))
            if token in seen:continue
            seen.add(token);uniq.append([E(w.get('boss_name','未知Boss')),pct(100*w.get('boss_factor',1)),pct(100*w.get('reference_factor',1)),'是' if w.get('reference_fallback') else '否'])
        if uniq:out.append(detail('查看各 Boss 的体型/参考倍率校准',table(['Boss','体型上限因子','参考回退因子','是否回退'],uniq)+para('这些因子只解释程序如何定理论上限；不能把不同 Boss 的最大命中简单相加。')))
    out.append(para('新版可匹配 Hit 证据 '+str(step['matched_evidence_casts'])+' 次；其中估计关联 '+str(step['estimated_casts'])+' 次。逐次完整倍率比例的简单平均为 '+pct(step['mean_full_rate'])+'；按 Boss 校准后平均为 '+pct(step['mean_boss_rate'])+'。这些是诊断平均值，<strong>不是应用到整场的统一乘数</strong>。'))
    if step['matched_evidence_casts'] or abs(step['net_change'])>.01:
        out.append(para('在新版同一份轴、同一装备和状态计价下，正常正值分支可反推的最后 V2 换算：'+m(step['recovered_input'])+' → '+m(step['same_rows_output'])+'，净变化 '+delta(step['net_change']/1e6)+' 百万。此步骤既替换命中倍率，也撤销已包含的名义加速/体型因子，因此不一定只会扣伤害。'))
        if step['noninvertible_zero_rows']:
            out.append(para('另有 '+str(step['noninvertible_zero_rows'])+' 次为零值/零命中，无法仅从结果反推出换算前伤害，未伪造填入上面的反推总量。','warn'))
    if nc1 and nt1:
        a=v.get('actual_power',0);t=v.get('theory_power',0)
        if a>0 and t>0:
            out.append(para('新版这项的账可以这样读：次数比 '+pct(100*nc1/nt1)+' × 实际/理论平均单次计价比 '+pct(100*(a/nc1)/(t/nt1))+' = 未封顶伤害完成率 '+pct(100*a/t)+'。每次计价已经包含 Hit 换算，不能再额外乘一遍倍率实现率。'))
    if s['skill'] in ('重击','神圣十字','雷电冲击','审判锤','神圣断言','神圣复仇'):
        out.append(para('对十字军/圣骑的特别边界：审判力量等状态会影响单次计价。单看伤害均值不能证明某次没有 Buff；BlueMoon 的 UDP 与逐 Hit 复核仍存在“标签缺失不等于实际缺失”的问题，不能把这一项全部归咎于漏放审判力量。','note'))
    out.append(para('已确认的是计分账目与程序采用的参考值。若要把“单次计价残差”进一步唯一拆成某个 Buff、某个 Debuff、技能等级或瞬时面板，需要对两版逐时刻状态做独立反事实复算；本表没有把这些尚未隔离的因素冒充已确认根因。','muted'))
    return ''.join(out)

def support_block(r):
    out=[]
    totals=r['support_totals'];x,y=r['versions']['4.78'],r['versions']['4.85']
    if all(t>0 for a,t in totals):
        out.append(para('辅助分不是覆盖率的简单平均。这里按“实际边际收益总量 ÷ 理论边际收益总量 × 100”计分。旧版 '+f(totals[0][0],4)+' ÷ '+f(totals[0][1],4)+' = '+f(x['support_score'])+'；新版 '+f(totals[1][0],4)+' ÷ '+f(totals[1][1],4)+' = '+f(y['support_score'])+'。收益数值是引擎的相对贡献单位，不是实际打出的伤害。'))
    else:
        out.append(para('至少一版没有可计算的正值理论辅助收益，不能用0作分母。两版实际展示的辅助分为 '+p(x['support_score'],y['support_score'])+'；实际/理论收益总量为 '+E(totals)+'，无有效收益时的默认展示值不代表实战额外完成了辅助。'))
    rows=[]
    for s in r['supports']:
        u,v=s['old'],s['new'];change=(s['actual_term'] or 0)+(s['theory_term'] or 0)
        expl=[]
        if bool(u.get('included_in_score'))!=bool(v.get('included_in_score')):expl.append('计分纳入状态改变')
        if v.get('included_in_score') and v.get('actual_contribution',0)>0 and v.get('theory_contribution',0)==0:
            expl.append('重点：新版实际收益仍计入，但该项理论收益为0；会抬高总辅助收益比，不能解释为单项覆盖超额完成，需核查理论效果是否漏排')
        ca,ct=u.get('actual_coverage'),u.get('theory_coverage');na,nt=v.get('actual_coverage'),v.get('theory_coverage')
        if ca is not None and na is not None and abs(ca-na)>.001:expl.append('实际覆盖区间/范围改变')
        if ct is not None and nt is not None and abs(ct-nt)>.001:expl.append('理论覆盖安排改变')
        if ca is not None and na is not None and ct is not None and nt is not None and abs(ca-na)<.001 and abs(ct-nt)<.001 and abs(change)>.05:
            expl.append('覆盖时长基本不变，但覆盖到的伤害价值/边际收益或全体收益权重改变；不是漏放次数即可解释')
        reason=v.get('effective_reason') or ''
        if reason:expl.append(reason)
        expl.append('实际收益变化分摊 '+delta(s['actual_term'] or 0)+'；理论收益变化分摊 '+delta(s['theory_term'] or 0))
        rows.append([E(s['skill'])+'<br><small>'+E(s['effect'])+'</small>',
                     p(ca,na,lambda n:'—' if n is None else pct(n*100)),p(ct,nt,lambda n:'—' if n is None else pct(n*100)),
                     p(u.get('actual_contribution',0),v.get('actual_contribution',0)),p(u.get('theory_contribution',0),v.get('theory_contribution',0)),
                     delta(change),'<br>'.join(E(z) for z in expl)])
    out.append(table(['技能/效果','实际覆盖','理论覆盖','实际收益','理论收益','对辅助分变化的分摊','原因'],rows))
    out.append(para('注意：“对辅助分变化的分摊”可以相加得到辅助分差；但辅助分还要乘该版本的辅助权重，才影响最终总分。所有效果共用总分母，某个效果分摊为正，不代表它单项完成率必然上升。'))
    return ''.join(out)

def traits(r):
    x,y=r['versions']['4.78'],r['versions']['4.85'];out=[]
    if x['specialty_score'] is None and y['specialty_score'] is None:return para('两版都没有单列职业特色分。')
    out.append(para('职业特色分 '+p(x['specialty_score'],y['specialty_score'])+'；计入总分的部分 '+p(x['specialty_contribution'],y['specialty_contribution'])+'。'))
    if y['healing_completion'] is not None or x['healing_completion'] is not None:
        labels={'healing_effective_realization':'有效治疗兑现率','healing_rescue_completion':'危险血量救急诊断完成率','healing_cast_completion':'有效治疗释放完成率','healing_damage_reduction_completion':'减伤治疗项完成率','effective_healing':'计入角色的有效治疗量','healing_evaluated_casts':'参与评价的治疗次数'}
        out.append(table(['治疗指标','v4.78 → v4.85'],[[label,p(x[k],y[k],lambda n:f(n,0) if k in ('effective_healing','healing_evaluated_casts') else pct(n))] for k,label in labels.items()]))
        for ver,z in r['versions'].items():
            rd=z.get('healing_rescue_detail') or {};weights=rd.get('effective_weights',{})
            penalty=rd.get('penalty',0)
            score=weights.get('healing',0)*(z.get('healing_effective_realization') or 0)+weights.get('rescue',0)*(z.get('healing_rescue_completion') or 0)+weights.get('mitigation',0)*(z.get('healing_damage_reduction_completion') or 0)-penalty
            if abs(score-(z['specialty_score'] or 0))<1e-6:
                out.append(para(ver+' 可核对公式：治疗兑现率 × '+pct(weights.get('healing',0)*100)+' ＋ 救急诊断完成率 × '+pct(weights.get('rescue',0)*100)+' ＋ 减伤项 × '+pct(weights.get('mitigation',0)*100)+' − 救急责任扣分 '+f(penalty)+' = '+f(score)+'。'))
            else:out.append(para(ver+' 特色内部权重/封顶需要另查，未套用不能对账的公式。','warn'))
            if rd.get('evidence'):out.append(para(ver+' 引擎证据：'+E(rd['evidence']),'muted'))
        h=[]
        for name,versions in r['healing_skills'].items():
            a,b=versions.get('4.78',{}),versions.get('4.85',{})
            h.append([E(name),p(a.get('cast_count',0),b.get('cast_count',0),lambda n:f(n,0)),p(a.get('healing',0),b.get('healing',0),lambda n:f(n,0))])
        out.append(table(['治疗技能','识别的释放次数','回放中归因的治疗量（非直接特色分）'],h))
        out.append(para('海龙新版识别到更多 HP 恢复/扣血记录，并改变未明来源恢复与分组责任的处理，因而“缺血是否解决”“哪些治疗有效”“个人应承担多少救急责任”都会变化。未明来源回血不等于给该奶增加个人治疗量；两者须分别查看。'))
        episodes=[]
        for ver,z in r['versions'].items():
            for ep in (z.get('healing_rescue_detail') or {}).get('episode_penalties',[]):
                episodes.append([ver,E(ep.get('target_id')),p(ep.get('start_seconds'),ep.get('end_seconds'),timefmt),E(ep.get('end_reason')),pct(ep.get('team_completion')),f(ep.get('responsibility_share')),f(ep.get('penalty')),E(ep.get('status'))])
        out.append(detail('逐个危险血量事件与责任扣分',table(['版本','目标','时间','结束原因','团队解决率','本人责任比例','本人扣分','状态'],episodes)))
    if y['tank_completion'] is not None or x['tank_completion'] is not None:
        keys={'tank_taunt_completion':'挑衅完成率','tank_shell_completion':'蛋壳完成率','tank_attack_down_completion':'减攻击完成率','tank_taunt_coverage':'实际挑衅覆盖','tank_taunt_theory_coverage':'理论挑衅覆盖','tank_shell_coverage':'实际蛋壳覆盖','tank_shell_theory_coverage':'理论蛋壳覆盖','tank_shell_theory_casts':'理论蛋壳次数'}
        out.append(table(['T职责指标','v4.78 → v4.85'],[[label,p(x[k],y[k])] for k,label in keys.items()]))
        for ver,z in r['versions'].items():
            parts=[z[k] for k in ('tank_taunt_completion','tank_shell_completion','tank_attack_down_completion')]
            if all(n is not None for n in parts) and z['tank_completion'] is not None:
                combined=sum(parts)/3
                if abs(combined-z['tank_completion'])<1e-6:
                    out.append(para(ver+' T职责核对：（挑衅 '+f(parts[0])+' ＋ 蛋壳 '+f(parts[1])+' ＋ 减攻击 '+f(parts[2])+'）÷3 = '+f(combined)+'。单项可以超过100%，最终计入总分仍需经过职责权重和上限处理。'))
        for k in ('tank_taunt_evidence','tank_shell_evidence','tank_attack_down_evidence'):
            out.append(para(E(x.get(k))+' → '+E(y.get(k))))
        out.append(para('同一 Boss 分组规则改变的是“该替谁承担职责”的分母，不是补造挑衅或蛋壳释放。若新版证据写明按同组人秒评价，表示分隔战斗中的另一组不再自动算作此 T 漏覆盖。'))
    return ''.join(out)

def casts_report(r):
    casts=json.loads((DEST/r['cast_ledger']).read_text(encoding='utf-8'))
    rows=[];has_exported=any(c.get('direct_v2_observation') for c in casts)
    for c in casts:
        u,v=c.get('old') or {},c.get('new') or {};h=c.get('hit_evidence') or {};iv=c.get('final_v2_step') or {}
        rows.append([E(c['skill']),f(c['seconds'],3),m(u.get('power',0)),m(v.get('power',0)),
                     '—' if iv.get('before') is None else m(iv['before']),pct(h.get('full_rate')),pct(h.get('boss_rate')),
                     f(h.get('matched_hits'),0)+' / '+f(h.get('expected_hits'),0),
                     '旧版独有' if not v else ('新版独有' if not u else '同名同毫秒匹配'),E(METHODS.get(iv.get('method'),iv.get('method','')))])
        if has_exported:
            exported=c.get('direct_v2_observation') or {}
            rows[-1].append(m(exported['before'])+' → '+m(exported['after']) if exported else '—')
    path=DEST/(aid(r)+'_逐次计价.html')
    headers=['技能','释放时间(s)','旧标准伤害','新标准伤害','新版最后换算前（反推）','完整倍率比例','Boss校准比例','命中/期望Hit','匹配','换算方法']
    if has_exported:headers.append('引擎导出参考→最终值（正向核验）')
    path.write_text(shell(r['name']+' · 逐次标准伤害对照',para(E(r['capture'])+' / '+E(r['job']))+para('这里是标准计价，不是 UDP 真实伤害。相同技能按毫秒时间戳对照；不能匹配的保留两边，不擅自合并。换算前仅对正常正值分支反推；零命中无法反推的留空，不标为直接观察到的值。若有最后一列，则来自引擎导出的同名同时刻参考值，已按发布公式正向核验；不是将反推结果重标为导出值。')+table(headers,rows)),encoding='utf-8')
    return path.name

def player_block(r):
    x,y=r['versions']['4.78'],r['versions']['4.85'];d=r['total_decomposition'];body=[]
    body.append('<h3 id="'+aid(r)+'">'+E(r['name'])+' · '+E(r['job'])+'　总分 '+p(x['total_score'],y['total_score'])+'（'+delta(y['total_score']-x['total_score'])+'）</h3>')
    body.append(para('这一人的总分变化：输出部分 '+delta(d['output_contribution'])+' ＋ 辅助部分 '+delta(d['support_contribution'])+' ＋ 职责部分 '+delta(d['specialty_contribution'])+' ＋ 承伤扣分变化的影响 '+delta(d['survival'])+(' ＋ 总分上下限处理 '+delta(d['clamp']) if abs(d.get('clamp',0))>1e-8 else '')+' = '+delta(y['total_score']-x['total_score'])+' 分。这里用的是各项<strong>已经加权、处理溢出后的贡献</strong>。'))
    if x['role']!=y['role']:
        body.append(para('先注意职责变更：'+E(x['role'])+' → '+E(y['role'])+'。旧依据：'+E(x['role_reason'])+'；新依据：'+E(y['role_reason'])+'。这会改变特色分是否计入以及输出/辅助权重。','warn'))
    fields=[('output_score','输出分'),('support_score','辅助分'),('specialty_score','特色分'),('output_weight','输出权重'),('support_weight','辅助权重'),('specialty_weight','特色权重'),('output_contribution','输出计入总分'),('support_contribution','辅助计入总分'),('specialty_contribution','特色计入总分'),('survival_penalty','承伤扣分'),('output_seconds','计分可输出秒数')]
    body.append(table(['指标','v4.78','v4.85'],[[label,f(x[k]*100 if 'weight' in k and x[k] is not None else x[k]),f(y[k]*100 if 'weight' in k and y[k] is not None else y[k])] for k,label in fields]))
    body.append(para('权重为百分数。辅助分升高、但辅助计入总分减少是可能的：两版辅助权重也在变化。输出分超过 100 时，还有溢出分配，不能简单拿展示分乘一个固定权重。'))
    weight_rows=[]
    for prefix,label in [('output','输出'),('support','辅助'),('specialty','特色')]:
        s0,s1=x[prefix+'_score'] or 0,y[prefix+'_score'] or 0
        w0,w1=x[prefix+'_weight'] or 0,y[prefix+'_weight'] or 0
        c0,c1=x[prefix+'_contribution'] or 0,y[prefix+'_contribution'] or 0
        score_term=(s1-s0)*(w0+w1)/2
        weight_term=(w1-w0)*(s0+s1)/2
        overflow=(c1-s1*w1)-(c0-s0*w0)
        assert abs(score_term+weight_term+overflow-(c1-c0))<1e-7
        weight_rows.append([label,delta(score_term),delta(weight_term),delta(overflow),delta(c1-c0)])
    body.append(detail('总分还受权重影响：分数变化与权重变化分别占多少',table(['部分','该项分数变化的影响','该项权重变化的影响','溢出/特殊贡献处理变化','合计影响总分'],weight_rows)+para('同样采用对称分摊；每行前三列相加等于最后一列。这是解释已算出的两版结果，不是把整个队伍重新固定权重跑过一次。')))
    body.append(para('本人的主要输出变化项：'+'；'.join(E(s['skill'])+' '+delta(impact(s))+' 个整场伤害完成率百分点' for s in r['skills'][:4])+'。'))
    body.append(para('主要辅助变化项：'+'；'.join(E(s['skill'])+' / '+E(s['effect'])+' '+delta((s['actual_term'] or 0)+(s['theory_term'] or 0))+' 辅助分' for s in r['supports'][:3])+'。'))
    body.append(para('实际标准伤害 '+p(x['actual_standard_power'],y['actual_standard_power'],m)+'；理论标准伤害 '+p(x['theory_standard_power'],y['theory_standard_power'],m)+'。'))
    raw_old,raw_new=r['replay_player']['4.78'],r['replay_player']['4.85']
    body.append(para('作为交叉检查，回放归因到此人的原始伤害为 '+p(raw_old.get('damage'),raw_new.get('damage'),m)+'，命中记录数 '+p(raw_old.get('hits'),raw_new.get('hits'),lambda n:f(n,0))+'。上面的“标准伤害”是统一装备后的重新计价，不是把这份原始伤害直接乘一个全局系数。原始伤害不变但标准完成率变，不能据此说网络少抓了伤害包。'))
    formula=[]
    for ver,z in r['versions'].items():
        w=z['validated_burst_weight']
        formula.append([ver,pct(z['theory_damage_completion']),pct(z['burst_usage']),pct(w*100),f(z['output_score'])])
    body.append(table(['版本','整场伤害完成率','爆发窗口完成率（未封顶显示）','爆发计分权重','最终输出分'],formula))
    body.append(para('每一版已核对：输出分 = 整场伤害完成率 ×（1−爆发权重）＋ min(爆发完成率,100%) × 爆发权重。逐技能分摊表解释前一项；爆发窗口变化另看下表，不把两者混在一起。'))
    whole_change=(1-y['validated_burst_weight'])*y['theory_damage_completion']-(1-x['validated_burst_weight'])*x['theory_damage_completion']
    burst_change=y['validated_burst_weight']*min(100,max(0,y['burst_usage']))-x['validated_burst_weight']*min(100,max(0,x['burst_usage']))
    body.append(para('输出分变化单独对账：整场项 '+delta(whole_change)+' 分 ＋ 爆发窗口项 '+delta(burst_change)+' 分 = '+delta(y['output_score']-x['output_score'])+' 分。爆发部分尚未逐技能作独立状态反事实归因，不能把它重复加到某个技能的普通 Hit 损失上。'))
    window_rows=[]
    for ver,z in r['versions'].items():
        for w in z['burst_windows']:
            window_rows.append([ver,E(w['label']),p(w['start_seconds'],w['end_seconds'],timefmt),m(w['actual_power']),m(w['theory_power']),pct(w['completion'])])
    body.append(detail('查看每一个爆发/加速窗口的比较',table(['版本','窗口','时间','实际标准伤害','理论标准伤害','该窗口完成率'],window_rows)+para('不同 Buff 的窗口可能重叠，不能直接把这些行相加当整场爆发总量；最终计分以合并区间为准。')))
    burstrows=[]
    for ver,z in r['versions'].items():
        diag=z['burst_diagnostics'];burstrows.append([ver,E(diag.get('mode','旧版施放时刻口径')),str(len(z['burst_windows'])),f(diag.get('actual_window_power'),0),f(diag.get('theory_window_power'),0),str(len(diag.get('discarded_windows',[])))])
    body.append(detail('爆发窗口与标准装备核对',table(['版本','窗口计价模式','展示窗口数','窗口实战标准量','窗口理论标准量','过滤窗口数'],burstrows)+table(['面板','v4.78','v4.85'],[[E(k),f(x['panel'].get(k)),f(y['panel'].get(k))] for k in dict.fromkeys([*x['panel'],*y['panel']]) if x['panel'].get(k)!=y['panel'].get(k)])+para('面板是两版各自默认评分装备的计算值，不是玩家实际装备。面板同时作用于实战与理论；不能把面板涨幅直接当作分数涨幅。')+para('等级/纹章证据详见数据附件。个人可输出区间变化：'+p(x['output_seconds'],y['output_seconds'])+' 秒。与历史列表上传显示的全队时间是不同用途。')))
    overview=[]
    for s in r['skills']:
        a,b=s['scored_old'],s['scored_new']
        overview.append([E(s['skill']),p(a.get('actual_count',0),b.get('actual_count',0),lambda n:f(n,0)),p(a.get('theory_count',0),b.get('theory_count',0),lambda n:f(n,0)),p(a.get('actual_power',0),b.get('actual_power',0),m),p(a.get('theory_power',0),b.get('theory_power',0),m),delta(impact(s))])
    body.append('<h4>输出技能：全部列出，按对变化的影响排序</h4>'+table(['技能','实战计分次数','理论次数','实战标准伤害','理论标准伤害','影响整场完成率（百分点）'],overview))
    for s in r['skills']:body.append(detail(E(s['skill'])+'：'+delta(impact(s))+' 个百分点｜点击看次数、计价、Hit 与未定因素',skill_explanation(s)))
    body.append('<h4>辅助：逐技能、逐效果</h4>'+support_block(r))
    body.append('<h4>职业特色</h4>'+traits(r))
    survivalkeys={'incoming_hp_loss':'识别到的 HP 损失','team_mechanic_baseline_hp_loss':'机制扣血基线','avoidable_hp_loss':'可归责损失','avoidable_hp_loss_per_minute':'每分钟可归责损失/最大HP','point_count':'HP证据点','damage_penalty':'伤害扣分','death_penalty':'死亡扣分','non_death_penalty_cap':'非死亡扣分上限'}
    surv=[]
    for k,label in survivalkeys.items():surv.append([label,p((x['survival'].get('hp_shadow') or {}).get(k),(y['survival'].get('hp_shadow') or {}).get(k))])
    body.append(detail('承伤扣分为何变化',table(['项目','v4.78 → v4.85'],surv)+para('新增 HP 扣血记录不等于全部作为失误扣分。需先减去机制基线，再应用归责、HP 标准和上限；这里保留各层数值。')))
    castfile=casts_report(r)
    body.append(para('<a href="'+E(castfile)+'">查看这个人的全部逐次标准计价（含新旧独有释放）</a> · <a href="'+E(r['cast_ledger'])+'">逐次 JSON 证据</a>'))
    return ''.join(body)

def shell(title,body):
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+E(title)+'</title><style>body{font:16px/1.75 "Microsoft YaHei",sans-serif;color:#173a44;background:#f2f7f7;margin:0}main{max-width:1500px;margin:auto;background:white;padding:28px}h1,h2,h3{line-height:1.4}h2{border-bottom:3px solid #168184;padding:12px 0;margin-top:48px}h3{padding:16px;background:#e9f5f4;scroll-margin-top:20px}h4{margin-bottom:8px}a{color:#006f83}p{max-width:1300px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{border:1px solid #d4e3e4;padding:9px 12px;text-align:left;vertical-align:top}th{background:#eaf2f3;white-space:nowrap}tr:nth-child(even){background:#f8fbfb}.scroll{overflow:auto;margin:15px 0}details{border:1px solid #d8e5e5;border-radius:6px;margin:10px 0;padding:10px 14px}summary{cursor:pointer;font-weight:600;color:#146672}.warn{background:#fff4dc;padding:14px;border-left:4px solid #ba7d13}.note{background:#edf6fc;padding:12px}.muted,small{color:#566d73;font-size:13px}nav a{display:inline-block;margin:6px 12px 6px 0}code{background:#eef4f5;padding:2px 5px}@media print{details{break-inside:avoid}main{padding:10px}}</style><main><h1>'+E(title)+'</h1>'+body+'</main></html>'

def main():
    data=json.loads((DEST/'attribution.json').read_text(encoding='utf-8'));players=data['players'];out=[]
    out.append(para('核对范围：刚才两版对照的 3 场黑龙硬核＋3 场梦魇海龙，共 48 人次、630 个角色技能条目、284 项辅助效果、14,040 个新旧释放时间配对条目。不是数据库全量，也不是 630 个不同技能。'))
    out.append(para('<strong>结论边界：</strong>所有人的总分差、整场输出完成率差、辅助分差都已逐项对账，误差仅为浮点尾数；但“账怎么算出来”不等于“游戏机制全部正确”。审判力量状态、程序标记未闭合的倍率模型、逐时刻 Buff／Debuff 的唯一归因，仍明确保留未定项。此次没有修改程序、重传评分或更改云端。','warn'))
    out.append('<h2>先用最直白的话解释</h2>'+para('同一份抓包，玩家操作没有改变。版本更换后，尺子变了：有的技能理论要求次数变了，有的单次标准伤害变了，有的以前不计分、现在计分了，命中不足也开始影响标准伤害；辅助、奶和 T 又有各自的尺子。因此不能把所有分数下降统称为“玩家漏 Hit”。'))
    out.append(table(['问题','本次确认的答案'],[
        ['为什么次数和倍率实现率看起来高，输出分仍会下降？','两版的理论平均单次计价、理论技能构成和爆发窗口可能不同。实际次数/理论次数 × 平均单次标准计价比，才等于未封顶的伤害完成率。'],
        ['是不是用了一个整场统一系数？','不是。每次施放先按相应状态计价，再换算命中倍率，最后相加。报告里的“平均单次计价”是事后解释用的除法。'],
        ['辅助分上升，为什么总分还下降？','输出、辅助的权重也变了；展示分变高不保证加权贡献更高。下文每人都给出真正计入总分的数。'],
        ['奶和 T 为什么在海龙上涨？','新版 HP 恢复识别、治疗需求/救急责任，以及同 Boss 分组职责发生变化。不是把未知回血全部算到奶头上，也不是凭空增加 T 的释放。'],
        ['这些数据能证明所有新版低分都是正确的吗？','不能。已确认计算路径；对机制依据不足的技能保留警示，尤其不把审判力量标签缺失当作玩家确实未放。']]))
    out.append('<h2>BlueMoon 1011：先看一个完整例子</h2>')
    blue=next(r for r in players if r['case']=='black_1011' and r['name']=='BlueMoon')
    x,y=blue['versions']['4.78'],blue['versions']['4.85']
    out.append(para('输出分 '+p(x['output_score'],y['output_score'])+'，下降 '+f(x['output_score']-y['output_score'])+' 分；总分 '+p(x['total_score'],y['total_score'])+'。实际标准伤害其实 '+p(x['actual_standard_power'],y['actual_standard_power'],m)+'，不是少了这么多伤害包；但理论标准伤害 '+p(x['theory_standard_power'],y['theory_standard_power'],m)+'，分母涨得更快。'))
    out.append(table(['技能','直接证据','应怎样理解'],[
        ['连锁闪电','实际 68→68 次；理论 81→82 次；新程序记录完整参考由内部 5 倍改为 8 倍。','新基准和命中换算一起影响完成率，不能说少放了很多连锁。'],
        ['重击','实际 39→39，理论 41→41；新版本最后换算前约 541.43 百万，换算后 510.02 百万；理论 754.42 百万。','最后 Hit 换算只解释约 31.41 百万减少，不能解释剩下的全部差距。逐次状态计价及理论状态仍须独立核实。'],
        ['神圣之锤攻击','旧版理论 0 次，新版理论 281 次；实际 148→339 次。','旧版存在实际计分而理论零次数的条目，新版补入理论比较；同时两版普通攻击次数识别/分组口径也变了，不可直接比次数。']]))
    out.append(para('BlueMoon 的审判力量：之前逐包审计找到的状态记录均被现解析接收，但仍不能证明标记缺失区间真的没 Buff。部分相邻重击的伤害证据并不支持简单“没有审判”的结论。此项继续列为高优先级未定问题，而非已证实的玩家操作损失。','warn'))
    out.append('<h2>本次确认的共性变化与待复核点</h2>')
    excluded=[(r,s) for r in players for s in r['skills'] if s['old'].get('excluded_from_score') and not s['new'].get('excluded_from_score')]
    uncertain=[(r,s) for r in players for s in r['skills'] if (s.get('rule') or {}).get('basis_uncertain')]
    out.append(para('① 旧低伤害豁免取消：本样本 '+str(len(excluded))+' 个角色技能条目从旧版“不参与评分”变为新版计分。有些只使用 1～3 次的技能，旧版理论为零，新版要求几十次。这是明确的规则变化，不是抓包突然少录。'))
    out.append(detail('查看全部豁免变化条目',table(['抓包/角色','技能','旧实际展示次数','新实际计分次数','新理论次数','影响整场完成率'],[[link(r),E(s['skill']),f(s['old'].get('actual_count'),0),f(s['new'].get('actual_count'),0),f(s['new'].get('theory_count'),0),delta(impact(s))] for r,s in excluded])))
    out.append(para('② 新版用本人的可输出区间和新版技能安排，48 人的评分区间均与旧版全队区间不同。即使是同一抓包，“计分释放次数”也可能因区间筛选减少；未逐包证明前，不把这当丢包。'))
    out.append(para('③ 默认标准装备、无属性攻和技能参考有更新。两边都按各版本标准装备重算，绝对伤害不可直接当真实伤害差。统一比例提升通常会在分子/分母相除时抵消；真正影响比例的是不同技能、状态或安排变化不一致。'))
    out.append(para('④ 奶/T 的职责和辅助权重改变。海龙的上升往往来自职责加分，不是输出更好；有的辅助分升了、加权贡献反而少了。下面每人都列实际数值。'))
    out.append(para('⑤ 程序仍标记 '+str(len(uncertain))+' 个角色技能条目的倍率口径为未闭合/近似（有重复职业技能）。必须把“程序采用的假设”与“已重新核实客户端”分开，不能用分数下降反证玩家操作差。'))
    out.append(detail('查看倍率口径警示项',table(['角色','技能','程序记录的边界','影响整场完成率'],[[link(r),E(s['skill']),E(s['rule'].get('basis_note','')),delta(impact(s))] for r,s in uncertain])))
    out.append('<h2>48 人总分变化总览</h2>')
    overview=[]
    for r in players:
        a,b=r['versions']['4.78'],r['versions']['4.85'];dd=r['total_decomposition']
        overview.append([E(r['case']),link(r),p(a['total_score'],b['total_score']),p(a['output_score'],b['output_score']),p(a['support_score'],b['support_score']),p(a['specialty_score'],b['specialty_score']),delta(dd['output_contribution']),delta(dd['support_contribution']),delta(dd['specialty_contribution']),delta(dd['survival'])])
    out.append(table(['场次','角色（点击跳转）','总分','输出分','辅助分','特色分','输出对总分影响','辅助对总分影响','特色对总分影响','扣分变化影响'],overview))
    for case in data['cases']:
        out.append('<h2>'+E(case['name'])+'</h2>')
        a,b=case['versions']['4.78'],case['versions']['4.85']
        out.append(para('同一抓包 SHA-256 已核对。回放识别总伤害 '+p(a['total_damage'],b['total_damage'],m)+'；回放总治疗/恢复量 '+p(a['total_healing'],b['total_healing'],m)+'；承伤事件 '+p(a['incoming_events'],b['incoming_events'],lambda n:f(n,0))+'。回放恢复量不是某一个奶的个人贡献。'))
        for r in players:
            if r['case']==case['case']:out.append(player_block(r))
    out.append('<h2>核对方法、限制与证据</h2>'+para('总分：输出加权贡献＋辅助加权贡献＋特色加权贡献－承伤扣分（含零分下限）。辅助：所有纳入效果的实际收益之和／理论收益之和。输出：先按真正参与评分的技能合计标准伤害，再与爆发项混合。所有 48 人均通过数值复核。'))
    out.append(para('技能分摊采用对称拆解：R=100×A/T；某技能分子变化贡献为 50×ΔA技能×(1/T旧+1/T新)，分母变化贡献为 −50×(A旧+A新)×ΔT技能/(T旧×T新)。每个技能再把伤害=次数×平均单价分成次数项与单价项。这种拆解可以精确相加，但不是唯一的因果归因；没有声称为每个因素重跑了理论轴。'))
    out.append(para('新版最后倍率步骤的正值反推使用实际发布包的 factor_at/state_factor；本地代码与发布包不完全相同，因此未直接以当前源码代替历史版本。反推只用于解释最后一个算术步骤，不等于恢复了所有原始状态。零命中、未闭合模型和审判力量真实性问题不做虚假定论。'))
    out.append(para('未完成的机制根因：尚未对全部 630 项做逐状态开关的反事实重算；个别技能的单次计价残差不能唯一分到某个 Buff 或 Debuff。该报告是完整的计算差异核对与待查定位，不是“全部游戏机制已经证明正确”的验收。','warn'))
    out.append(para('<a href="attribution.json">全部分析数据与校验结果</a> · <a href="../validation.json">两版原始运行校验</a> · <a href="../provenance.json">发布包与抓包溯源</a>'))
    out.append('<pre>'+E(json.dumps(data['validation'],ensure_ascii=False,indent=2))+'</pre>')
    path=DEST/'六场48人逐技能评分升降详解_20261019.html'
    path.write_text(shell('v4.78 → v4.85：六场48人逐技能评分升降详解',''.join(out)),encoding='utf-8')
    assert path.read_text(encoding='utf-8').count('<h3 id=')==48
    print(path)
    print('bytes',path.stat().st_size,'exemption_changes',len(excluded),'uncertain_skill_rows',len(uncertain))

if __name__=='__main__':main()
