"""Acceptance for exact entity skill IDs, with separate cumulative baselines.

No replay, reclassification, denominator changes or synthetic confirmations.
Writes a new report set and preserves the previous scene-evidence acceptance.
"""
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.compare_profession_attribution_audits import compare, markdown
from tools.audit_direct_damage_by_profession import _analysis_signature


def read(path):
    return json.loads(path.read_text('utf-8'))


def main():
    out = ROOT / 'outputs/compact_entity_selectors_20260908'
    after = read(out / 'after_identity36/profession_direct_damage_report.json')
    assert not after['failures'] and after['successful_captures'] == 36
    assert after['analysis_signature'] == _analysis_signature(), 'Runtime changed after replay'
    original = read(ROOT / 'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')
    fixed35 = {r['capture_path'] for r in original['capture_manifest']}
    assert len(fixed35) == 35
    comparisons = {}
    for name, before, selected in (
        ('identity_cumulative35', original, fixed35),
        ('identity_isolated35', read(out / 'after_pve36/profession_direct_damage_report.json'), fixed35),
        ('identity_isolated36', read(out / 'after_pve36/profession_direct_damage_report.json'),
         {r['capture_path'] for r in after['capture_manifest']}),
    ):
        assert not before['failures']
        b = {**before, 'capture_manifest': [r for r in before['capture_manifest'] if r['capture_path'] in selected]}
        a = {**after, 'capture_manifest': [r for r in after['capture_manifest'] if r['capture_path'] in selected]}
        # Route totals in the full report are not silently applied to a subset.
        a['details'] = []
        result = compare(b, a)
        assert result['same_capture_set'] and result['all_conserved'], name
        assert result['captures_with_real_hit_evidence_changes'] == 0, name
        for r in result['jobs']:
            old, new = r['before'], r['after']
            assert old['total_direct_hits'] == new['total_direct_hits'], r
            assert old['unknown_hits'] == new['unknown_hits'], r
            assert old['deterministic_hits'] + old['named_inferred_hits'] == new['deterministic_hits'] + new['named_inferred_hits'], r
            assert new['deterministic_hits'] >= old['deterministic_hits'], r
        (out / (name + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / (name + '.md')).write_text(markdown(result), encoding='utf-8')
        comparisons[name] = result
    result = comparisons['identity_cumulative35']
    totals = {side: Counter() for side in ('before', 'after')}
    for row in result['jobs']:
        for side in totals:
            totals[side].update({k: row[side][k] for k in ('deterministic_hits', 'named_inferred_hits', 'unknown_hits')})
    named = sum(totals['after'][k] for k in ('deterministic_hits', 'named_inferred_hits'))
    summary = dict(captures=35, second_jobs=30, named_hits=named,
        before_confirmed=totals['before']['deterministic_hits'], after_confirmed=totals['after']['deterministic_hits'],
        before_percent=100 * totals['before']['deterministic_hits'] / named,
        after_percent=100 * totals['after']['deterministic_hits'] / named,
        per_job_passed=result['jobs_meeting_named_target'], all_jobs_passed=result['jobs_meeting_named_target'] == 30,
        isolated_confirmations=sum(r['after']['deterministic_hits'] - r['before']['deterministic_hits']
                                  for r in comparisons['identity_isolated35']['jobs']),
        unknown_hits=totals['after']['unknown_hits'], analysis_signature=after['analysis_signature']['sha256'])
    (out / 'identity_acceptance_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 已命名真实 Hit 确认覆盖率：最新验收', '',
        f"固定35份、30个二转职业：{summary['before_percent']:.5f}% → {summary['after_percent']:.5f}%。",
        f"已命名分母 {named:,} 不变；确认 {summary['before_confirmed']:,} → {summary['after_confirmed']:,}；未命名 {summary['unknown_hits']:,} 另列且不变。", '',
        f"逐职业≥95%：{summary['per_job_passed']}/30。",
        '**尚未完成全部职业95%的目标。**' if not summary['all_jobs_passed'] else '全部职业通过95%门槛。', '',
        '统计的是原始记录与客户端资源支持的确定性证据覆盖，不是人工标注准确率；其中包含对象链、实体自身技能 ID、直接技能字段等路线，并非全部都是 Projectile 链。', '',
        '|职业|完整旧基线|最新|累计新增确认Hit|距95%至少还需|', '|---|---:|---:|---:|---:|']
    for r in result['jobs']:
        a, b = r['before'], r['after']
        count = b['deterministic_hits'] + b['named_inferred_hits']
        lines.append(f"|{r['job']}|{a['named_coverage_percent']:.2f}%|{b['named_coverage_percent']:.2f}%|{b['deterministic_hits']-a['deterministic_hits']}|{max(0, math.ceil(count*.95)-b['deterministic_hits'])}|")
    lines += ['', '## 本轮落地', '',
        '- 解析短实体09记录中的ACT动作/信号索引，严格连接同实体、同代次的对象和伤害；补重磅鸭喷射、暗影分身落地弹道。',
        '- 初始地图证据缺失时，用同连接、同场景代次的原始正式Boss出生和新鲜目标出生补独立PvE模式证明；不会改变地图显示、自动起停或战斗起点。',
        '- 原始自身技能槽明确为262494，且当前实体确认为33608时，沿安装技能表、AI调用和DNT唯一玩家显示名键确认6319暗影召唤术。**不宣称已解出复制的6309/6706，也不生成玩家施放。**',
        '- 保留出生/主人/退役/重传/对象复用/职业冲突检查；未用m114上下文或旧标签给未知攻击兜底。', '',
        '## 配对回放与范围', '',
        '最新候选完整回放36份。主表固定使用旧基线35份；额外0826硬核曜只进入36份独立验证，不混入主表分母。',
        f"最后一项明确技能ID修复单独新增 {summary['isolated_confirmations']:,} 次确认（固定35份）。累计增量另包含之前的魔羽共享子箭修复。",
        '逐份原始、聚合记录的时间、来源、目标、金额、暴击、结果组和对象多重集指纹全部一致，真实命中标记也一致；没有新增/删除物理伤害。各职业已命名分母和未知数不变。', '',
        '## 仍存在的缺口', '',
        '- 部分共享弹道未找到当前父对象或独立动作根，不能靠最近技能或旧名称确认。',
        '- 牧师等部分直接Hit尚未找到当前对象注册；短窗口和已解TCP中的无匹配不等于抓包绝对没有关联。',
        '- 召唤实体自身技能槽为空时，需要其自身动作/对象选择；主人明确不代表攻击技能已明确。',
        '- 32022岩浆墙EX的Clone创建入口尚缺独立关联，不将整个模板强行升级。',
        '- 离线解出的111字节TCP候选族在两份抓包可读到角色名及变化数值，但尚未确认技能/对象关系，**新增确认Hit为0，未修改生产密钥注册表**。', '',
        '详细数据：identity_cumulative35.json（累计）、identity_isolated35.json（最后一项隔离）、identity_isolated36.json（36份隔离）。此前阶段报告保留，勿混合不同基线。']
    (out / 'LATEST_RESULT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
