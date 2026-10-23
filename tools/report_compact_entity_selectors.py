"""Matched full-replay acceptance. Keep 35-file cumulative and 3-file A/B separate."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_profession_attribution_audits import compare,markdown
from tools.audit_direct_damage_by_profession import _analysis_signature

def read(p):return json.loads(p.read_text('utf-8'))


def main():
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    after=read(out/'after36/profession_direct_damage_report.json')
    assert not after['failures']
    assert after['analysis_signature']==_analysis_signature(),'Source changed during/after replay'
    comparisons={}
    for name,path in [('cumulative35',ROOT/'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json'),
                      ('isolated3',out/'before_three/profession_direct_damage_report.json')]:
        before=read(path);assert not before['failures']
        index={r['capture_path']:r for r in before['capture_manifest']}
        matched={**after,'capture_manifest':[r for r in after['capture_manifest']if r['capture_path']in index]}
        result=compare(before,matched)
        assert result['same_capture_set']and result['all_conserved'],name
        assert result['captures_with_real_hit_evidence_changes']==0,name
        for row in result['jobs']:
            a,b=row['before'],row['after']
            assert a['total_direct_hits']==b['total_direct_hits'],row
            assert a['unknown_hits']==b['unknown_hits'],row
            assert a['deterministic_hits']+a['named_inferred_hits']==b['deterministic_hits']+b['named_inferred_hits'],row
        result['scope']=('35 fixed captures, cumulative previous shared-arrow plus current entity-selector work'
            if name=='cumulative35'else'3 matched captures before/after only current entity-selector work')
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/(name+'.md')).write_text(markdown(result),encoding='utf-8')
        comparisons[name]=result
    main_result=comparisons['cumulative35'];rows=main_result['jobs']
    named=sum(r['after']['deterministic_hits']+r['after']['named_inferred_hits']for r in rows)
    exact=sum(r['after']['deterministic_hits']for r in rows)
    old_exact=sum(r['before']['deterministic_hits']for r in rows)
    changed=[dict(job=r['job'],before=r['before']['named_coverage_percent'],after=r['after']['named_coverage_percent'],
        added=r['after']['deterministic_hits']-r['before']['deterministic_hits'])for r in rows
        if r['after']['deterministic_hits']!=r['before']['deterministic_hits']]
    base_paths={Path(c['capture_path']).name for c in after['capture_manifest'] if c['capture_path']in
        {x['capture_path']for x in read(ROOT/'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')['capture_manifest']}}
    # Per-capture details are raw dictionaries; the global export is localized.
    fresh=Counter()
    for cp in (out/'after36/profession_direct_damage_checkpoints').glob('*.json'):
        capture=read(cp)['report']
        if capture['capture']not in base_paths:continue
        for row in capture['details']:
            if row['method']=='packet_entity_projectile_selector':fresh[row['job']]+=row['hits']
    summary=dict(captures=35,jobs=30,named_hits=named,confirmed_hits=exact,previous_confirmed_hits=old_exact,
        coverage_percent=100*exact/named,previous_coverage_percent=100*old_exact/named,
        jobs_meeting_95=main_result['jobs_meeting_named_target'],all_jobs_met=main_result['jobs_meeting_named_target']==30,
        cumulative_changes=changed,current_selector_hits_by_job=dict(fresh),
        isolated3_changes=[dict(job=r['job'],added=r['after']['deterministic_hits']-r['before']['deterministic_hits'])
            for r in comparisons['isolated3']['jobs']if r['after']['deterministic_hits']!=r['before']['deterministic_hits']])
    (out/'acceptance_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
