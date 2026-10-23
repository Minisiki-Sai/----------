"""Publish only after full physical reconciliation, never a pooled pass claim."""
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.compare_profession_attribution_audits import compare

OUT = ROOT / 'outputs/compact_entity_selectors_20260908'


def read(path):
    return json.loads(path.read_text('utf-8'))


def subset(report, paths):
    return {**report, 'details': [], 'capture_manifest': [r for r in report['capture_manifest']
                                                       if r['capture_path'] in paths]}


def main():
    physical = read(OUT / 'self_target_corpus_acceptance.json')
    assert physical['all_accepted'] and physical['captures'] == 36
    assert len(physical['paired']) + len(physical['identical_physical_hashes']) == 36
    for r in physical['paired']:
        assert r['all_changes_proven']
        assert all(not v['unexplained_count'] and v['unique_keys_identical']
                   for v in r['checks'].values())
    kali = read(OUT / 'kali_increment_acceptance.json')
    assert kali['all_affected_captures_physically_conserved']
    old = read(ROOT / 'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')
    stage4 = read(OUT / 'after_rake36_frozen/profession_direct_damage_report.json')
    stage5 = read(OUT / 'after_self_target36_frozen/profession_direct_damage_report.json')
    current = read(OUT / 'composed36_after_kali.json')
    assert physical['runtime']['before'] == stage4['analysis_signature']['sha256']
    assert physical['runtime']['after'] == stage5['analysis_signature']['sha256']
    assert current['analysis_signature'] == kali['increment_signature']
    fixed = {r['capture_path'] for r in old['capture_manifest']}
    assert len(fixed) == 35
    cumulative = compare(subset(old, fixed), subset(current, fixed))
    recent = compare(subset(stage4, fixed), subset(current, fixed))
    for result in (cumulative, recent):
        assert result['same_capture_set'] and len(result['jobs']) == 30
        assert all(r['same_capture_bytes'] for r in result['capture_checks'])
    totals = {}
    for side in ('before', 'after'):
        c = Counter()
        for row in cumulative['jobs']:
            c.update({k: row[side][k] for k in ('deterministic_hits', 'named_inferred_hits', 'unknown_hits')})
        c['named_hits'] = c['deterministic_hits'] + c['named_inferred_hits']
        totals[side] = dict(c) | {'confirmed_percent': 100*c['deterministic_hits']/c['named_hits']}
    removed = {}
    for field in ('raw_physical_rows', 'aggregated_physical_rows'):
        values = [r['checks'][field] for r in physical['paired']]
        removed[field] = dict(rows=sum(r['duplicates_removed'] for r in values),
            damage=sum(r['duplicate_damage_removed'] for r in values),
            largest_single_capture_damage_change_percent=max(abs(r['damage_change_percent']) for r in values),
            unique_target_corrections=sum(r['unique_target_corrections'] for r in values))
    summary = dict(scope='same fixed 35 captures, 30 second jobs; impact-bounded latest increment',
        per_job_passed=cumulative['jobs_meeting_named_target'],
        all_jobs_passed=cumulative['jobs_meeting_named_target'] == 30,
        totals=totals, duplicate_removal_full36=removed,
        kali_new_confirmed_hits=kali['new_confirmed_hits'],
        latest_validation_scope=current['validation_scope'],
        jobs=cumulative['jobs'], recent_jobs=recent['jobs'])
    (OUT / 'confirmed_coverage_v30_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    a, b = totals['before'], totals['after']
    rows = ['# 已命名真实 Hit 确认覆盖：最新验收', '',
        f"固定35份、30个二转职业，总体确认覆盖率：{a['confirmed_percent']:.5f}% → {b['confirmed_percent']:.5f}%。",
        f"逐职业≥95%：{summary['per_job_passed']}/30。**全职业95%目标尚未完成。**", '',
        '确认覆盖率不等于有人工真值标签的准确率。仍按真实命中证据、完整对象链、实体自身技能字段等分路线审计；未确认记录保留，不能改成确认来凑比例。', '',
        f"已命名分母 {a['named_hits']:,} → {b['named_hits']:,}，确认 {a['deterministic_hits']:,} → {b['deterministic_hits']:,}；未命名 {a['unknown_hits']:,} → {b['unknown_hits']:,} 另列。",
        '本轮包含用户授权的已证实重复伤害修复，分母不再固定；下表的净确认变化包含重复项移除，不能当作纯新增证据数量。', '',
        '|职业|旧基线|最新确认覆盖|上次已验收覆盖|最新已命名Hit|仍靠推断Hit|达到95%至少再确认Hit|',
        '|---|---:|---:|---:|---:|---:|---:|']
    last = {r['job']: r['before'] for r in recent['jobs']}
    for row in cumulative['jobs']:
        before, after = row['before'], row['after']
        named = after['deterministic_hits'] + after['named_inferred_hits']
        need = max(0, math.ceil(named*.95)-after['deterministic_hits'])
        rows.append(f"|{row['job']}|{before['named_coverage_percent']:.2f}%|{after['named_coverage_percent']:.2f}%|{last[row['job']]['named_coverage_percent']:.2f}%|{named}|{after['named_inferred_hits']}|{need}|")
    raw, agg = removed['raw_physical_rows'], removed['aggregated_physical_rows']
    rows += ['', '## 本轮实际修复', '',
        '- 短实体混合包：不再拿外层09记录里的实体ID当后续08伤害的目标。用完整记录边界和原始字段纠正目标，再由已有去重消除同一物理伤害的双计。',
        '- 重磅鸭符文攻击：仅对实体自身明确的261102–261117技能槽，结合AI、DNT和ACT三段闭合证据补充归属；FFFFFFFF、缺失loop的261101不套用。机械大师覆盖提升包含新证据和重复修复两部分。',
        f"- 噬魂者11076子对象：补充完整父ID、主人、动作/信号和当前代次的窄格式解析。3份完整回放净新增{kali['new_confirmed_hits']}次确认，原始及汇总物理伤害、真实命中标记和未命名数量均不变。0817的小窗口正例在完整回放未增加确认，不算收益。", '',
        '## 伤害完整性核验', '',
        '36份前后完整回放：14份原始及汇总物理指纹完全一致；22份逐条重放、逐条核对。所有唯一物理伤害键保留，每个被删重复都有对应完整原始包证据及保留行。',
        f"原始事件去除重复{raw['rows']}条 / {raw['damage']:,}伤害；汇总事件去除重复{agg['rows']}条 / {agg['damage']:,}伤害。两层不能相加。",
        f"单份抓包汇总伤害最大变化绝对值{agg['largest_single_capture_damage_change_percent']:.6f}%，没有未解释的物理变化。",
        '明细证据：self_target_corpus_acceptance.json 及 self_target_pair/*_comparison.json。', '',
        '## 验证版本边界', '',
        '第二阶段前后使用固定代码副本和完全相同的数据，36份均完整回放。新增11076后只对可能受影响的3份再完整回放；另33份由全文件必要字节扫描排除新格式候选，继承前一阶段结果。这里不是宣称新版本又完整跑了36份。',
        '最新组合表：composed36_after_kali.json；增量核验：kali_increment_acceptance.json。全技能明细仍以after_self_target36_frozen明细加3份after_kali原始回放明细为准，未伪造合并明细。',
        '旧35份累计基线与本轮跨过其他任务的治疗模型数据更新，因此累计差异不是解析器单变量实验；上述本轮A/B则保持数据一致。', '',
        '## 仍有缺口的原因', '',
        '- 岩浆墙EX：能解出正常EX根动作，但具体32022出生尚无独立创建选择键；ACT中的Clone入口未闭合，不能直接将整个模板升为确认。',
        '- 掌握/痊愈等圣物：部分伤害自身技能槽为FFFFFFFF，而实体不止一种可能的伤害动作；不能用创建技能或最近动作补成确定证据。',
        '- 其余玩家直接攻击：部分缺完整对象注册或同代父链。出现特效的推测是调查方向，不能替代抓包中的关联字段。', '',
        '不改UI、不打包、不上传评分，不把当前进度写成全职业完成。', '']
    (OUT / 'RUNTIME_RESULT.md').write_text('\n'.join(rows), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k not in ('jobs', 'recent_jobs')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
