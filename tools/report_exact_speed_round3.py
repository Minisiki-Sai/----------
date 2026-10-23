"""Third-round report: measured timings and unrounded comparison results."""
import argparse
import html
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--comparison', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--before', type=Path, required=True)
    p.add_argument('--after', type=Path, required=True)
    p.add_argument('--tests', type=int, required=True)
    p.add_argument('--initial-comparison', type=Path)
    p.add_argument('--initial-before', type=Path)
    p.add_argument('--axis-before', type=Path)
    p.add_argument('--axis-after', type=Path)
    args = p.parse_args()
    comparison = json.loads(args.comparison.read_text('utf8'))
    before = json.loads(args.before.read_text('utf8'))
    after = json.loads(args.after.read_text('utf8'))
    timing = comparison['timings']
    gain = timing['score_seconds']['time_reduction_percent']
    rows = ''.join(f'<tr><td>{label}</td><td>{timing[key]["before"]:.2f} 秒</td>'
                   f'<td>{timing[key]["after"]:.2f} 秒</td><td>{timing[key]["seconds_saved"]:.2f} 秒</td>'
                   f'<td>{timing[key]["time_reduction_percent"]:.2f}%</td></tr>'
                   for key, label in [('decode_seconds', '加载与统计投影'), ('score_seconds', '完整评分'),
                                      ('elapsed_seconds', '启动、加载和评分总等待')])
    players = ''.join(f'<tr><td>{html.escape(row["name"])}</td><td>{html.escape(row["job"])}</td>'
                      f'<td>{row["total_score"]:.6f}</td><td>{row["output_score"]:.6f}</td>'
                      f'<td>{row["support_score"]:.6f}</td><td>{row["skills_checked"]}</td>'
                      f'<td>{"全部一致" if row["identical"] else "存在末尾所列差异"}</td></tr>'
                      for row in comparison['players'])
    difference = html.escape(json.dumps(comparison['differences'], ensure_ascii=False, indent=2))
    numeric_deltas = [abs(row['before']-row['after']) for row in comparison['differences']
                      if isinstance(row['before'], (int, float)) and isinstance(row['after'], (int, float))]
    max_delta = max(numeric_deltas, default=0.)
    supplementary = ''
    if args.initial_comparison:
        initial = json.loads(args.initial_comparison.read_text('utf8'))['timings']['score_seconds']
        supplementary += (f'<p>先行实验：{initial["before"]:.2f} → {initial["after"]:.2f} 秒，'
                          f'减少 {initial["time_reduction_percent"]:.2f}%。之后修正了新缓存命中后未填充上一轮缓存的问题，'
                          '重新进行本页主表的完整 A/B；先行实验不与主表平均。</p>')
    if args.initial_before and args.initial_comparison:
        from compare_exact_score_speed import differences
        first_before = json.loads(args.initial_before.read_text('utf8'))
        control_diff = list(differences(
            {k: first_before[k] for k in ('rows', 'plans')},
            {k: before[k] for k in ('rows', 'plans')}))
        initial_diff = json.loads(args.initial_comparison.read_text('utf8'))['differences']
        reproduced = control_diff == initial_diff
        audit = dict(both_round3_disabled=first_before['round3'] == before['round3'] == '0',
                     control_difference_count=len(control_diff),
                     initial_differences_exactly_reproduced_without_optimization=reproduced,
                     differences=control_diff)
        audit_path = args.output.parent / 'control_float_audit.json'
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), 'utf8')
        supplementary += (f'<p>尾差对照实验：两次都关闭第三轮优化，仍出现 {len(control_diff)} 处浮点末位差异；'
                          f'与先行 A/B 的全部差异（字段、前后原始值）完全相同：{reproduced}。'
                          '这证明先行实验中的这些尾差无需本轮优化也能复现；'
                          f'<a href="{audit_path.name}">保留对照原值</a>，不通过舍入掩盖差异。</p>')
    if args.axis_before and args.axis_after:
        axis_a = json.loads(args.axis_before.read_text('utf8'))
        axis_b = json.loads(args.axis_after.read_text('utf8'))
        supplementary += (f'<p>另做独立十字军单轴复测：{axis_a["seconds"]:.2f} → {axis_b["seconds"]:.2f} 秒，'
                          f'减少 {100*(1-axis_b["seconds"]/axis_a["seconds"]):.2f}%；'
                          f'{axis_b["segments"]} 个片段的完整结果校验值一致：{axis_a["digest"] == axis_b["digest"]}。'
                          '单轴结果不冒充全队耗时；带性能分析器的耗时不用于提速百分比。</p>')
    verdict = ('本组完整评分复测达到耗时减少至少 20% 的目标；不能据此承诺每场稳定减少 20%。'
               if gain >= 20 else '本组完整评分复测接近减少 20% 的目标；仍低于严格的 20%，且不能承诺每场稳定达到。'
               if gain >= 18 else '未达到额外 20% 的目标，以上为实际测量值。')
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>第三轮评分无损提速实测 · 20261019</title>
<style>body{{font:16px/1.7 system-ui,"Microsoft YaHei";color:#193b41;background:#f3f7f8;max-width:1150px;margin:32px auto;padding:24px}}section{{background:white;padding:22px;margin:18px 0;border:1px solid #d5e2e4;border-radius:12px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #dce5e6}}th{{background:#edf5f5}}strong{{color:#007d83}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style>
<h1>第三轮：以前两轮优化为基线继续提速</h1>
<p>2026-10-19 · 本地源码实测 · 未打包、未发布、未上传评分</p>
<section><h2>完整评分耗时减少 {gain:.2f}%</h2><p>{verdict}</p>
<table><tr><th>阶段</th><th>本次重新实测的上一轮基线</th><th>第三轮优化后</th><th>节省</th><th>耗时减少</th></tr>{rows}</table>
<p>抓包：{html.escape(Path(comparison['capture']).name)}。本轮开关分别为 0 和 1；前两轮优化均开启。
前后均为新进程、独立空证据缓存、4 个时间轴工作进程及4 个 Hit 工作进程；禁止联网、上传、回放缓存和完整评分缓存。</p>
<p>不直接拿上一轮报告中的秒数作本轮分母：源码其他任务更新、机器负载和文件系统缓存均可能影响耗时。
这是同机同样本 A/B，不能保证全部职业、全部抓包和其他硬件都有相同比例。主表复测期间不运行其他测试；
前后进程的 Python 哈希随机种子相同（{html.escape(str(before.get('python_hash_seed', '未记录')))}）。
先行实验中曾交叉运行短时间回归测试，故另做本页主表复测。没有修改系统或其他软件的优先级。</p>
{supplementary}</section>
<section><h2>评分与结果检查</h2>
<p>评分分项完全一致：{comparison['score_components_identical']}；时间轴完全一致：{comparison['plans_identical']}。
共比较 {len(comparison['players'])} 人、{sum(r['skills_checked'] for r in comparison['players'])} 条逐技能明细、{comparison['theory_segments_checked']} 条理论片段。
逐字段原始数值比较，不按显示小数舍入，不使用“相差几分以内”作为通过条件。</p>
<p>解码证据逐字节一致：{comparison['raw_cache_byte_identical']}；{comparison['hit_evidence_report_count']} 份逐 Hit 报告完全一致：{comparison['hit_evidence_reports_identical']}；预处理全部字段一致：{comparison['prepared_decoded_fields_identical']}。</p>
<table><tr><th>角色</th><th>职业</th><th>总分</th><th>输出分</th><th>辅助分</th><th>技能条目</th><th>完整字段检查</th></tr>{players}</table>
<p>完整结果对象差异数：{comparison['difference_count']}；其中数值差异的最大绝对值为 {max_delta:.17g}。
本页不把辅助中间值或挑衅覆盖率的浮点末位差异隐去。是否分数一致与是否所有中间字段逐位一致是两项检查，分别报告；
不能仅凭尾差很小就认定所有字段完全相同。</p><pre>{difference}</pre></section>
<section><h2>实际修改</h2><ol>
<li>面板状态采用精确字段对应的短标识；标识表和面板缓存共享生命周期，并有独立命名空间，避免不同理论轴误用状态。</li>
<li>复用同一组增益的覆盖顺序判定，保留原始顺序与精确施放时刻；不合并不同数值，不改变覆盖规则。</li>
<li>对只读运行时效果预先整理普通属性及减抗的合并输入；每次仍按原顺序合并，复制后的新快照必须重新整理。</li>
<li>预处理固定的未来减 CD 区间；清 Buff 时刻和区间变化会使缓存失效，当前角色的施放/到期区间仍动态计算。</li>
<li>仅在输入装备配置与完整 Buff 数值相同的情况下复用纯面板计算。审判力量数值先逐次解析，再查缓存；并非按技能名复用旧快照。</li>
</ol><p>没有减少候选、理论任务、搜索预算或评分精度，没有增加工作进程，也未修改实时抓包和悬浮窗。</p></section>
<section><h2>验证范围</h2><p>{args.tests} 项针对性测试通过，覆盖12个职业的面板数值、重新施放、化身到期、清 Buff 边界、重复来源、覆盖顺序、缓存隔离与变化后的失效。</p>
<p>理论任务数：{before['theory_diagnostics']['task_count']} → {after['theory_diagnostics']['task_count']}。
保留 DN_SCORE_EXACT_ROUND3=0 回退开关。</p>
<p><a href="{html.escape(args.comparison.name)}">逐字段对比与校验值 JSON</a> ·
<a href="{html.escape(args.before.name)}">优化前完整结果</a> · <a href="{html.escape(args.after.name)}">优化后完整结果</a></p></section></html>'''
    args.output.write_text(document, 'utf8')
    print(args.output)


if __name__ == '__main__':
    main()
