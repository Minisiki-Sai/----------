"""Fixed-capture acceptance for compact entity selectors + scene evidence.

Keep cumulative changes distinct from isolated scene-evidence changes. Never
substitute pooled coverage for the required per-profession acceptance gate.
"""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_profession_attribution_audits import compare,markdown
from tools.audit_direct_damage_by_profession import _analysis_signature


def read(path):return json.loads(path.read_text('utf-8'))


def main():
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    after=read(out/'after_pve36/profession_direct_damage_report.json')
    assert not after['failures']
    assert after['analysis_signature']==_analysis_signature(),'Runtime changed during/after replay'
    original=read(ROOT/'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')
    paths={r['capture_path']for r in original['capture_manifest']}
    assert len(paths)==35
    results={}
    for name,before in [('final_cumulative35',original),('isolated_pve35',read(out/'after36/profession_direct_damage_report.json')),
                       ('final_isolated3',read(out/'before_three/profession_direct_damage_report.json'))]:
        assert not before['failures']
        selected={r['capture_path']for r in before['capture_manifest']}
        if name=='isolated_pve35':selected &= paths
        b={**before,'capture_manifest':[r for r in before['capture_manifest']if r['capture_path']in selected]}
        a={**after,'capture_manifest':[r for r in after['capture_manifest']if r['capture_path']in selected]}
        result=compare(b,a)
        assert result['same_capture_set']and result['all_conserved'],name
        assert result['captures_with_real_hit_evidence_changes']==0,name
        for r in result['jobs']:
            x,y=r['before'],r['after']
            assert x['total_direct_hits']==y['total_direct_hits'],r
            assert x['unknown_hits']==y['unknown_hits'],r
            assert x['deterministic_hits']+x['named_inferred_hits']==y['deterministic_hits']+y['named_inferred_hits'],r
            assert y['deterministic_hits']>=x['deterministic_hits'],r
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/(name+'.md')).write_text(markdown(result),encoding='utf-8')
        results[name]=result
    rows=results['final_cumulative35']['jobs']
    totals={side:Counter()for side in('before','after')}
    for r in rows:
        for side in totals:
            totals[side].update({k:r[side][k]for k in('deterministic_hits','named_inferred_hits','unknown_hits')})
    summary=dict(captures=35,second_jobs=30,named_hits=sum(totals['after'][k]for k in('deterministic_hits','named_inferred_hits')),
        before_confirmed=totals['before']['deterministic_hits'],after_confirmed=totals['after']['deterministic_hits'],
        per_job_passed=results['final_cumulative35']['jobs_meeting_named_target'],
        all_jobs_passed=results['final_cumulative35']['jobs_meeting_named_target']==30,
        before_percent=100*totals['before']['deterministic_hits']/sum(totals['before'][k]for k in('deterministic_hits','named_inferred_hits')),
        after_percent=100*totals['after']['deterministic_hits']/sum(totals['after'][k]for k in('deterministic_hits','named_inferred_hits')),
        changes=[dict(job=r['job'],before=r['before']['named_coverage_percent'],after=r['after']['named_coverage_percent'],
            added=r['after']['deterministic_hits']-r['before']['deterministic_hits'])for r in rows
            if r['after']['deterministic_hits']!=r['before']['deterministic_hits']])
    (out/'final_acceptance_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 已命名真实 Hit 的确定性证据覆盖复核','',
        f"固定35份、30个二转职业：{summary['before_percent']:.4f}% → {summary['after_percent']:.4f}%；逐职业达标 {summary['per_job_passed']}/30。",
        '',f"分母保持 {summary['named_hits']:,} Hit，确认数 {summary['before_confirmed']:,} → {summary['after_confirmed']:,}。",
        '', '**逐职业95%目标尚未全部完成。**'if not summary['all_jobs_passed']else'逐职业95%门槛已通过。',
        '', '此比例是原始包/客户端资源证据覆盖率，不是人工逐Hit标注后的正确率。未命名、附加、周期伤害的口径未改变。',
        '', '| 职业 | 此前完整基线 | 最新 | 新增确认Hit | 距95%至少还需 |','|---|---:|---:|---:|---:|']
    import math
    for r in rows:
        a,b=r['before'],r['after'];named=b['deterministic_hits']+b['named_inferred_hits']
        needed=max(0,math.ceil(named*.95)-b['deterministic_hits'])
        lines.append(f"|{r['job']}|{a['named_coverage_percent']:.2f}%|{b['named_coverage_percent']:.2f}%|{b['deterministic_hits']-a['deterministic_hits']}|{needed}|")
    lines +=['','## 已落地的修复','',
        '- 短实体09记录的ACT动作/信号索引：重磅鸭喷射与暗影分身落地弹道；需完整出生/主人/对象/原始伤害闭环。',
        '- 缺初始地图包时，使用同连接、同场景代次的原始正式Boss出生与新鲜目标出生，补独立PvE场景证据。',
        '- 重传不能续命或跨场景激活；对象复用、主人变化、模板冲突、未知正数自身技能编号仍拒绝。',
        '- 不生成额外技能施放，不修改原始伤害或评分公式。',
        '', '## 口径与守恒','',
        '累计35份的基线包含此前共享子箭改进（魔羽增量）；`isolated_pve35`单独隔离本次场景证据变化，`final_isolated3`为本轮两项新修复的3份配对基线。',
        '每份原始和聚合伤害的时间、来源、目标、金额、暴击、对象及结果组多重集指纹保持一致；真实命中标志、各职业已命名分母和未命名数量保持一致。',
        '', '## 尚未认证的分支','',
        '- 共享模板不能仅凭旧技能名确定技能；缺当前父对象/动作根的伤害仍保留推断。',
        '- 32022岩浆墙EX的Clone创建入口仍缺独立资源关联；不把整模板直接标为确认。',
        '- 分身自身技能262494与完整09对象链不是同一批结果；未放宽未知自身技能槽。',
        '- 对部分缺失对象的TCP检查无已解记录匹配，但存在未解记录，不能宣称原包不存在关联。',
        '', '详细逐包守恒及逐职业数据见同目录 final_cumulative35.json / isolated_pve35.json。']
    (out/'RESULT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
