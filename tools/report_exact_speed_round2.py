"""Render the second exact optimization experiment from measured JSON only."""
import argparse
import html
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--comparison', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--tests', required=True)
    p.add_argument('--before', type=Path)
    p.add_argument('--after', type=Path)
    args = p.parse_args()
    result = json.loads(args.comparison.read_text('utf8'))
    timings = result['timings']
    reduction = timings['score_seconds']['time_reduction_percent']
    players = result['players']
    exact = result['all_score_fields_and_plans_identical']
    heading = ('全部字段一致' if exact else
               '评分分项与时间轴一致；其他字段存在差异' if result.get('score_components_identical') and result.get('plans_identical')
               else '发现评分或时间轴差异，不能作为无损优化交付')
    difference_text = ('' if exact else '<pre>' + html.escape(json.dumps(result['differences'], ensure_ascii=False, indent=2)) + '</pre>')
    if result['differences'] and all(d['path'].endswith(('/tank_taunt_completion', '/tank_taunt_coverage'))
                                    and abs(d['before'] - d['after']) < 1e-12 for d in result['differences']):
        difference_text = ('<p>上述两处是约 10⁻¹⁴ 的底层挑衅完成/覆盖率差异，显示值和所有评分分项均未变化。'
                           '现有挑衅计算遍历无序 Boss 集合并累加浮点数；独立进程可能产生末位差异。'
                           '没有为了宣称完全一致而舍入或删除这两处差异，也未修改该既有评分计算。</p>' + difference_text)
    phase_text = ''
    if args.before and args.after:
        old = json.loads(args.before.read_text('utf8'))['theory_diagnostics']
        new = json.loads(args.after.read_text('utf8'))['theory_diagnostics']
        phase_text = (f'<p>辅助对照轴的最终汇总校验：{old["timing_ablation"]["seconds"]:.2f} → {new["timing_ablation"]["seconds"]:.2f} 秒；'
                      f'理论任务数：{old["task_count"]} → {new["task_count"]}，没有少算任务。</p>')
    cells = ''.join(
        f'<tr><td>{label}</td><td>{timings[key]["before"]:.2f} 秒</td>'
        f'<td>{timings[key]["after"]:.2f} 秒</td><td>{timings[key]["seconds_saved"]:.2f} 秒</td>'
        f'<td>{timings[key]["time_reduction_percent"]:.2f}%</td></tr>'
        for key, label in [('decode_seconds', '加载与统计投影'), ('score_seconds', '完整评分'),
                           ('elapsed_seconds', '启动、加载、评分总等待')])
    player_rows = ''.join(
        f'<tr><td>{html.escape(row["name"])}</td><td>{html.escape(row["job"])}</td>'
        f'<td>{row["total_score"]:.6f}</td><td>{row["output_score"]:.6f}</td>'
        f'<td>{row["support_score"]:.6f}</td><td>{row["skills_checked"]}</td>'
        f'<td>{"一致" if row["identical"] else "不一致"}</td></tr>' for row in players)
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>第二轮评分无损提速实测 · 20261019</title>
<style>body{{font:16px/1.7 system-ui,"Microsoft YaHei";color:#193b41;background:#f3f7f8;max-width:1150px;margin:32px auto;padding:24px}}section{{background:white;padding:22px;margin:18px 0;border:1px solid #d5e2e4;border-radius:12px}}h1,h2{{line-height:1.4}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #dce5e6}}th{{background:#edf5f5}}strong{{color:#007d83}}.note{{color:#47626a}}code{{word-break:break-all}}</style>
<h1>第二轮：在上一轮优化基础上继续提速</h1>
<p>2026-10-19 · 本地源码实验 · 未打包、未发布、未上传评分</p>
<section><h2>实测完整评分耗时减少 {reduction:.2f}%</h2>
<p>{'达到本轮约 30% 的目标。' if reduction >= 28 else '尚未达到本轮约 30% 的目标，不把局部加速或缓存命中后的秒开当成整场收益。'}</p>
<table><tr><th>阶段</th><th>上一轮优化后基线</th><th>本轮优化后</th><th>节省</th><th>耗时减少</th></tr>{cells}</table>
<p>样本：<code>{html.escape(Path(result['capture']).name)}</code>。前后均启用上一轮全部优化；各自新进程、独立空证据缓存、不读取完整评分或回放结果缓存；均为 {result['workers']} 个时间轴工作进程和 4 个 Hit 工作进程，禁止联网和上传。</p>
<p class="note">这是同机一组整场 A/B，不是跨机器性能保证。文件系统缓存和系统负载会造成波动。评分提速与包含加载的总等待改善分开列示；实际应用会根据内存和战斗状态调整进程数。</p></section>
<section><h2>无损检查：{heading}</h2>
<p>逐字段比较 {len(players)} 人完整评分、{sum(row['skills_checked'] for row in players)} 条技能明细和 {result['theory_segments_checked']} 条理论片段。
包括实际与理论时间轴、输出、辅助、职业特色、承伤和总分；原始数值比较，<strong>差异 {result['difference_count']} 处</strong>，不使用四舍五入或分数容差。</p>
<p>解码证据逐字节一致：{result['raw_cache_byte_identical']}；{result['hit_evidence_report_count']} 份逐 Hit 报告一致：{result['hit_evidence_reports_identical']}。
公共 Hit 预处理文件数：{result['prepared_artifact_counts'][0]} → {result['prepared_artifact_counts'][1]}；解码后全部字段一致：{result['prepared_decoded_fields_identical']}。
预处理压缩文件逐字节一致：{result['prepared_unique_content_identical']}（不同进程的容器序列化顺序可能影响压缩字节，数据字段另行完整核对）。</p>{difference_text}
<table><tr><th>角色</th><th>职业</th><th>总分</th><th>输出分</th><th>辅助分</th><th>技能数</th><th>完整字段</th></tr>{player_rows}</table></section>
<section><h2>本轮怎么改</h2><ol>
<li>外部 Buff/Debuff 的固定区间重复求值时，复用已整理的效果行。缓存同时区分区间、清 Buff 边界、属性排除和来源角色；角色自身的动态动作与审判力量快照仍逐次计算。</li>
<li>相同顺序的不可变效果行复用状态签名；保留覆盖顺序、命名空间和对象引用，不把相似但不同的状态合并。</li>
<li>纯时间轴的只读配置使用浅层投影，避免反复深拷贝完整战斗数据；被替换的 Buff 列表和附加属性仍独立构造。</li>
<li>技能名称规范化复用计算结果；规则和别名仍读取当前值，不缓存动态规则。</li>
<li>固定同一不可变预处理函数的缓存指纹，避免同一份 Hit 预处理数据在一场评分里重复生成。函数实现变化时仍区分版本。</li>
<li>加快辅助对照轴输入校验：排序时先比较键前缀，不重复展开大型配置值；同一轮只读配置复用已规范化的子树，动态 Buff、时间和雕纹等仍重新校验。保留原有排序、键内容和校验字节。</li>
</ol><p>不降低候选数量、搜索预算或评分精度，不增加并行进程数，也不改实时抓包和悬浮窗链路。</p></section>
<section><h2>测试与适用边界</h2><p>{html.escape(args.tests)}</p>{phase_text}
<p>整场样本覆盖上述 8 个职业，并不等于已验证全部职业、所有地图或所有硬件；保留环境开关用于精确 A/B 与回退。</p>
<p><a href="{html.escape(args.comparison.name)}">机器可读逐字段对比与原始文件校验值</a></p></section></html>'''
    args.output.write_text(document, 'utf8')
    print(args.output)


if __name__ == '__main__':
    main()
