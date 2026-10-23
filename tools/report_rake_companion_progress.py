"""Strict acceptance of a copied, frozen parser build; never mix live data."""
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_profession_attribution_audits import compare,markdown


def read(path):return json.loads(path.read_text('utf-8'))


def verified(report,runtime):
    assert not report['failures']
    for name,expected in report['analysis_signature']['files'].items():
        assert sha256((runtime/name).read_bytes()).hexdigest()==expected,(runtime,name)


def subset(report,paths):
    return {**report,'details':[],
        'capture_manifest':[r for r in report['capture_manifest']if r['capture_path']in paths]}


def checked(before,after,paths):
    r=compare(subset(before,paths),subset(after,paths))
    assert r['same_capture_set']and r['all_conserved']
    assert r['captures_with_real_hit_evidence_changes']==0
    for row in r['jobs']:
        a,b=row['before'],row['after']
        assert a['total_direct_hits']==b['total_direct_hits'],row
        assert a['unknown_hits']==b['unknown_hits'],row
        assert a['deterministic_hits']+a['named_inferred_hits']==b['deterministic_hits']+b['named_inferred_hits'],row
        assert b['deterministic_hits']>=a['deterministic_hits'],row
    return r


def main():
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    after=read(out/'after_rake36_frozen/profession_direct_damage_report.json')
    before3=read(out/'before_rake_three_frozen/profession_direct_damage_report.json')
    verified(after,out/'rake_frozen_runtime/after')
    verified(before3,out/'rake_frozen_runtime/before')
    assert after['successful_captures']==36 and before3['successful_captures']==3
    diff=[k for k,v in before3['analysis_signature']['files'].items()if v!=after['analysis_signature']['files'].get(k)]
    assert diff==['dragon_nest_builder/live_dps/protocol_v576.py'],diff
    original=read(ROOT/'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')
    stage3=read(out/'after_identity36/profession_direct_damage_report.json')
    fixed35={r['capture_path']for r in original['capture_manifest']}
    comparisons={
        'rake_companion_isolated3':checked(before3,after,{r['capture_path']for r in before3['capture_manifest']}),
        'rake_companion_stage3_35':checked(stage3,after,fixed35),
        'rake_companion_cumulative35':checked(original,after,fixed35)}
    data_changes=[k for k,v in stage3['analysis_signature']['files'].items()
        if k.startswith('dragon_nest_builder/data/')and v!=after['analysis_signature']['files'].get(k)]
    for name,result in comparisons.items():
        result['paired_parser_only']=name.endswith('isolated3')
        result['concurrent_data_changes_from_stage3']=data_changes
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/(name+'.md')).write_text(markdown(result),encoding='utf-8')
    result=comparisons['rake_companion_cumulative35']
    totals={side:Counter()for side in ('before','after')}
    for r in result['jobs']:
        for side in totals:totals[side].update({k:r[side][k]for k in ('deterministic_hits','named_inferred_hits','unknown_hits')})
    named=totals['after']['deterministic_hits']+totals['after']['named_inferred_hits']
    summary=dict(captures=35,second_jobs=30,named_hits=named,
        before_confirmed=totals['before']['deterministic_hits'],after_confirmed=totals['after']['deterministic_hits'],
        before_percent=100*totals['before']['deterministic_hits']/named,
        after_percent=100*totals['after']['deterministic_hits']/named,
        per_job_passed=result['jobs_meeting_named_target'],all_jobs_passed=result['jobs_meeting_named_target']==30,
        isolated_confirmations=sum(r['after']['deterministic_hits']-r['before']['deterministic_hits']for r in comparisons['rake_companion_isolated3']['jobs']),
        unknown_hits=totals['after']['unknown_hits'],analysis_signature=after['analysis_signature']['sha256'],
        data_changes_from_stage3=data_changes)
    (out/'rake_companion_acceptance_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=['# 已命名真实 Hit 确认覆盖：固定版本验收','',
        f"固定35份、30职业：{summary['before_percent']:.5f}% → {summary['after_percent']:.5f}%。",
        f"分母 {named:,} 不变，确认 {summary['before_confirmed']:,} → {summary['after_confirmed']:,}，未命名 {summary['unknown_hits']:,} 另列且不变。",
        f"逐职业≥95%：{summary['per_job_passed']}/30。",
        '**全部职业95%目标尚未完成。**'if not summary['all_jobs_passed']else'全部职业达到95%证据覆盖。','',
        '这是可追溯证据链的覆盖率，不是人工标注后的准确率；完整对象链、实体技能ID和直接技能字段均分路线保存。','',
        '|职业|旧基线|本次|累计新增确认Hit|距95%仍缺Hit|','|---|---:|---:|---:|---:|']
    for r in result['jobs']:
        a,b=r['before'],r['after'];n=b['deterministic_hits']+b['named_inferred_hits']
        rows.append(f"|{r['job']}|{a['named_coverage_percent']:.2f}%|{b['named_coverage_percent']:.2f}%|{b['deterministic_hits']-a['deterministic_hits']}|{max(0,math.ceil(n*.95)-b['deterministic_hits'])}|")
    rows+=['','## 本次新增的原始字段解析','',
        '- 九齿钉耙5711的09/6f、09/73 branch3记录：完整父对象ID、三处主人一致、完整记录边界；父模板5707/5715有独立ACT子弹道声明，且必须保留当前动作根。',
        '- 天降雨点5709/5737混合信封：补齐已观察的伴随记录长度，完整父ID、同主人/代次和真实动作要求不变。保留1个非法父字段原帧为拒绝例。',
        '- 3份抓包各含两种弹道的完整原帧链正例；去动作、去父、错主、同低16位但不同完整父ID等仍不能确认。',
        f"只有解析器不同、数据完全相同的3份隔离回放新增 {summary['isolated_confirmations']} 次确认。",'',
        '## 版本与守恒边界','',
        '主线期间其他任务修改了治疗模型数据。保留该改动，重新复制代码和全部数据为固定的前后副本；隔离3份只差protocol_v576.py。',
        '36份结果来自固定after副本；35份累计表与旧基线跨过该治疗数据变化，因此不把累计对比冒称为解析器单变量实验。',
        '逐份原始、聚合伤害的时间/来源/目标/金额/暴击/结果组/对象ID多重集指纹和真实命中标记均不变。未增加伤害、未删除伤害、未改变分母。',
        '旧after_rake_three目录因运行中数据版本变化被拒绝，未纳入上述结果。',
        '完整路线与逐技能明细：after_rake36_frozen/profession_direct_damage_report.json；固定35对比和隔离3对比见rake_companion_*.json。']
    (out/'RUNTIME_RESULT.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
