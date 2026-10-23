"""Summarize the offline frozen-v4.85 controlled replay; never upload results."""
import json
from collections import Counter, defaultdict
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/verification/bluemoon_price_cause_20261019'
before = json.loads((OUT / 'trace_4.85.json').read_text(encoding='utf-8'))
after = json.loads((OUT / 'trace_4.85_source_fix.json').read_text(encoding='utf-8'))
published = json.loads((ROOT / 'outputs/verification/published_478_485_20261019/black_1011_v4.85.json').read_text(encoding='utf-8'))
player = next(r for r in published['scoreRowsDetailed'] if r['name'] == 'BlueMoon')
assert before['result']['_score_actual_cast_segments'] == after['result']['_score_actual_cast_segments']

def totals(result):
    damage, counts = defaultdict(float), Counter()
    for row in result['segments']:
        value = row.get('_score_actual_damage', 0.)
        if value > 0:
            damage[row['skill']] += value
            counts[row['skill']] += 1
    return damage, counts

old, old_counts = totals(before['result'])
new, new_counts = totals(after['result'])
rows = []
for item in player['skill_details']:
    skill = item['skill_name']
    if skill not in old or skill not in new:
        continue
    # This engine run excludes damage glyphs during planning, as production
    # does. Reapply the three explicitly equipped +20% glyphs at the end.
    factor = 1.2 if skill in {'重击', '神圣十字', '连锁闪电'} else 1.
    baseline = old[skill] * factor
    corrected = new[skill] * factor
    assert abs(baseline - item['theory_power']) < 1e-4
    rows.append(dict(skill=skill, before_count=old_counts[skill], after_count=new_counts[skill],
                     before_theory=baseline, after_theory=corrected,
                     unchanged_actual=item['actual_power'],
                     before_completion=100 * item['actual_power'] / baseline,
                     after_completion=100 * item['actual_power'] / corrected))
smite = next(r for r in rows if r['skill'] == '重击')
assert abs(smite['after_theory'] - 609731205.4660585) < 1e-4
summary = dict(capture='1011硬核bluemoon十字', player='BlueMoon',
               method='Frozen v4.85 engine; replace only manual replay methods with production fix; no network access',
               actual_cast_segments_identical=True,
               baseline_seconds=before['seconds'], corrected_seconds=after['seconds'],
               note='One-axis diagnostic, not a full-team score. Timing can change when corrected candidates are selected.',
               skills=rows)
(OUT / 'candidate_snapshot_fix_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
table = ''.join('<tr><td>'+escape(r['skill'])+'</td><td>'+str(r['before_count'])+' → '+str(r['after_count'])+
                '</td><td>'+f"{r['before_theory']/1e6:.2f} → {r['after_theory']/1e6:.2f}"+
                '</td><td>'+f"{r['unchanged_actual']/1e6:.2f}"+'</td><td>'+
                f"{r['before_completion']:.1f}% → {r['after_completion']:.1f}%"+'</td></tr>' for r in rows)
html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>理论候选轴快照修复验证</title>
<style>body{max-width:1050px;margin:40px auto;padding:0 24px;font:17px/1.7 system-ui;color:#203b42}table{border-collapse:collapse;width:100%}th,td{border:1px solid #cedce0;padding:9px;text-align:left}th{background:#edf5f5}small{color:#50656b}</style>
<h1>理论候选轴：审判力量快照修复验证</h1>
<p>样本：1011硬核bluemoon十字，BlueMoon。冻结的 v4.85 引擎先精确复现原理论伤害，再仅替换本次正式修复的手动／候选重放方法。未改动云端或安装包。</p>
<h2>根因与修复</h2><p>公共技能释放逻辑原本会更新快照，但动态轴／稳定轴比较及界面恢复时，辅助动作和周期 Buff 表可能分别深拷贝。更新和计价读取不同对象，导致候选使用上一轮强化倍率。</p>
<p>每条重放独立复制状态；按技能和 Buff 标记重新绑定对象；重置运行期快照；沿用公共释放逻辑逐次更新；通过 finally 在成功、拒绝、异常及嵌套返回时恢复外层状态。不是直接削减理论伤害，也没有强行把理论与实战拉齐。</p>
<h2>结果</h2><p>实战逐次记录完全相同。下表伤害单位为百万，已恢复原评分的伤害纹章。完成率为单技能实际／理论标准伤害，不是整场输出分。</p>
<table><tr><th>技能</th><th>理论次数</th><th>理论标准伤害：前 → 后</th><th>实战标准伤害（不变）</th><th>技能伤害完成率：前 → 后</th></tr>'''+table+'''</table>
<h2>范围与限制</h2><p>已验证候选顺序不影响计价、化身结束后保留原审判快照、重新施放审判会覆盖快照、无审判不继承加成、异常／拒绝／嵌套候选恢复状态。</p>
<p>本次没有修复或推断抓包中缺失的审判 Buff，也未重新发布安装包、上传或覆盖云端成绩。尚不能把剩余理论与实战差距全部判为玩家操作差距。</p>
<p>专项回归 48 项通过；扩展技能测试 25 项通过，7 个既有倍率子断言失败，禁用本次修复后同样失败，未修改无关技能数据或旧断言。</p>
<small>原始证据：trace_4.85.json、trace_4.85_source_fix.json；机器可读汇总：candidate_snapshot_fix_summary.json。</small></html>'''
path = OUT / '理论候选轴审判快照修复验证_20261019.html'
path.write_text(html, encoding='utf-8')
print(path)
print(json.dumps(summary, ensure_ascii=False, indent=2))
