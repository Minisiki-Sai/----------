"""Report repeated scoring measurements without choosing the fastest sample."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_exact_score_speed import differences

LABELS = {'A': '关闭三轮开关', 'B': '只开第一轮', 'C': '开启前两轮', 'D': '开启全部三轮'}


def hashed(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    directory = args.directory
    runs = json.loads((directory / 'runs.json').read_text('utf8'))
    manifest = json.loads((directory / 'manifest.json').read_text('utf8'))
    assert len(runs) == 12 and json.loads((directory / 'complete.json').read_text('utf8'))['inputs_unchanged']
    reference = json.loads((directory / runs[0]['file']).read_text('utf8'))
    comparable = {k: reference[k] for k in ('rows', 'plans')}
    scores = ('total_score', 'output_score', 'support_score', 'specialty_score', 'survival_penalty', 'grade')
    checks = []
    reference_evidence = None
    for run in runs:
        telemetry = json.loads((directory / (Path(run['file']).stem + '_telemetry.json')).read_text('utf8'))
        run['largest_monitor_gap_seconds'] = max((b['time'] - a['time'] for a, b in zip(telemetry, telemetry[1:])), default=0)
        run['prepared_evidence_files'] = len(list((directory / (Path(run['file']).stem + '_evidence') / 'prepared').glob('*.bin')))
        report = json.loads((directory / run['file']).read_text('utf8'))
        found = list(differences(comparable, {k: report[k] for k in ('rows', 'plans')}))
        numeric = [abs(x['after'] - x['before']) for x in found
                   if isinstance(x['before'], (int, float)) and isinstance(x['after'], (int, float))]
        check = {'file': run['file'], 'differences': len(found), 'examples': found[:30],
                 'max_numeric_delta': max(numeric, default=0),
                 'plans_exact': reference['plans'] == report['plans'],
                 'score_components_exact': len(reference['rows']) == len(report['rows']) and all(a['source_id'] == b['source_id'] and
                    all(a.get(k) == b.get(k) for k in scores)
                    for a, b in zip(reference['rows'], report['rows'])),
                 'all_fields_exact': not found}
        evidence_dir = directory / (Path(run['file']).stem + '_evidence')
        evidence = sorted(hashed(json.loads(p.read_text('utf8')))
                          for p in (evidence_dir / 'evidence').glob('*.json'))
        if reference_evidence is None:
            reference_evidence = evidence
        check['hit_evidence_count'] = len(evidence)
        check['hit_evidence_exact'] = bool(evidence) and evidence == reference_evidence
        checks.append(check)
    stats = {}
    for mode in LABELS:
        selected = [r for r in runs if r['mode'] == mode]
        stats[mode] = {}
        for field in ('score_seconds', 'decode_seconds', 'elapsed_seconds', 'parallel_seconds'):
            vals = [r[field] for r in selected]
            stats[mode][field] = {'median': statistics.median(vals), 'min': min(vals), 'max': max(vals), 'values': vals}
        vals = [r['accounting']['cpu_seconds'] for r in selected]
        stats[mode]['process_cpu_seconds'] = {'median': statistics.median(vals), 'values': vals}
    pairs = []
    for before, after in [('A', 'B'), ('B', 'C'), ('C', 'D'), ('A', 'D')]:
        pair = {'before': before, 'after': after, 'blocks': [], 'timings': {}}
        for field in ('score_seconds', 'elapsed_seconds', 'process_cpu_seconds'):
            a, b = stats[before][field]['median'], stats[after][field]['median']
            pair['timings'][field] = {'before': a, 'after': b, 'reduction_percent': 100 * (1 - b / a)}
        for block in (1, 2, 3):
            a = next(r for r in runs if r['block'] == block and r['mode'] == before)
            b = next(r for r in runs if r['block'] == block and r['mode'] == after)
            pair['blocks'].append({'block': block, 'score_reduction_percent': 100 * (1 - b['score_seconds'] / a['score_seconds']),
                                   'total_reduction_percent': 100 * (1 - b['elapsed_seconds'] / a['elapsed_seconds'])})
        pair['paired_score_reduction_median'] = statistics.median(b['score_reduction_percent'] for b in pair['blocks'])
        pair['paired_total_reduction_median'] = statistics.median(b['total_reduction_percent'] for b in pair['blocks'])
        pair['timings_interpretation'] = 'Descriptive ratio of independent medians; primary estimator is median of within-block reductions.'
        pairs.append(pair)
    result = {'statistics': stats, 'pairs': pairs, 'checks': checks, 'runs': runs,
              'all_fields_exact': all(x['all_fields_exact'] for x in checks),
              'score_components_exact': all(x['score_components_exact'] for x in checks),
              'plans_exact': all(x['plans_exact'] for x in checks),
              'hit_evidence_exact': all(x['hit_evidence_exact'] for x in checks),
              'players': len(reference['rows']),
              'skills': sum(len(r['skill_details']) for r in reference['rows']),
              'segments': sum(len(p['segments']) for p in reference['plans'].values()),
              'reference_result_digest': hashed(comparable)}
    result['primary_estimator'] = 'median of within-block reductions; all runs retained'
    axis_path = directory / 'axis_repeat/analysis.json'
    if axis_path.exists():
        result['axis_diagnostic'] = json.loads(axis_path.read_text('utf8'))
        result['axis_diagnostic']['paired_reduction_median'] = statistics.median(
            p['reduction_percent'] for p in result['axis_diagnostic']['pairs'])
    (directory / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf8')
    table = ''.join(f'<tr><th>{mode} · {label}</th><td>{" / ".join(f"{x:.1f}" for x in stats[mode]["score_seconds"]["values"])}</td>'
                    f'<td>{stats[mode]["score_seconds"]["median"]:.1f}</td><td>{stats[mode]["elapsed_seconds"]["median"]:.1f}</td>'
                    f'<td>{stats[mode]["process_cpu_seconds"]["median"]:.1f}</td></tr>' for mode, label in LABELS.items())
    pair_rows = ''.join(f'<tr><th>{p["before"]} → {p["after"]}</th><td>{p["paired_score_reduction_median"]:.2f}%</td>'
                       f'<td>{" / ".join("{:.2f}%".format(b["score_reduction_percent"]) for b in p["blocks"])}</td>'
                       f'<td>{p["paired_total_reduction_median"]:.2f}%</td></tr>' for p in pairs)
    block_rows = ''
    for block in (1, 2, 3):
        batch = {r['mode']: r for r in runs if r['block'] == block}
        block_rows += f'<tr><th>第 {block} 批</th>' + ''.join(f'<td>{batch[m]["score_seconds"]:.1f}</td>' for m in 'ABCD')
        block_rows += f'<td>{100*(1-batch["D"]["score_seconds"]/batch["A"]["score_seconds"]):.2f}%</td></tr>'
    axis_section = ''
    if 'axis_diagnostic' in result:
        axis = result['axis_diagnostic']
        axis_rows = ''.join(f'<tr><td>{p["block"]}</td><td>{p["before"]:.3f}</td><td>{p["after"]:.3f}</td><td>{p["reduction_percent"]:.2f}%</td></tr>' for p in axis['pairs'])
        axis_section = f'''<section><h2>补充：第三轮短间隔单轴交替复测</h2><p>顺序 CD、DC、CD、DC；同一固定理论轴，C/D 各四次，新进程、固定哈希种子，所有数值结果摘要一致：{axis['identical']}。无性能分析器插桩。只验证一条理论轴的计算开销，不代替整场收益。</p><table><tr><th>配对</th><th>第三轮关闭（秒）</th><th>第三轮开启（秒）</th><th>减少</th></tr>{axis_rows}</table><p>四次配对的减少比例中位数为 {axis['paired_reduction_median']:.2f}%。两组耗时各自中位数为 {axis['medians']['C']:.3f} 和 {axis['medians']['D']:.3f} 秒，仅作为分布描述。该局部结果不能套用到整场，整场第三轮配对中位数仍为 {pairs[2]['paired_score_reduction_median']:.2f}%。<a href="axis_repeat/analysis.json">单轴完整结果</a></p></section>'''
    all_rows = ''.join(f'<tr><td>{r["index"]}</td><td>{r["block"]}</td><td>{r["mode"]}</td><td>{r["decode_seconds"]:.1f}</td>'
                      f'<td>{r["score_seconds"]:.1f}</td><td>{r["parallel_seconds"]:.1f}</td><td>{r["system_cpu_median_percent"]:.1f}%</td>'
                      f'<td>{r["minimum_available_mb"]:.0f} MB</td><td>{r["accounting"]["peak_commit_mb"]:.0f} MB</td><td><a href="{r["file"]}">原始结果</a></td></tr>' for r in runs)
    check_rows = ''.join(f'<tr><td>{x["file"]}</td><td>{x["differences"]}</td><td>{x["max_numeric_delta"]:.3g}</td>'
                        f'<td>{x["score_components_exact"]}</td><td>{x["plans_exact"]}</td></tr>' for x in checks)
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>三轮提速重复验证</title>
<style>body{{max-width:1300px;margin:32px auto;padding:20px;background:#f3f7f8;color:#183a40;font:16px/1.75 system-ui,"Microsoft YaHei"}}section{{background:white;border:1px solid #d5e2e4;border-radius:12px;margin:20px 0;padding:24px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #dce5e6}}th{{background:#edf5f5}}code{{overflow-wrap:anywhere}}h1,h2{{line-height:1.4}}a{{color:#007d83}}</style>
<h1>三轮优化是否有效：12 次整场交错复测</h1><p>同一份冻结源码 · 同一抓包 · 四组各三次 · 未上传、未发布</p>
<section><h2>先看结论</h2><p>三批累计评分耗时分别减少 {pairs[-1]['blocks'][0]['score_reduction_percent']:.1f}%、{pairs[-1]['blocks'][1]['score_reduction_percent']:.1f}%、{pairs[-1]['blocks'][2]['score_reduction_percent']:.1f}%，分批比较的中位数为 {pairs[-1]['paired_score_reduction_median']:.1f}%。第三轮单独的分批中位收益为 {pairs[2]['paired_score_reduction_median']:.1f}%，不能再把此前单组 19.1% 称为稳定收益。</p><p>保留了较慢的第二次基线和整体较慢的第三批。由于整台机器的运行状态仍会波动，本报告优先按同一批配对比较，不把不同批次的中位数相除，当成累计收益。</p><table><tr><th>批次</th><th>A 全关闭</th><th>B 第一轮</th><th>C 前两轮</th><th>D 全开启</th><th>A→D 减少</th></tr>{block_rows}</table><p>表内耗时均为完整评分秒数，不含初始加载。</p></section>
<section><h2>测试条件与边界</h2><p>抓包：{html.escape(manifest['capture'])}。顺序：{' → '.join(manifest['orders'])}，每组四个评分/Hit工作进程，新进程、独立空证据缓存，固定 PYTHONHASHSEED=0。不清除操作系统文件缓存；各次开始前读取并校验固定输入。只清理本次测试自己启动的工作进程，不终止用户应用。</p><p>测试源码及数据完整复制并逐次核验哈希；四组仅改变此前三轮的受控优化开关，不是拿历史 v4.78 安装包作基线。开关以外的共同实现保持一致，因此这验证的是可开关路径的当前收益，而不是复原历史全部源代码差异。</p><p>所有 12 次均保留，不挑最快、不剔除慢样本。每组仅三次，单抓包八职业，不能推出所有机器/抓包的固定收益。CPU 时间是进程树累计 CPU 秒，不等于用户等待的墙钟时间。</p></section>
<section><h2>每组耗时分布：秒</h2><p>下表中位数仅用于描述分布，可能来自不同批次，不直接相除作为主要提速结论。</p><table><tr><th>配置</th><th>三次评分耗时</th><th>评分中位数</th><th>含加载总等待中位数</th><th>进程树CPU秒中位数</th></tr>{table}</table></section>
<section><h2>逐轮增量及累计收益</h2><p>正值表示耗时减少，负值表示变慢。A→B 为第一轮；B→C 为第二轮；C→D 为第三轮；A→D 为累计。先计算每一批的减少比例，再取三批中位数；各轮中位比例不能直接相加或相乘。</p><table><tr><th>比较</th><th>分批评分减少中位数</th><th>三批各自减少</th><th>分批总等待减少中位数</th></tr>{pair_rows}</table></section>
{axis_section}
<section><h2>评分与时间轴不变检查</h2><p>每次均与第一组原始值逐字段比较，不四舍五入：{result['players']} 人、{result['skills']} 条技能明细、{result['segments']} 条理论片段。所有字段完全相等：{result['all_fields_exact']}；评分分项完全相等：{result['score_components_exact']}；理论轴完全相等：{result['plans_exact']}。</p><table><tr><th>结果</th><th>字段差异数</th><th>最大数值差异</th><th>评分分项一致</th><th>轴一致</th></tr>{check_rows}</table></section>
<section><h2>全部运行及系统状态</h2><p>峰值提交内存为整棵测试进程树的 Windows Job 统计，不等于物理常驻内存。测试没有关闭其他用户应用，监测了系统负载和剩余内存，但没有完整记录全程 CPU 频率，不能武断把所有波动归因于后台占用或某一原因。最大资源采样间隔 {max(r['largest_monitor_gap_seconds'] for r in runs):.2f} 秒。</p><div style="overflow-x:auto"><table><tr><th>顺序</th><th>批次</th><th>配置</th><th>加载</th><th>评分</th><th>理论相关阶段</th><th>系统CPU中位</th><th>最低空闲内存</th><th>任务峰值提交</th><th>证据</th></tr>{all_rows}</table></div><p><a href="analysis.json">完整分析数据</a> · <a href="manifest.json">冻结输入清单及方案</a> · <a href="runs.json">全部运行摘要</a></p></section></html>'''
    path = directory / '三轮评分提速重复验证_20261019.html'
    path.write_text(document, 'utf8')
    print(json.dumps({k: result[k] for k in ('statistics', 'pairs', 'all_fields_exact', 'score_components_exact', 'plans_exact')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
