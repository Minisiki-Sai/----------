"""Render a read-only cloud audit, preserving candidates and evidence boundaries."""
from collections import defaultdict
from html import escape
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / 'work' / name).read_text(encoding='utf-8'))


def decode(timeline, key):
    tick = 0
    scale = timeline.get('q', 100)
    for row in timeline.get(key, []):
        tick += row[0]
        yield tick / scale, (tick + row[1]) / scale, timeline['n'][row[2]], row[3:]


def num(value):
    return float(value or 0)


def diagnosis(job, skill):
    if job == '风行者' and skill in {'螺旋踢', '精神火焰', '凌波微步'}:
        return '优先核查演出时间：客户端描述被动技能减90%冷却；模型未找到相应限定技能规则。连续派生仍需统一计数。'
    if skill in {'雷电之手', '炮塔', '寒冰喷射塔', '重磅鸭'}:
        return '召唤/补召唤口径：核对存续、同时存在上限与伤害。多次补召唤不能直接等同于更高输出。'
    if skill in {'地狱火海', '虔诚', '觉醒', '猎人翻滚'}:
        return '持续/姿态/功能技能：核对取消、循环、开关和派生；次数不是完整输出的充分证据。'
    if job == '时空领主' and skill == '九尾激光':
        return '历史理论轴：已修复聚能激光九尾充能；这些上传记录尚不能证明新模型仍有相同缺陷。'
    if skill == '冰尖柱':
        return '多个法师职业重复出现：先核对理论排程是否主动少用填充技能，再查计数、等级、冷却和衔接。'
    return '待区分排程取舍与模型遗漏：理论优化整套伤害，不追求每个技能最多释放；当前没有足够证据直接判为bug。'


def main():
    groups = read('release_excess_groups_20261011.json')
    examples = read('release_excess_examples_20261011.json')
    details = read('release_excess_details_20261011.json')
    coverage = read('release_excess_coverage_20261011.json')
    best = {(r['job'], r['skill_name']): r for r in examples if int(r['rn']) == 1}
    detail_by_id = {r['row_fingerprint']: r for r in details}
    repeated = [r for r in groups if int(r['recent_schema_excess']) >= 3]
    by_job = defaultdict(list)
    for row in repeated:
        by_job[row['job']].append(row)
    summary_rows = []
    for job, rows in sorted(by_job.items()):
        descriptions = []
        for r in sorted(rows, key=lambda x: int(x['recent_schema_excess']), reverse=True):
            example = best[(job, r['skill_name'])]
            descriptions.append(f"{r['skill_name']}：{example['actual_count']} / {example['theory_count']}（{r['recent_schema_excess']}条超出）")
        summary_rows.append('<tr><td>' + escape(job) + '</td><td>' + '<br>'.join(map(escape, descriptions)) + '</td></tr>')
    all_rows = []
    for r in sorted(groups, key=lambda x: (-int(x['recent_schema_excess']), x['job'], x['skill_name'])):
        ex = best.get((r['job'], r['skill_name']))
        if ex is None:
            raise ValueError('Missing representative example')
        power_ratio = num(ex['actual_power']) / num(ex['theory_power']) if num(ex['theory_power']) > 0 else None
        detail = detail_by_id.get(ex['row_fingerprint'])
        proportional = ''
        if detail:
            skill = next((s for s in detail['skill_details'] if s['skill_name'] == r['skill_name']), None)
            if skill and skill.get('proportional_output'):
                proportional = '；持续/按有效命中折算'
        values = [r['job'], r['skill_name'],
                  f"{r['recent_schema_excess']} / {r['recent_schema_samples']}",
                  f"{r['excess_samples']} / {r['samples']}（{r['excess_captures']}个抓包哈希）",
                  f"{ex['actual_count']} / {ex['theory_count']}",
                  f"{power_ratio:.2f}倍{proportional}" if power_ratio is not None else '无理论伤害分母',
                  f"{ex['recorded_at']} · {ex['nest']} · 算法{ex['score_schema_version']}",
                  diagnosis(r['job'], r['skill_name'])]
        all_rows.append('<tr>' + ''.join('<td>' + escape(str(v)) + '</td>' for v in values) + '</tr>')
    rapid = []
    for record in details:
        if record['job'] != '风行者':
            continue
        timeline = record['compact_timeline']
        windows = [(a, b) for a, b, name, flags in decode(timeline, 'e')
                   if name.startswith('演出时间') and flags == [4]]
        for skill in ('螺旋踢', '精神火焰'):
            starts = [a for a, _, name, _ in decode(timeline, 'a') if name.removesuffix('EX') == skill]
            pairs = [(a, b) for a, b in zip(starts, starts[1:]) if 0 < b-a < .8]
            inside = sum(any(lo <= a <= b < hi for lo, hi in windows) for a, b in pairs)
            if pairs:
                rapid.append({'row_fingerprint': record['row_fingerprint'], 'recorded_at': record['recorded_at'],
                              'skill': skill, 'rapid_gaps_under_0_8s': len(pairs),
                              'both_casts_inside_showtime': inside, 'showtime_windows': len(windows),
                              'minimum_gap': round(min(b-a for a,b in pairs), 3)})
    evidence_rows = ''.join('<tr>' + ''.join('<td>' + escape(str(x)) + '</td>' for x in
                            (r['recorded_at'], r['skill'], r['rapid_gaps_under_0_8s'], r['both_casts_inside_showtime'], r['minimum_gap'])) + '</tr>' for r in rapid)
    evidence_path = ROOT / 'deliverables' / '全职业释放超理论_机制证据_20261011.json'
    evidence_path.write_text(json.dumps(rapid, ensure_ascii=False, indent=2), encoding='utf-8')
    total = sum(int(r['score_records']) for r in coverage)
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>全职业实际释放超理论筛查 · 2026-10-11</title>
<style>body{{font:15px/1.65 "Microsoft YaHei",sans-serif;color:#172d39;background:#f5f8fa;margin:24px}}main{{max-width:1600px;margin:auto}}h1{{font-size:25px}}h2{{font-size:20px;margin-top:30px}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{border:1px solid #d7e1e8;padding:9px;text-align:left;vertical-align:top}}th{{background:#e7f0f3}}.note{{padding:15px;background:#fff4db;border-left:4px solid #c68715}}.wide{{overflow-x:auto}}small{{color:#425763}}</style><main>
<h1>全职业实际释放大于上传理论释放：只读筛查</h1>
<p>统计日期：2026-09-28至2026-10-11；{total:,}条评分记录，{len(coverage)}个职业/角色分类标签。
筛出{len(groups)}组职业×技能候选；其中{len(by_job)}个职业分类、{len(repeated)}组技能在较新上传算法（≥20261006）中至少重复出现3条。</p>
<div class="note">筛查阈值：理论≥5次、实际≥15次、实际≥理论120%、多出≥5次。数字为<strong>实际次数 / 理论次数</strong>，不是Hit。
同一场不同机器上传或重算会重复，条数不等于独立战斗数。代表样本选较新算法内比值最大的记录，不能当平均水平。
这些是已上传历史理论轴，不是对当前修复模型的正式重算。本次没有修改任何云端评分。</div>
<h2>逐职业重复异常候选</h2><p>此表只列较新算法中至少3条超出的技能；零星、较旧算法案例在末表完整保留。</p>
<table><thead><tr><th>职业</th><th>技能及代表样本（实际/理论）</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table>
<h2>机制线索：风行者演出时间</h2>
<p>客户端描述：25秒内所有被动技能冷却减少90%。螺旋踢、精神火焰基础CD约6秒，因此约0.6秒的连续释放具有合理来源。
当前skill_buff表没有演出时间条目，冷却规则和理论排程中未找到其专属规则；这里只将其列为高优先级机制遗漏线索，不把90%减CD套用到全部主动技能。
下表按上传的完整时间轴检查连续两次起点，不等同于评分内的施放总数。0.8秒是诊断阈值，不是游戏硬上限。</p>
<table><thead><tr><th>记录时间</th><th>技能</th><th>间隔&lt;0.8秒的相邻对</th><th>两次均在演出时间内</th><th>最小间隔/秒</th></tr></thead><tbody>{evidence_rows}</tbody></table>
<h2>完整147组候选与伤害核对</h2>
<p>理论轴追求整套伤害最大，不追求每个技能次数最大。填充技能多用可能伴随核心技能少用；召唤和持续技能还须看存续、有效命中及标准伤害。
例如重炮手重磅鸭15/6，但标准伤害只有理论约0.76倍，不能由次数直接认定理论低估。</p>
<div class="wide"><table><thead><tr><th>职业</th><th>技能</th><th>≥20261006超出/样本</th><th>全时期超出/样本</th><th>样本实际/理论</th><th>标准伤害实际/理论</th><th>样本时间·巢穴·算法</th><th>排查方向（非定论）</th></tr></thead><tbody>{''.join(all_rows)}</tbody></table></div>
<p><small>来源：team_scores、team_score_skill_details以及43份上传compact_timeline明细。数据仅本地分析，未改线上数据。</small></p></main></html>'''
    target = ROOT / 'deliverables' / '全职业实际释放大于理论释放_筛查_20261011.html'
    target.write_text(html, encoding='utf-8')
    print(json.dumps({'report': str(target), 'groups': len(groups), 'repeated_groups': len(repeated),
                      'repeated_jobs': len(by_job), 'rapid_evidence': rapid}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
