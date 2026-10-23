"""Read-only triage: exported scores plus current speed-reference consistency."""
import json
import math
from collections import defaultdict
from html import escape
from pathlib import Path

from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.score_hit_evidence import repository
from dragon_nest_builder.live_dps.score_v2 import native_reference, native_speed_reference_enabled
from dragon_nest_builder.live_dps.scoring import _action_speed_output_multiplier

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / 'outputs/verification'
OUT = VERIFY / 'skill_price_risks_20261019'
OUT.mkdir(exist_ok=True)
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

ab = read(VERIFY/'all_jobs_published_ab_20261019/detailed_attribution/attribution.json')
cloud = read(VERIFY/'all_jobs_completion_20261019/analysis.json')
reps = read(VERIFY/'all_jobs_completion_20261019/representative_analysis.json')
experiment = read(VERIFY/'scoring_optimization_20261019/accuracy_reference/native_speed_experiment.json')
store, repo = DataStore.load(), repository()
pairs = sorted({(r['job'], r['skill']) for r in experiment['theory_cast_changes']})
speed_rows = []
for job, skill in pairs:
    example = next(s for p in ab['players'] if p['job'] == job for s in p['skills'] if s['skill'] == skill and s.get('rule'))
    sid = str(example['rule']['skill_id'])
    ref = native_reference(repo, store, job, sid, skill)
    variants = repo.variants_for(job, sid, skill, 40.)
    variant = next(v for v in variants if v.form == ref['reference_form'])
    hits = [h for h in variant.hits if not h.optional]
    native = math.fsum(h.weight for h in hits) / ref['native_weight']
    nominal = _action_speed_output_multiplier(job, skill, 40.)
    affected = [r for r in experiment['theory_cast_changes'] if (r['job'], r['skill']) == (job, skill)]
    speed_rows.append(dict(job=job, skill=skill, skill_id=sid,
        speed=40., theory_default_factor=nominal, native_complete_factor=native,
        relative_full_price=native/nominal*100, reference_hits_zero=ref['reference_hits'],
        reference_hits_40=len(hits), samples=sorted({r['case'] for r in affected}),
        affected_theory_casts=len(affected), classification='当前默认规则与逐Hit完整参考不一致；非游戏真值的独立证明',
        experiment_enabled=native_speed_reference_enabled()))

# Screen growth only AFTER removing counts and reversing the final V2 step.
# This inverse is a diagnostic, not an observed counterfactual/recomputed score.
ab_rows = []
for player in ab['players']:
    total = sum((s.get('new') or {}).get('theory_power', 0) for s in player['skills'])
    for s in player['skills']:
        old, new, v2 = s.get('old') or {}, s.get('new') or {}, s.get('v2_final_step') or {}
        pre = v2.get('recovered_input', 0)
        if min(old.get('actual_count', 0), old.get('theory_count', 0), old.get('actual_power', 0),
               old.get('theory_power', 0), new.get('actual_count', 0), new.get('theory_count', 0), pre) <= 0:
            continue
        share = new['theory_power']/total if total else 0
        if min(new['actual_count'], new['theory_count']) < 5 or share < .03 or v2.get('noninvertible_zero_rows', 0):
            continue
        a = (pre/new['actual_count'])/(old['actual_power']/old['actual_count'])
        t = (new['theory_power']/new['theory_count'])/(old['theory_power']/old['theory_count'])
        ratio = a/t
        if .75 <= ratio <= 1.33:
            continue
        ab_rows.append(dict(job=player['job'], skill=s['skill'], case=player['case'], capture=player['capture'],
            actual_pre_v2_mean_growth=a, theory_mean_growth=t, relative_growth=ratio,
            theory_share=share, proportional_output=new.get('proportional_output'),
            actual_before=old['actual_count'], actual_after=new['actual_count'],
            theory_before=old['theory_count'], theory_after=new['theory_count'],
            status='增长异常筛选，不等于已确认Bug；逆推还受持续时间模型变化影响'))
ab_rows.sort(key=lambda r: abs(r['relative_growth']-1)*r['theory_share'], reverse=True)

pending = []
reasons = {
 '九尾激光': '可靠倍率15次，评分49次；未知释放回退与已匹配释放混合，不能用6%左右的倍率代表全部49次。',
 '偷袭点': '显示倍率0%，但21条时间轴中仍有6条正伤害；先核对未匹配回退，不能当作已打满或简单清零。',
 '致命劈砍': '12次对12次；单发21.96M，附近通常1–2M。须核对该发对象、追加与物魔通道，不先归因于Buff。',
 '热情似火': '7次实战156.67M超过10次理论141.38M；理论与实战单次价格明显不同，需完整重放九天/幻影及加速窗口。',
 '火焰风暴': '6次实战170.35M超过7次理论143.62M；理论单次10.85–15.01M、实际23.58–39.57M，需确认状态与排轴。',
 '火球': '25次实际/36次理论，72.30M/205.07M；充能、三向、灼烧之手和面板换算混合，未证明为快照Bug。',
 '旋转冲击': '评分27次，可靠倍率仅1次；普通与真旋转的形态、6怒气条件不能共用一个实现率。',
 '幻象攻击': '评分约32次正伤害，可靠倍率20次；需要核对衍生对象与未知回退。',
 '冲击之手': '10次实战37.60M/20次理论202.73M；每次在场圣物数量会改变价格，不能按固定单次参考直接判断错误。',
 '灵魂之门': '一次召唤不等于完整周期；需核对烙印和持续窗口，当前不是同类快照错误的证据。',
 '重磅鸭': '补召与存续周期不是同一计数；9条实战只有5条正伤害，需要周期账本，不宜直接乘次数。',
}
for rep in reps:
    if rep['skill'] not in reasons:
        continue
    row = next((r for r in cloud['rows'] if str(r['id']) == str(rep['id']) and r['skill'] == rep['skill']), None)
    pending.append(dict(id=rep['id'], job=rep['job'], skill=rep['skill'], note=reasons[rep['skill']],
                        status='集合/计价异常或特殊机制，尚未证明算法错误', metrics=row))

summary = dict(scope=dict(captures=len(ab['scope']['cases']), player_records=len(ab['players']),
                          jobs=len({p['job'] for p in ab['players']}), cloud_records_with_realizations=cloud['counts']['with_realizations']),
               current_speed_checks=speed_rows, growth_screen=ab_rows, pending=pending,
               other_speed_risks=[g for g in ab['speed_reference_gap_groups'] if (g['job'],g['skill']) not in pairs],
               limits=['复用已完成的双版本评分及2026-10-19云端快照，本次未重跑全库评分或上传数据。',
                       '当前源代码重新核对六类加速计价；一致性冲突确认不代表已独立证实游戏中Hit上限。',
                       '大差距本身不能区分算法错误、Buff时机、技能形态和样本集合不同。'])
(OUT/'analysis.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
def table(headers, rows):
    return '<table><tr>'+''.join('<th>'+escape(str(h))+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'

parts=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>全职业技能计价比例异常清单</title><style>body{font:16px/1.7 system-ui;max-width:1300px;margin:32px auto;padding:0 24px;color:#213c43}table{width:100%;border-collapse:collapse}th,td{border:1px solid #cedbdd;padding:9px;vertical-align:top}th{background:#e8f3f2}h2{margin-top:32px}.note{background:#fff4d9;padding:15px}</style>',
 '<h1>全职业技能计价比例异常清单 · 2026-10-19</h1>',
 '<p>范围：已有12份双版本评分、96个角色记录、30职业；另复核云端快照中1286条带实现率记录。本次不是全库重新评分。表内严格区分已确认规则冲突、异常筛选和合理机制。</p>',
 '<p class="note">十字军的对象引用／快照问题已单独修复。下列技能不能仅因比例异常，就认定存在相同Bug。加速规则冲突仍为默认代码与原生路线的一致性问题，需统一规则并验证游戏机制，不在本次直接改分。</p>',
 '<h2>一、当前代码复核：完整命中仍可能两侧不同价</h2><p>固定40%动作速度，排除其他Buff/状态差异。百分比表示完整原生路线相对理论加速计价，不是整场输出分。</p>',
 table(['职业','技能','理论加速系数','逐Hit完整系数','相对计价','0% / 40%参考Hit','既有实验涉及样本数'],
       [[r['job'],r['skill'],f"{r['theory_default_factor']:.4f}",f"{r['native_complete_factor']:.4f}",f"{r['relative_full_price']:.1f}%",f"{r['reference_hits_zero']} / {r['reference_hits_40']}",len(r['samples'])] for r in speed_rows]),
 '<p>对应共享原生参考修正仍由实验开关控制，默认未启用。另：毁灭者/狂战士旋风、狙翎包围、暗圆环强击存在参考/时钟边界异常，不能直接套用上述修正。</p>',
 '<h2>二、其他优先核查项</h2>',table(['职业','技能','云端记录ID','证据与结论边界'],[[r['job'],r['skill'],r['id'],r['note']] for r in pending]),
 '<h2>三、去掉次数和最后倍率修正后的跨版本增长异常</h2><p>实战列为逆推倍率修正前的平均价格增长，非新跑出的实战反事实。持续技能在V2取消旧时长折损后也可能显著增大，不能据此直接认定错误。仅保留至少5次、理论伤害占比≥3%的技能。</p>',
 table(['职业','技能','抓包','实战单次价格增长','理论单次价格增长','两者之比','持续型'],[[r['job'],r['skill'],r['capture'],f"{r['actual_pre_v2_mean_growth']:.3f}倍",f"{r['theory_mean_growth']:.3f}倍",f"{r['relative_growth']:.3f}",r['proportional_output']]for r in ab_rows]),
 '<h2>结论</h2><p>优先核查六类加速参考冲突，以及九尾激光、偷袭点、致命劈砍、热情似火、火焰风暴、极度打击。螺旋踢/猛砍站姿的跨版本增长必须先剥离V1时长折损与V2 Hit折算；冲击之手/塔鸭/灵魂之门必须先按对象或周期对齐，不能强行让所有技能满足“次数比×整场平均倍率”。</p>',
 '<p>原始来源：all_jobs_published_ab_20261019/detailed_attribution/attribution.json、all_jobs_completion_20261019/analysis.json、representative_analysis.json、scoring_optimization_20261019/accuracy_reference/native_speed_experiment.json。当前代码核对：score_v2.py 与完整Hit路线资源。</p></html>']
path=OUT/'全职业技能计价比例异常清单_20261019.html'
path.write_text(''.join(parts),encoding='utf-8')
print(path)
print(json.dumps(speed_rows,ensure_ascii=False,indent=2))
print('growth_screen',len(ab_rows),'pending',len(pending))
