"""Build the human-readable report from the final measured outputs."""
from pathlib import Path
import json, html, hashlib
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/verification/hit_matching_fix_20261020'
def esc(x):return html.escape(str(x))
def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(x)+'</th>'for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(x)+'</td>'for x in row)+'</tr>'for row in rows)+'</tbody></table></div>'
def pct(x):return f'{x:.1f}%'
def main():
    a=json.loads((OUT/'analysis.json').read_text('utf8'))
    act=json.loads((OUT/'entrypoint_act.json').read_text('utf8'))
    standalone=json.loads((OUT/'entrypoint_standalone.json').read_text('utf8'))
    assert act['sha256']==standalone['sha256']
    assert json.loads((OUT/'targeted_gui.json').read_text('utf8'))['ok']
    assert json.loads((OUT/'gui_smoke.json').read_text('utf8'))['ok']
    timings=a['timing'];old=timings['before']['median'];fixed=timings['corrected']['median'];fast=timings['fast']['median']
    improvement=sum(x['count']for x in a['improved_over_100ms'])
    p=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Hit 配对修复与无损加速验收 · 20261020</title><style>
    body{font:16px/1.7 "Microsoft YaHei",sans-serif;color:#243f48;background:#f3f7f8;margin:0;padding:28px}
    main{max-width:1200px;margin:auto;background:white;padding:30px;border-radius:12px}
    h1,h2{color:#007c86}table{border-collapse:collapse;width:100%;margin:15px 0}td,th{border:1px solid #d8e4e7;padding:10px;text-align:left}th{background:#eaf3f4}
    .scroll{overflow:auto}.note{padding:16px;background:#fff6df;border-left:4px solid #ba8422}details{margin:18px 0}summary{cursor:pointer;color:#007c86;font-weight:bold}
    code{background:#edf3f4;padding:2px 5px}small{color:#52656d}</style><main>
    <h1>Hit 配对修复与无损加速验收</h1><p>样本：1019噬魂竞速第十七把.dpscap。原包只读，未上传、未发布安装包。</p>''']
    p.append(f'<p>覆盖 {a["jobs"]} 个职业、{a["skills"]} 个技能、{a["casts"]} 次有命中的施放。修正后的基础实现与优化实现逐字段一致；{act["casts"]} 条批量统计记录在 ACT 并行入口与附带工具独立运行环境中一致。</p>')
    p.append('<h2>一、修了什么</h2><ol><li>取消伤害拟合后的全局顺序强排。保留时间与伤害联合选择的一对一对应；同倍率、同时间窗可整理顺序；完全同代价的平局只在不改变每个实际 Hit 倍率、已匹配 Hit 集合及槽位集合的前提下靠近合理时间。</li><li>火焰风暴的灼烧之手关联追加单独识别。要求明确来源 2215、同角色、同目标、同次施放、唯一前后 Hit，允许已观察到的跨包延迟；重复证据去重，竞争候选不强配。追加不填普通段缺失，2301 自身伤害不挪入 2215。</li><li>时间误差、倍率拟合误差按最终显示的对应重算；不再沿用已被替换的初始匹配的小误差。显示统计与既有形态选择分开。</li><li>灵魂之门、灵魂之纸继续执行你刚确认的“有烙印完整路线”标准。没有烙印或漏掉的伤害不会自动降低分母；这不是伪造已收到烙印。</li></ol>')
    p.append('<h2>二、截图里的火焰风暴</h2>')
    rows=[]
    for r in a['firestorm']:
        b,c=r['before'],r['after'];rb,rc=b['realization'],c['realization']
        rows.append([f'{c["time"]/1000:.3f}s',f'{b["procs"]} → {c["procs"]}',
                     f'{b["summary"]["missing"]} → {c["summary"]["missing"]}',
                     f'{pct(rb["hit_rate_percent"])} → {pct(rc["hit_rate_percent"])}',
                     f'{pct(rb["raw_multiplier_rate_percent"])} → {pct(rc["raw_multiplier_rate_percent"])}',
                     f'{b["window_distance_ms"]:.1f} → {c["window_distance_ms"]:.1f} ms'])
    p.append(table(['施放时间','明确追加 Hit','主体缺失','主体 Hit 实现率','原始倍率实现率','普通段平均距时间窗'],rows))
    p.append('<p>111.233s：17 个实际 Hit 分为 11 个普通段＋6 个追加段。完整普通段 1.00 倍，追加 6×0.08＝0.48 倍，因此原始倍率实现率是 148%。旧逻辑只认出 3 个追加，且把早期普通段挤成了“额外”。</p><p>255.941s：17 个实际 Hit 同样分为 11 个普通段＋6 个追加段，但加速下完整普通路线要求 15 Hit／1.32 倍。因此主体是 11/15＝73.3%；已打倍率 1.48/1.32＝112.1%。这不是矛盾：主体有缺失，追加带来额外收益，两者分开显示。这里缺失由 3 变 4，是把以前占了主体槽位的追加还原出来，不是删除了一个真实伤害包。</p>')
    p.append('<h2>三、灵魂之门的统一基准</h2>')
    p.append(table(['施放时间','实际匹配倍率','有烙印标准倍率','倍率实现率'],
        [[f'{r["time"]/1000:.3f}s',f'{r["realization"]["realized_weight"]:.2f}',f'{r["realization"]["expected_weight"]:.2f}',pct(r['realization']['raw_multiplier_rate_percent'])]for r in a['brand']]))
    p.append('<p>无烙印时伤害少，不再挑选较低分母让它看起来完成；缺失是否确实由操作造成，还需完整状态记录支持。计算标准和“是否观察到烙印状态”保持分离。</p>')
    p.append('<h2>四、其他技能与剩余边界</h2>')
    p.append(f'<p>{improvement} 次施放的普通段平均距理论时间窗缩短超过 100ms，涉及 {len(a["improved_over_100ms"])} 个职业—技能组合。距离更短仅说明时序对应改善，不能独立证明每一段倍率已经正确。</p>')
    p.append(table(['职业','技能','改善超过100ms的施放数'],[[r['job'],r['skill'],r['count']]for r in sorted(a['improved_over_100ms'],key=lambda r:-r['count'])]))
    p.append(f'<div class="note">没有宣称全技能全部核准。本次有 {len(a["weight_changes"])} 次施放的已匹配倍率总和变化，须与时序改善分开看；另有 {len(a["worsened_over_100ms"])} 次平均距窗反而增大。原生技能 ID 不等于已知命中段 ID，移动弹体、召唤物出生锚点、缺失子对象信息仍可能使理论时间不准确。不能仅靠把曲线对齐或把完成率抬高判断修复成功。</div>')
    p.append('<details><summary>展开：倍率总和改变的全部施放</summary>')
    p.append(table(['职业','技能','施放秒','修复前倍率','修复后倍率'],[[r['before']['job'],r['before']['skill'],f'{r["before"]["time"]/1000:.3f}',f'{r["before"]["realization"]["realized_weight"]:.4f}',f'{r["after"]["realization"]["realized_weight"]:.4f}']for r in a['weight_changes']]))
    p.append('</details><details><summary>展开：距窗增大的待核项</summary>')
    p.append(table(['职业','技能','施放秒','之前距窗(ms)','现在距窗(ms)'],[[r['before']['job'],r['before']['skill'],f'{r["before"]["time"]/1000:.3f}',f'{r["before"]["window_distance_ms"]:.1f}',f'{r["after"]["window_distance_ms"]:.1f}']for r in a['worsened_over_100ms']]))
    p.append('</details><h2>五、匹配耗时：同一输入、独立进程、交错三轮</h2>')
    p.append(table(['实现','第1轮','第2轮','第3轮','中位数'],[[name,*[f'{v:.2f}s'for v in timings[mode]['runs']],f'{timings[mode]["median"]:.2f}s']for mode,name in [('before','冻结旧实现'),('corrected','修正逻辑，关闭纯加速'),('fast','修正逻辑，启用纯加速')]]))
    p.append(f'<p>相对修正基础实现减少 <b>{100*(1-fast/fixed):.1f}%</b>；相对冻结旧实现减少 <b>{100*(1-fast/old):.1f}%</b>。只统计形态生成和逐 Hit 配对，不含原包读取解码、全队理论轴评分、界面绘制。不能据此声称完整评分也缩短同样比例。</p>')
    p.append('<p>加速只复用单次拟合内部完全相同的代价矩阵解，并合并重复倍率误差计算；不舍弃 Hit、不减少候选、不做近似键、不跨施放借用结果。三轮修正基础版与三轮优化版的完整结果哈希一致。</p>')
    p.append('<h2>六、入口与验证</h2>')
    p.append(table(['入口','统计记录','核对结果','运行环境'],[['ACT 并行入口',act['casts'],'完整结果与独立工具一致',act['python']],['附带工具',standalone['casts'],'独立隔离进程，结果一致',standalone['python']]]))
    p.append('<p>专门回归：59 项通过、5 个子测试通过；覆盖平局、时间顺序、跨包追加、竞争候选拒绝、全烙印基准和优化开关等价。附带工具的隔离运行环境已通过4次真实施放界面渲染及通用界面冒烟验收。通用自检原先只允许提示含“推测”，现同时允许语义一致的“估计”，仍要求低证据样例不得变成可确定结论。</p><p>扩大测试曾有4项旧的“雷电之手/塔完全不进入Hit计价”断言失败，原因是当前召唤物计价实现会读取实际技能库，而该测试传入空库；本次没有修改这部分策略，也未将它们算作通过。此次入口对照复用了已解码的原包证据，未重复执行完整抓包解码；它验证的是两个消费入口处理同一证据时的结果一致性。</p>')
    p.append('<p>源代码已更新共用模块，旧缓存通过组件指纹和内部规则版本失效。没有重新发布安装包；已打开的工具需要重启并重新分析，已有导出结果不会原地自动改写。</p>')
    p.append('<h2>可追溯结果</h2><p><a href="analysis.json">全部对照数据</a> · <a href="before_1.json">冻结旧实现逐Hit输出</a> · <a href="fast_1.json">最终实现逐Hit输出</a> · <a href="entrypoint_act.json">ACT入口</a> · <a href="entrypoint_standalone.json">独立工具入口</a></p>')
    p.append('<small>2026-10-20。次数与倍率变化是本次配对修正的效果；“纯加速不改变结果”指修正后基础实现与优化实现完全一致，不是要求错误旧实现与修复后一致。</small></main></html>')
    target=OUT/'Hit匹配修复与耗时对照_20261020.html'
    target.write_text(''.join(p),'utf8')
    provenance={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest()for f in [
        ROOT/'third_party/skill_hit_tool/app/act_hit_timeline_comparator/core.py',
        ROOT/'third_party/skill_hit_tool/app/act_hit_timeline_comparator/stage_estimator.py',
        ROOT/'third_party/skill_hit_tool/app/skill_hit_tool/confirmed_components.py',
        ROOT/'dragon_nest_builder/live_dps/score_v2.py']}
    (OUT/'source_fingerprints.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),'utf8')
    print(target)
if __name__=='__main__':main()
