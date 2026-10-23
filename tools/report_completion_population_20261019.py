"""Produce a reproducible, read-only audit of uploaded skill completion metrics."""
import base64
from collections import Counter, defaultdict
from html import escape as esc
import json
from pathlib import Path
import statistics as stats
import zlib

from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.scoring import canonical_skill
from dragon_nest_builder.live_dps.score_hit_evidence import repository
from dragon_nest_builder.live_dps.score_v2 import native_reference

ROOT=Path('outputs/verification/all_jobs_completion_20261019')
MECHANICS={
 '十字军':'逐时刻物魔面板及神圣化身→审判力量快照不同；1011已用评分中间量验证。不能把全部残差只归因于审判力量覆盖率。',
 '圣徒':'冲击之手还取决于当时在场圣物数量；雷电之手/奇迹之手需区分一次召唤、完整周期、输出窗口内持续伤害。',
 '圣骑士':'神圣化身等逐时刻面板及有效输出区间影响每次计价；保护技能伤害完成率不是坦克保护完成率。',
 '刀锋舞者':'九天之舞、幻影凝视及团队窗口改变每次伤害价格；热情似火云端理论单次价偏低，排轴时机仍是核查项，不能仅凭Buff解释为正常。',
 '灵魂舞者':'本批旧记录没有上传倍率实现率，无法验证此乘积；须重新评分补传或提供原包。',
 '噬魂者':'灵魂之门走完整召唤周期和存续窗口换算；烙印/幽灵之魂等状态改变单次基准，不能把不同路线当作同价。',
 '黑暗萨满':'幽灵复仇者、自身状态及团队窗口需逐次对齐；样本少，不能把单场偏差当作整个职业定论。',
 '机械大师':'塔/鸭属于持续对象，召唤次数不等于输出周期；补召、覆盖、提前移除和输出窗口会改变单次价。',
 '重炮手':'塔走持续周期结算；飞溅等另有路线、资源和Buff时机差异，须区分召唤与直接攻击。',
 '时空领主':'九尾等连续充能技能要核查释放与Hit证据逐次绑定；部分记录可靠倍率样本远少于评分释放，未知证据回退尤其明显。',
 '黑暗女王':'上升重力→强化黑洞有前置状态，普通/强化不能按同价；体型技能还受逐Boss校准影响。',
 '火舞':'灼烧之手随触发模式独立计价，原生倍率实现率不包含相同的额外伤害收益；火球另有蓄力/三向路线与面板换算。',
 '冰灵':'叠冰、强化和团队Buff时机改变单次价；冰刺/冰尖柱还需同Boss与动作速度校准，暴风雪存在理论/实际窗口差异。',
 '狂战士':'普通旋转与真旋转合并技能名，但6怒气强化单次价不同；应核查形态绑定与6怒气基准，不能仅乘合并次数。',
 '毁灭者':'怒气/强化和加速循环改变单次价；台风异常个案需核查循环参考与Buff，不按同价简化。',
 '剑皇':'勇敢、强化/连招状态及Buff时机改变单次价；日食还涉及物魔面板桥接，需单独闭合。',
 '月之领主':'致命劈砍有多投射物、追加及物魔通道；出现单次异常高价，优先核查Hit绑定、体型上限与通道折算，而不是仅看平均实现率。',
 '狙翎':'包围/箭雨为循环或追加路线；偷袭点0%却有正伤害的记录存在未知样本回退，显示与评分不能解释成同一分母。',
 '魔羽':'追风/集中/迅捷属于体型校准技能；路线、动作速度和Buff时机需同时对齐。',
 '影舞者':'衔接/追加与加速循环改变每次完整路线；整场实现率与评分筛选的释放集合不同。',
 '风行者':'连续技能与派生归属需同口径；精神火焰等次数异常巨大个案优先复核技能归属，不应由Buff解释一切。',
 '银色猎人':'地面/空中/变身/追加路线需要分别比较；当前大偏差样本较少，不足以认定全职业系统性错误。',
 '影':'终结等状态/追加技能每次基准不固定；样本数量很少，先核查路线及逐次价格。',
 '烈':'极度打击等连续释放、动作速度与派生首段影响完整路线；可靠样本与评分释放要对齐。',
 '暗':'幻象攻击EX衍生对象要完整绑定；部分记录只识别了少量倍率却仍按未知回退计价，优先核查证据不足路径。',
 '输出曜':'圆环采用默认平地/动作速度模型，按既定规则不直接用实战原生Hit重定倍率，因此不满足本公式的同分母前提。',
 '打坐曜':'输出技能还受打坐时段、团队窗口及默认模型影响；日光焰的逐次价格与次数口径需单独核查。',
 '炼金圣士':'幻影数量/强化与持续伤害分开计价，原生Hit完成不代表整套伤害完成；疾病超高次数个案需查归属。',
 '输出药剂':'幻影及中毒等状态收益与主体倍率分开；可靠倍率样本少的技能不宜直接乘全部释放次数。',
 '辅助药剂':'主体技能、幻影/持续效果及团队窗口不同价；额外伤害和样本缺失需分开，不与辅助分混淆。',
 '黑暗复仇者':'觉醒/变身与派生路线改变单次参考；部分面板转换在代码中仍标注待闭合，不能据此宣称无算法问题。',
}


def num(v):
    try:return float(v)
    except (TypeError,ValueError):return 0.


def explain(row, ref):
    reasons=[]
    if row['valid_casts'] != row['actual_count']:
        reasons.append(f"样本不同：整场倍率可靠样本{row['valid_casts']}次，评分实际{row['actual_count']}次；既有整场/个人有效区间筛选，也可能有未知、零命中样本，不能直接互乘。")
    if row['unknown_records']:
        reasons.append(f"倍率侧另有{row['unknown_records']}条非可靠/未纳入记录；评分代码对未匹配到可靠逐次证据者回退原有计价，不按0处理。")
    if row['proportional_output']:
        reasons.append(f"持续型：实战时长利用率{100*row['duration_utilization']:.1f}%；次数不能代表完整周期，理论也按其自身窗口截断。V2对获准召唤物先撤销旧时长折损再替换观测倍率，避免重复扣减。")
    if abs(row['rate']-row['native_rate'])>3:
        reasons.append(f"参照不同：Boss校准实现率{row['rate']:.1f}%，完整原生路线实现率{row['native_rate']:.1f}%；要匹配Boss/形态再比较。")
    if ref and ref.get('basis_uncertain'):
        reasons.append('当前模型自身标为待闭合：'+ref['basis_note']+'。这不是已证明正确的解释。')
    elif ref and abs(ref.get('full_factor',1)-1)>.05:
        reasons.append(f"完整面板存在换算桥（新/旧{ref['full_factor']:.3f}）：{ref['basis_note']}；未知回退与可靠样本不能混作同价。")
    if row['rate']<=1e-9 and row['actual']>1:
        reasons.append('重点：显示倍率为0仍有评分伤害，必须核查未匹配回退/归属；不能解释为玩家打出了0倍率却正常伤害。')
    if row['price_factor'] is not None:
        reasons.append(f"总量反算剩余系数K={row['price_factor']:.3f}（次数×显示倍率×K=伤害比）；K混合逐次Buff/状态、权重和回退，并非纯Buff倍率。")
    reasons.append(MECHANICS.get(row['job'],'需核查同Boss、同形态、同有效区间和逐时刻Buff计价。'))
    return reasons


def main():
    inventory=json.loads((ROOT/'inventory.json').read_text(encoding='utf8'))
    records=[r for f in sorted(ROOT.glob('cloud_*.json')) for r in json.loads(f.read_text(encoding='utf8'))]
    assert len({r['id']for r in records})==len(records)
    store=DataStore.load();repo=repository();references={}
    skipped=Counter();audited=[]
    for r in records:
        rates={canonical_skill(z['skill']):z for z in r['realizations']or []}
        total=sum(num(s.get('theory_power')) for s in r['skills']or [] if not s.get('excluded_from_score'))
        for s in r['skills']or []:
            key=canonical_skill(s['skill_name']);z=rates.get(key,{})
            a=num(s.get('actual_count'));t=num(s.get('theory_count'));d=num(s.get('theory_power'))
            if z.get('multiplier_boss_rate') is None:skipped['no_matching_rate']+=1;continue
            if s.get('excluded_from_score') or min(a,t,d)<=0:skipped['excluded_or_zero_denominator']+=1;continue
            rate=float(z['multiplier_boss_rate']);pred=a/t*rate;actual=num(s.get('actual_power'))/d*100
            row=dict(id=r['id'],job=r['job'],nest=r['nest'],date=r['recorded_at'],schema=r['score_schema_version'],skill=key,
                actual_count=int(a),theory_count=int(t),rate=rate,native_rate=num(z.get('multiplier_theory_rate')),
                predicted=pred,actual=actual,display_completion=s.get('completion'),gap=actual-pred,
                valid_casts=int(num(z.get('valid_casts'))),unknown_records=int(num(z.get('unknown_records'))),
                proportional_output=bool(s.get('proportional_output')),duration_utilization=num(s.get('duration_utilization')),
                theory_share=d/total if total else 0,price_factor=actual/pred if pred>1e-9 else None,
                robust=min(a,t)>=5 and (d/total if total else 0)>=.01)
            refkey=(r['job'],key)
            if refkey not in references:
                basejob={'输出曜':'曜','打坐曜':'曜','输出药剂':'药剂师','辅助药剂':'药剂师'}.get(r['job'],r['job'])
                e=next((e for e in store._skill_damage_entries(basejob) if canonical_skill(e['name'])==key),None)
                try:references[refkey]=native_reference(repo,store,basejob,str(e['id']),e['name']) if e else None
                except (KeyError,ValueError,AttributeError):references[refkey]=None
            row['reasons']=explain(row,references[refkey]);audited.append(row)
    groups=defaultdict(list)
    for row in audited:
        if row['robust']:groups[row['job'],row['skill']].append(row)
    summaries=[]
    for (job,skill),rows in sorted(groups.items()):
        bad=[r for r in rows if abs(r['gap'])>=10]
        worst=max(rows,key=lambda r:abs(r['gap']))
        summaries.append(dict(job=job,skill=skill,n=len(rows),large=len(bad),median_gap=stats.median(r['gap']for r in rows),
            worst=worst,reference=references.get((job,skill))))
    counts=dict(cloud_records=sum(int(x['records'])for x in inventory),with_realizations=len(records),
        comparable_labels=len({r['job']for r in audited}),audited_skill_rows=len(audited),
        robust_rows=sum(r['robust']for r in audited),large_robust_rows=sum(r['robust']and abs(r['gap'])>=10 for r in audited),
        skill_groups=len(summaries),large_skill_groups=sum(r['large']>0 for r in summaries),
        systematic_skill_groups=sum(r['n']>=3 and abs(r['median_gap'])>=10 for r in summaries),skipped=dict(skipped))
    result=dict(counts=counts,inventory=inventory,groups=summaries,rows=audited,
        limits='云端统计是记录级而非独立玩家抽样；不同机器重复抓同一场可能重复。仅共同上传两种指标者可计算乘积。当前代码模型审计不等于每条历史记录当时运行版本证明。')
    (ROOT/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    parts=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>全职业技能完成率口径核查</title><style>body{font:15px/1.7 system-ui;color:#223743;background:#f5f8fa;margin:30px auto;max-width:1500px;padding:0 22px}h1,h2{color:#086b73}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{border:1px solid #d9e3e8;padding:8px;text-align:left;vertical-align:top}th{background:#e7f2f2}details{margin:14px 0;padding:12px;background:white;border:1px solid #d9e3e8;border-radius:8px}summary{cursor:pointer;font-weight:600}.warn{background:#fff1d9;padding:14px}input{padding:10px;width:70%;margin:15px 0}.num{white-space:nowrap}.muted{color:#526773}a{color:#057b85}</style><h1>全职业：次数 × 倍率，与伤害完成率为何不同</h1>']
    parts += [f'<p>2026-10-19 只读核查。云端目录 {counts["cloud_records"]:,} 条记录；带整场倍率数据 {len(records):,} 条；可配对职业标签 {counts["comparable_labels"]} 个（包括输出/辅助分型）。</p>',
      '<p>公式有条件成立：同一批释放、同Boss/形态/速度参考、同一单次完整伤害价格、同一权重。严格式为 Σ(每次完整计价×该次实际倍率比例) ÷ Σ(理论每次完整计价)，不是两列百分比直接相乘。</p>',
      '<p class="warn">没有将残差统一认定为正常Buff差异。已确认：样本筛选不同、未知样本回退、持续对象周期计价、逐次状态价格不同。具体技能的回退是否合理、倍率/归属是否错误，未被云端字段充分证明者仍列为待核查。未修改云端历史分数。</p>',
      f'<p>全量可计算技能条目 {len(audited):,}；稳健筛选：实战与理论均≥5次、理论伤害占比≥1%。其中差距≥10个百分点 {counts["large_robust_rows"]} 条、涉及 {counts["large_skill_groups"]} 个职业/技能组合；跨≥3条记录中位差仍≥10点的组合 {counts["systematic_skill_groups"]} 个。百分比采用原始伤害比，不先截断到100%；页面封顶值另列，避免制造假差距。</p>',
      '<input id="search" placeholder="筛选职业或技能，例如：十字军、九尾激光"><p class="muted">展开每个职业可见所有大差距技能；继续展开案例可见逐条依据。正差=伤害比高于乘积，负差=低于乘积。样本是上传记录，不代表独立玩家或独立战斗。</p>']
    local=json.loads(Path('outputs/verification/crusader_completion_20261019/breakdown.json').read_text(encoding='utf8'))
    parts.append('<h2>1011 BlueMoon：九个可对照技能</h2><table><tr><th>技能</th><th>实战/理论</th><th>倍率实现率</th><th>乘积</th><th>伤害比</th><th>差距</th><th>V2修正前单次计价：实际/理论</th></tr>')
    for s in local['skills']:
        pred=s['count_ratio']*s['single_cast_display_rate'];ac=s['actual_damage']/s['theory_damage']*100
        parts.append(f'<tr><td>{esc(s["skill"])}</td><td>{s["actual_count"]}/{s["theory_count"]}</td><td>{s["single_cast_display_rate"]:.1f}%</td><td>{pred:.1f}%</td><td>{ac:.1f}%</td><td>{ac-pred:+.1f}点</td><td>{s["before_v2_per_cast_vs_theory"]*100:.1f}%</td></tr>')
    parts.append('</table><p>这一列来自本机完整重算的中间量，已验证代数闭合：次数比×V2修正前单次计价比×V2净倍率系数=伤害比。说明主要残差在Hit修正之前已存在，不是第二次乘了同一倍率实现率。计价比还包含Buff、状态、面板和时机，不能全部叫作“审判力量漏放”。全队输出609.089秒；BlueMoon个人573.430秒。上传已改为前者，个人评分窗口不变。</p>')
    focus={
        '33434':('九尾激光','可靠倍率15次，评分实际49次。时间轴同时存在约0.13M与3.09M的实战单次价格，吻合一部分按低实现率结算、另一部分走未知回退的表现。应先统一逐次证据集合，不能用15次的6%推算全部49次；每次回退是否合法仍需原始逐Hit账本。'),
        '33824':('偷袭点','21条实战时间轴中15条为零、6条仍有伤害，评分次数16。可见不是所有释放都在乘显示的0%。当前未知证据回退确实可能保留旧模型价格，哪些6次为何未绑定必须继续查原包，不可作为正常误差略过。'),
        '34310':('幻象攻击','实战时间轴33条、32条正伤害；倍率侧可靠20次。衍生部分未绑定或仅取主体段均可能造成低显示倍率与较高评分并存。数据已证实集合/计价不一致，具体衍生对象漏在哪一环尚不能由云端汇总定论。'),
        '34398':('热情似火','7次实战合计156.67M，10次理论合计141.38M；理论前几次仅5.08–10.42M，实战前几次20.99–31.16M。单次价差确实存在，且实战首发在40%加速窗口、理论首发无加速；但仅此不能定量证明全部差距，仍需核查九天/幻影/团队Buff排轴和倍率规则。'),
        '34345':('致命劈砍','实际和理论均12次，却有单次实战46.658秒约21.96M，而附近普通实战约1–2M，整套理论约30.61M。优先检查该次Hit归属、Boss校准、物魔通道以及追加路线；暂不能用一般Buff解释排除错误。'),
        '33851':('冲击之手','10次实战37.60M对20次理论202.73M。源码明确按施放时在场圣物数定价，理论允许不同圣物数量；次数×固定3Hit参考完成率不自动等于理论多圣物伤害比。仍需原包核实圣物存续数量。'),
        '34338':('灵魂之门','10次实战188.57M对13次理论264.13M。同为一次门，单次伤害4.90M到35.37M、理论6.56M到37.24M；持续区间和状态不同价，不能把10/13直接当完整周期比。烙印和持续窗口须进一步逐次核对。'),
        '34053':('重磅鸭','9条实战中仅5条正伤害，实战6.98M、理论7.30M；召唤次数与实际承担输出的存续周期不是同一计数。应与塔/鸭周期账本对齐，不将每次补召视为一个完整输出周期。'),
        '33766':('火球','实战25次72.30M，理论36次205.07M；初次实战0.77M、理论5.70M。火球充能/三向路线、原生完整参考与灼烧之手额外伤害需要分开，还存在新旧面板换算因子0.7667；云端不足以判定各因子的全部贡献。'),
        '33690':('火焰风暴','实战6次170.35M超过理论7次143.62M；理论前几次10.85–15.01M，而实战前几次23.58–39.57M。提示理论时机或状态计价偏弱，不是少一次施放就必然伤害低。'),
        '33906':('护卫者姿势','7次实战11.08M对10次理论18.81M；逐次价格实战1.03–3.43M，理论存在4.58M的一次。自身状态及团队Buff计价不同；这里分析的是伤害完成率，不是蛋壳覆盖完成率。'),
        '34005':('旋转冲击','评分27次实际、34次理论，而显示倍率仅1次可靠样本。普通/真旋转6怒气状态合并后的全场次数，不能乘这1次形态的实现率。形态覆盖率与旧包/新规则映射应优先核实。'),
    }
    timeline_evidence=[]
    for rec in json.loads((ROOT/'representative_timelines.json').read_text(encoding='utf8')):
        if str(rec['id']) not in focus or not (rec.get('timeline')or {}).get('p'):continue
        skill,note=focus[str(rec['id'])]
        context=json.loads(zlib.decompress(base64.b64decode(rec['timeline']['p'])))
        lanes={key:[x for x in context['detail'][key] if x.get('skill_name')==skill]
            for key in ('actual_timeline','theory_timeline')}
        timeline_evidence.append(dict(id=rec['id'],job=rec['job'],skill=skill,note=note,
            **{key:dict(count=len(xs),positive_count=sum(x.get('power',0)>0 for x in xs),
                       total_power=sum(x.get('power',0)for x in xs),casts=xs)for key,xs in lanes.items()}))
    (ROOT/'representative_analysis.json').write_text(json.dumps(timeline_evidence,ensure_ascii=False,indent=2),encoding='utf8')
    parts.append('<h2>12条重点记录：进一步查看已上传的逐次时间轴</h2><table><tr><th>记录/职业/技能</th><th>进一步查到的事实与边界</th></tr>')
    for e in timeline_evidence:
        parts.append(f'<tr><td>#{e["id"]} {esc(e["job"])}<br>{esc(e["skill"])}</td><td>{esc(e["note"])}</td></tr>')
    parts.append('</table>')
    for inv in inventory:
        job=inv['job'];gs=[g for g in summaries if g['job']==job and g['large']]
        parts.append(f'<details class="job"><summary>{esc(job)} · 云端{inv["records"]}条 · 有倍率{inv["with_realizations"]}条 · 大差距技能{len(gs)}个</summary>')
        parts.append('<p>'+esc(MECHANICS.get(job,'按整场倍率与评分技能名称归一化对齐；未上传倍率的数据不能倒推出倍率实现率。'))+'</p>')
        if not gs:
            parts.append('<p>'+('没有可配对倍率字段，不能判断正常或异常。' if int(inv['with_realizations'])==0 else '本次筛选未发现≥10个百分点的大差距技能，或样本不足5次/低于1%伤害占比；不等于算法已经全部证明正确。')+'</p></details>');continue
        parts.append('<table><tr><th>技能</th><th>可比记录/大差距</th><th>中位差</th><th>最大差案例</th><th>乘积→伤害比</th><th>逐项依据与待核查</th></tr>')
        for g in sorted(gs,key=lambda g:(abs(g['median_gap']),g['large']),reverse=True):
            w=g['worst'];case_rows=[r for r in groups[job,g['skill']] if abs(r['gap'])>=10]
            reasons='<br>'.join(esc(x)for x in w['reasons'])
            cases='<details><summary>全部大差距记录</summary><table><tr><th>ID / 巢穴</th><th>次数</th><th>倍率</th><th>乘积/伤害比/页面</th><th>差距</th></tr>'
            for c in sorted(case_rows,key=lambda r:abs(r['gap']),reverse=True):
                cases+=f'<tr><td>{esc(str(c["id"]))} {esc(c["nest"])}<br>{esc(c["date"])}</td><td>{c["actual_count"]}/{c["theory_count"]}</td><td>{c["rate"]:.1f}%</td><td>{c["predicted"]:.1f}% / {c["actual"]:.1f}% / {num(c["display_completion"]):.1f}%</td><td>{c["gap"]:+.1f}点</td></tr>'
            cases+='</table></details>'
            parts.append(f'<tr><td>{esc(g["skill"])}</td><td>{g["n"]}/{g["large"]}</td><td>{g["median_gap"]:+.1f}点</td><td>#{w["id"]}<br>{esc(w["nest"])}</td><td class="num">{w["predicted"]:.1f}% → {w["actual"]:.1f}%<br>{w["gap"]:+.1f}点</td><td>{reasons}{cases}</td></tr>')
        parts.append('</table></details>')
    parts.append('<h2>证据与边界</h2><p>本目录 analysis.json 保留每条可比较技能的数值和分析依据；cloud_*.json 为只读查询结果；representative_timelines.json 为12条重点记录的时间轴复核。源代码核查：skill_realization.py 只平均可靠且命中Boss的释放；score_v2.py value_actual 对未知证据回退，召唤物撤销旧时长折损再代入观测倍率；skill_timeline_output.py 逐时刻面板、圣物数量、灼烧之手改变计价。公开汇总不包含角色身份。</p><p>灵魂舞者等旧记录缺倍率字段：不能虚构其差距。当前模型中标注“面板口径待闭合”的项目不算已核实。云端数据无法独立判断原包是否丢失、每次Hit是否归属正确，也不能确定历史包运行的是同一构建。</p><script>document.getElementById("search").addEventListener("input",function(){let q=this.value.trim().toLowerCase();document.querySelectorAll("details.job").forEach(d=>{d.hidden=q&&!d.textContent.toLowerCase().includes(q);if(q&&!d.hidden)d.open=true;});});</script></html>')
    (ROOT/'全职业次数倍率与伤害完成率核查_20261019.html').write_text(''.join(parts),encoding='utf8')
    print(json.dumps(counts,ensure_ascii=False))


if __name__=='__main__':main()
