"""Verify all physical counters and per-job evidence without changing scope."""
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.compare_profession_attribution_audits import compare
from tools.run_magma_canonical_acceptance_v37 import OUT, BASE, read
from tools.audit_direct_damage_by_profession import _merge_reports


def check_pair(before, after):
    assert before['capture_path'] == after['capture_path']
    assert before['capture_sha256'] == after['capture_sha256']
    assert before['decoded_events'] == after['decoded_events']
    for key in ('raw_source_invariants', 'aggregated_invariants'):
        assert before[key] == after[key], (before['capture_path'], key)
        assert after[key]['hit_evidence_sha256']
    assert before['profession_summary'].keys() == after['profession_summary'].keys()
    gains = []
    for job, new in after['profession_summary'].items():
        old = before['profession_summary'][job]
        for key in ('total_direct_hits', 'total_direct_damage', 'unattributed_hits', 'unattributed_damage'):
            assert old.get(key, 0) == new.get(key, 0), (job, key)
        if old != new:
            assert job == '炼金圣士', job
            delta = new.get('deterministic_hits', 0) - old.get('deterministic_hits', 0)
            assert delta > 0
            assert old['nondeterministic_hits'] - new.get('nondeterministic_hits', 0) == delta
            gains.append(dict(capture=after['capture_path'], job=job, confirmed_gain=delta))
    return gains


def main():
    previous = read(BASE)
    paths = {r['capture_path'] for r in previous['capture_manifest']}
    outputs = sorted((OUT / 'after_results').glob('*.json'))
    assert len(outputs) == 36
    reports = []
    a, b = (read(OUT / s / 'frozen_manifest.json') for s in ('before', 'after'))
    changed = [k for k in a.keys() | b.keys() if k.startswith('dragon_nest_builder/') and a.get(k) != b.get(k)]
    # A concurrent, unrelated UI rescue-text edit crossed the freeze boundary.
    # It was reviewed separately; pin its exact pair instead of ignoring UI
    # changes in general. Full replay conservation below remains mandatory.
    ui = 'dragon_nest_builder/ui/live_dps_page_scoring.py'
    assert a[ui] == '3d38f3f576ff9833e3f1da79bf09788a0ead2f2e7b046172032b0083cabfe602'
    assert b[ui] == '236dab5cc16393d2321c67115455df93de33e2981bcadb10916f987604adb8d5'
    assert set(changed) == {'dragon_nest_builder/live_dps/autonomous_attack_families.py', ui}, changed
    expected = {k: v for k, v in b.items() if k.startswith('dragon_nest_builder/') and k.endswith('.py')}
    old = {r['capture_path']: r for r in previous['capture_manifest']}
    old_checkpoints = {
        r['capture_path']: r for p in (BASE.parent / 'profession_direct_damage_checkpoints').glob('*.json')
        for r in [read(p)['report']]}
    changes, fresh_pairs = [], []
    for output in outputs:
        r = read(output)
        assert r['runtime_py_hashes'] == expected
        digest = sha256()
        with Path(r['capture_path']).open('rb') as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        assert r['capture_sha256'] == digest.hexdigest()
        changes += check_pair(old[r['capture_path']], r)
        # A new certificate may replace legacy grouping, but only wall-entity
        # rows of the wall family are supposed to change in this increment.
        def unaffected(rows):
            return sorted((x for x in rows if not (
                x['job'] == '炼金圣士' and x['skill_id'] == '4410'
                and x['projectile_template_id'] == '实体模板:32022')),
                key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
        assert unaffected(old_checkpoints[r['capture_path']]['details']) == unaffected(r['details'])
        p = OUT / 'before_results' / (sha256(r['capture_path'].encode()).hexdigest() + '.json')
        if p.exists():
            current_before = read(p)
            assert current_before['raw_source_invariants'] == old[r['capture_path']]['raw_source_invariants']
            assert current_before['profession_summary'] == old[r['capture_path']]['profession_summary']
            check_pair(current_before, r)
            # Full physical tuples, excluding only skill/method labels.
            for key in ('raw_physical_rows', 'aggregated_physical_rows'):
                assert [x[:11] for x in current_before[key]] == [x[:11] for x in r[key]]
            fresh_pairs.append(r['capture_path'])
            del current_before
        # Release each capture's large physical lists before loading the next.
        r.pop('raw_physical_rows', None)
        r.pop('aggregated_physical_rows', None)
        reports.append(r)
    assert {r['capture_path'] for r in reports} == paths
    summary, details, metadata = _merge_reports(reports, previous['professions'])
    current = dict(professions=previous['professions'], capture_manifest=reports,
                   summary=summary, details=details, metadata=metadata,
                   successful_captures=36, failures=[],
                   analysis_signature=reports[0]['frozen_signature'],
                   validation_scope='36 fresh after replays; prior full36 baseline plus two fresh paired current-runtime controls')
    # Keep the original fixed35 denominator; the supplemental mechanical file
    # is included in physical acceptance, not quietly added to the coverage set.
    original = read(ROOT / 'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')
    fixed = {r['capture_path'] for r in original['capture_manifest']}
    assert len(fixed) == 35
    def subset(report):
        return dict(report, details=[], capture_manifest=[r for r in report['capture_manifest'] if r['capture_path'] in fixed])
    comparison = compare(subset(previous), subset(current))
    assert comparison['all_conserved'] and comparison['same_capture_set']
    assert len(comparison['jobs']) == 30
    totals = {}
    for side in ('before', 'after'):
        count = Counter()
        for row in comparison['jobs']:
            count.update({k: row[side][k] for k in ('deterministic_hits', 'named_inferred_hits', 'unknown_hits')})
        count['named_hits'] = count['deterministic_hits'] + count['named_inferred_hits']
        totals[side] = dict(count, confirmed_percent=100 * count['deterministic_hits'] / count['named_hits'])
    result = dict(full_after_replays=36, fresh_paired_controls=fresh_pairs,
        fixed_coverage_captures=35, physical_conserved=True, new_confirmed_hits=sum(x['confirmed_gain'] for x in changes),
        production_differences=changed, all_jobs_passed=comparison['jobs_meeting_named_target'] == 30,
        passed_jobs=comparison['jobs_meeting_named_target'], totals=totals, jobs=comparison['jobs'], changes=changes,
        evidence_scope='canonical resource-declared routing plus current exact entity-owner and physical record; NOT exact EX branch or m114 causal semantics',
        validation_scope=current['validation_scope'])
    (OUT / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    # The reusable compact manifest omits potentially large physical row lists.
    (OUT / 'current36_manifest.json').write_text(json.dumps(current, ensure_ascii=False, separators=(',', ':')), 'utf-8')
    lines = ['# v37 逐职业确认 Hit 验收', '',
        f"逐职业≥95%：{result['passed_jobs']}/30。全职业目标" + ('已完成。' if result['all_jobs_passed'] else '尚未完成。'), '',
        f"固定35份总体确认覆盖：{totals['before']['confirmed_percent']:.5f}% → {totals['after']['confirmed_percent']:.5f}%。",
        f"本次36份最新版本完整回放，新增确认{result['new_confirmed_hits']}次；原始与汇总物理指纹、真实Hit资格、未命名数量不变。", '',
        '确认覆盖率是证据覆盖，不是经人工真值标注的准确率。没有调整分母或把推断名称当证明。', '',
        '|职业|修改前|修改后|距95%还需确认Hit|', '|---|---:|---:|---:|']
    for row in comparison['jobs']:
        n = row['after']['deterministic_hits'] + row['after']['named_inferred_hits']
        need = max(0, math.ceil(n * .95) - row['after']['deterministic_hits'])
        lines.append(f"|{row['job']}|{row['before']['named_coverage_percent']:.2f}%|{row['after']['named_coverage_percent']:.2f}%|{need}|")
    lines += ['', '## 修改和证据边界', '',
        '仅调整岩浆墙EX实体32022的施放者技能归属：资源表正向关联4505→4410，声明调用图中可达创建者均为4410；该实体自身攻击仍属同一技能。未声称识别内部Clone分支或证明未公开引擎代码的全局完整性。',
        '不泛化到可受命令释放不同技能的召唤物。仅接纳已有名字、真实Hit、PvE、当代出生及重复主人字段相符的完整direct37，要求自身技能槽FFFFFFFF、上下文槽4410且无冲突。上下文只作一致性检查。', '',
        '## 验证范围', '',
        '最新候选完整回放36份，逐份对照上次全36验收；另外对0821硬核04和20260822YEP黑硬曜使用当前代码制作修改前/后两组完整回放，并逐条核对物理记录。这不等于本轮重新跑了72次。',
        '其余并行任务的代码保持原样。冻结前后除autonomous_attack_families.py外，还存在并行任务对ui/live_dps_page_scoring.py的治疗救急显示文字修改，已独立审阅并固定双方哈希，未回退该修改；回放为无界面数据审计，不调用该显示页面。所有非岩浆墙归因明细也必须完全一致。',
        '未打包、未上传评分、未运行全队评分或改动抓包文件。', '']
    (OUT / 'REPORT.md').write_text('\n'.join(lines), 'utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('jobs', 'changes')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
