"""Compare frozen corpus audits; never equate evidence coverage with accuracy."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def compare(before: dict, after: dict, threshold: float = 95.0) -> dict:
    def captures(report):
        return {row['capture_path']: row for row in report.get('capture_manifest', [])}
    left, right = captures(before), captures(after)
    checks = []
    physical_totals = {field: {'before_rows': 0, 'after_rows': 0,
                              'before_damage': 0, 'after_damage': 0}
                       for field in ('raw_source_invariants', 'aggregated_invariants')}
    for path in sorted(left.keys() | right.keys()):
        old, new = left.get(path), right.get(path)
        if old is None or new is None:
            checks.append({'capture': path, 'conserved': False, 'reason': 'capture_set_changed'})
            continue
        match = old.get('capture_sha256') == new.get('capture_sha256')
        fields = {}
        evidence_changes = {}
        for field in ('raw_source_invariants', 'aggregated_invariants'):
            a, b = old.get(field), new.get(field)
            fields[field] = (None if not a or not b else
                             all(a.get(key) == b.get(key) for key in ('rows', 'damage', 'sha256')))
            for label, value in (('before', a), ('after', b)):
                if value:
                    physical_totals[field][label + '_rows'] += value.get('rows', 0)
                    physical_totals[field][label + '_damage'] += value.get('damage', 0)
            a, b = a or {}, b or {}
            old_hash, new_hash = a.get('hit_evidence_sha256'), b.get('hit_evidence_sha256')
            evidence_changes[field] = {
                'before_real_hits': a.get('real_hits'), 'after_real_hits': b.get('real_hits'),
                'changed': old_hash != new_hash if old_hash is not None and new_hash is not None else None,
            }
        checks.append({'capture': path, 'same_capture_bytes': match,
                       **fields, 'real_hit_evidence_changes': evidence_changes,
                       'conserved': match and all(value is True for value in fields.values())})
    def counts(report):
        jobs = {}
        for capture in report.get('capture_manifest', []):
            for job, values in capture.get('profession_summary', {}).items():
                jobs.setdefault(job, Counter()).update(values)
        return jobs
    old_counts, new_counts = counts(before), counts(after)
    routes = {}
    for row in after.get('details', []):
        if row.get('归因分类') != 'deterministic':
            continue
        job = row.get('职业')
        method = row.get('method', '')
        route = ('object_chain_hits' if method == 'damage_object_projectile_link' else
                 'fixed_packet_skill_hits' if method == 'packet_skill_id' else 'other_deterministic_hits')
        routes.setdefault(job, Counter())[route] += int(row.get('Hit数', 0))
    broad_counts = {}
    for capture in after.get('capture_manifest', []):
        for job, values in (capture.get('named_direct_candidates') or {}).items():
            broad_counts.setdefault(job, Counter()).update(values)
    def rates(values):
        exact = values.get('deterministic_hits', 0)
        total = values.get('total_direct_hits', 0)
        inferred = values.get('nondeterministic_hits', 0)
        named = exact + inferred
        return {'total_direct_hits': total, 'deterministic_hits': exact,
                'named_inferred_hits': inferred, 'unknown_hits': values.get('unattributed_hits', 0),
                'named_coverage_percent': 100 * exact / named if named else None,
                'all_coverage_percent': 100 * exact / total if total else None}
    jobs = []
    for job in dict.fromkeys([*before.get('professions', []), *after.get('professions', []), *new_counts]):
        a, b = rates(old_counts.get(job, {})), rates(new_counts.get(job, {}))
        broad = dict(broad_counts.get(job, {}))
        broad_total = broad.get('named_candidate_hits', 0)
        broad['coverage_percent'] = 100 * broad.get('deterministic_hits', 0) / broad_total if broad_total else None
        jobs.append({'job': job, 'before': a, 'after': b,
                     'deterministic_routes': dict(routes.get(job, {})),
                     'broader_named_candidate_gate': broad,
                     'named_target_met': b['named_coverage_percent'] is not None and b['named_coverage_percent'] >= threshold})
    return {'coverage_definition': 'deterministic evidence coverage; NOT measured ground-truth accuracy',
            'threshold': threshold, 'before_signature': before.get('analysis_signature'),
            'after_signature': after.get('analysis_signature'),
            'same_capture_set': bool(left) and left.keys() == right.keys(),
            'all_conserved': bool(checks) and all(row['conserved'] for row in checks),
            'matched_capture_physical_totals': physical_totals,
            'captures_with_real_hit_evidence_changes': sum(
                any(value['changed'] is True for value in row.get('real_hit_evidence_changes', {}).values())
                for row in checks),
            'capture_checks': checks, 'jobs': jobs,
            'jobs_meeting_named_target': sum(row['named_target_met'] for row in jobs),
            'jobs_meeting_broader_named_target': sum(
                row['broader_named_candidate_gate']['coverage_percent'] is not None
                and row['broader_named_candidate_gate']['coverage_percent'] >= threshold for row in jobs),
            'jobs_below_named_target': [row['job'] for row in jobs if not row['named_target_met']]}


def markdown(result: dict) -> str:
    def pct(value):
        return '无样本' if value is None else f'{value:.2f}%'
    rows = ['# 全职业伤害确定性归因复核', '',
            '统计的是证据链覆盖率，不是人工逐条核验后的准确率。主表限已标记具有真实命中证据的正向伤害，并排除明确延后处理的附加/周期伤害；已命名口径保留推断项作为分母，未知伤害另列。更宽的候选集合见文末。', '',
            '确定性总数同时包含完整对象链与包内明确技能字段，并非全部都是对象链。各技能的 method 和 Hit 数保留在明细中。', '',
            f"- 同一批抓包：{'是' if result['same_capture_set'] else '否/证据不足'}",
            f"- 每份原始及聚合伤害记录守恒：{'全部通过' if result['all_conserved'] else '未全部验证通过，见 JSON 逐包明细'}",
            f"- 真实命中资格标记发生变化的抓包：{result['captures_with_real_hit_evidence_changes']}；它与原始伤害条数不是同一个指标，变化另列于 JSON，不宣称命中标记完全不变。",
            f"- 已命名 Hit 达到 {result['threshold']:g}% 证据覆盖的职业：{result['jobs_meeting_named_target']}/{len(result['jobs'])}", '',
            '| 职业 | 修改前已命名覆盖 | 修改后已命名覆盖 | 修改后全部直接 Hit 覆盖 | 仍靠推断 Hit | 未命名 Hit |',
            '|---|---:|---:|---:|---:|---:|']
    for row in result['jobs']:
        a, b = row['before'], row['after']
        rows.append(f"| {row['job']} | {pct(a['named_coverage_percent'])} | {pct(b['named_coverage_percent'])} | {pct(b['all_coverage_percent'])} | {b['named_inferred_hits']} | {b['unknown_hits']} |")
    rows += ['', '未达目标的职业：' + '、'.join(result['jobs_below_named_target']), '',
             '守恒核验比较每条记录的时间、来源、目标、伤害、暴击、结果组和对象 ID 的多重集，不只比较总伤害。技能归属和真实命中证据标记允许变化，并另行审计。', '']
    if any(row['deterministic_routes'] for row in result['jobs']):
        rows += ['## 确定性证据路线拆分', '',
                 '| 职业 | 对象链 Hit | 包内技能字段 Hit | 其他确定性 Hit |',
                 '|---|---:|---:|---:|']
        for row in result['jobs']:
            route = row['deterministic_routes']
            rows.append(f"| {row['job']} | {route.get('object_chain_hits', 0)} | {route.get('fixed_packet_skill_hits', 0)} | {route.get('other_deterministic_hits', 0)} |")
        rows += ['']
    if any(row['broader_named_candidate_gate'].get('named_candidate_hits') for row in result['jobs']):
        rows += ['## 更宽候选集合的保守联合验收', '',
                 '下表同时要求真实命中证据和确定性归因：把未标记为真实命中的已命名伤害以及 direct_hit_overflow 也保留在分母中，作为尚未联合通过的候选。明确的附加伤害、周期状态、治疗及承伤仍排除。分母不是已确认的真实 Hit 集合，因此联合通过率不能直接当作技能归因准确率，未通过也不等于已证实归错；此口径不能与旧报告未保存的宽口径进行前后比较。', '',
                 '| 职业 | 已命名候选数 | 联合通过率 | 未标记真实命中 | direct_hit_overflow |',
                 '|---|---:|---:|---:|---:|']
        for row in result['jobs']:
            value = row['broader_named_candidate_gate']
            rows.append(f"| {row['job']} | {value.get('named_candidate_hits', 0)} | {pct(value['coverage_percent'])} | {value.get('without_real_hit_evidence', 0)} | {value.get('direct_hit_overflow', 0)} |")
        rows += ['']
    return '\n'.join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = compare(json.loads(args.before.read_text(encoding='utf-8')), json.loads(args.after.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    args.output.with_suffix('.md').write_text(markdown(result), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('all_conserved', 'jobs_meeting_named_target', 'jobs_below_named_target')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
