"""Compare completed matched captures; never splice candidate checkpoints."""
from collections import Counter
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_profession_attribution_audits import compare


def main():
    out=ROOT/'outputs/named_hit_chain_followup_20260908'
    def read(path):return json.loads(path.read_text(encoding='utf-8'))
    old=read(ROOT/'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')
    extra=read(out/'before_extra/profession_direct_damage_report.json')
    new=read(out/'after_framed_four/profession_direct_damage_report.json')
    assert not extra['failures'] and not new['failures']
    assert extra['analysis_signature']['sha256']==old['analysis_signature']['sha256']
    index={r['capture_path']:r for r in old['capture_manifest']}
    for row in extra['capture_manifest']:
        assert row['capture_path'] not in index
        index[row['capture_path']]=row
    old={**old,'capture_manifest':[index[r['capture_path']]for r in new['capture_manifest']]}
    diff=compare(old,new)
    assert diff['same_capture_set'] and diff['all_conserved']
    assert diff['captures_with_real_hit_evidence_changes']==0
    changes=[]
    for row in new['capture_manifest']:
        before=index[row['capture_path']]
        assert before['capture_sha256']==row['capture_sha256']
        for job,after in row['profession_summary'].items():
            prior=before['profession_summary'][job]
            for key in ('total_direct_hits','unattributed_hits'):
                assert prior.get(key,0)==after.get(key,0),(row['capture_path'],job,key)
            if prior.get('deterministic_hits',0)!=after.get('deterministic_hits',0):
                n=prior['deterministic_hits']+prior['nondeterministic_hits']
                assert n==after['deterministic_hits']+after['nondeterministic_hits']
                changes.append(dict(capture=Path(row['capture_path']).name,job=job,named_hits=n,
                    before=prior['deterministic_hits'],after=after['deterministic_hits'],
                    added=after['deterministic_hits']-prior['deterministic_hits'],
                    before_percent=100*prior['deterministic_hits']/n,
                    after_percent=100*after['deterministic_hits']/n))
    previous=old['analysis_signature']['files'];current=new['analysis_signature']['files']
    changed_inputs=[k for k in previous.keys()|current.keys()if previous.get(k)!=current.get(k)]
    assert changed_inputs==['dragon_nest_builder/live_dps/protocol_v576.py'],changed_inputs
    result=dict(scope='four matched full capture replays, not the complete 30-job corpus',
        coverage='deterministic evidence coverage, not measured ground-truth accuracy',
        baseline_sources=['after_v2_35 (three matching captures)','before_extra (one capture, identical frozen signature)'],
        changed_inputs=changed_inputs,changed_jobs_per_capture=changes,comparison=diff)
    (out/'comparison_framed_four.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(changes=changes,added=sum(r['added']for r in changes),
        physical=diff['matched_capture_physical_totals']),ensure_ascii=False))


if __name__=='__main__':main()
