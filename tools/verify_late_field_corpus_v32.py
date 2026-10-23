"""Validate post-admission-only object metadata in fresh full 36 replays."""
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_profession_attribution_audits import compare,markdown
from tools.verify_kali_increment_v30 import hashes
OUT=ROOT/'outputs/compact_entity_selectors_20260908'


def read(path):return json.loads(path.read_text('utf-8'))


def check(before,after):
    result=compare(before,after)
    assert result['same_capture_set'] and result['all_conserved']
    assert result['captures_with_real_hit_evidence_changes']==0
    old={r['capture_path']:r for r in before['capture_manifest']}
    changes=[]
    for a in after['capture_manifest']:
        b=old[a['capture_path']]
        assert a['decoded_events']==b['decoded_events']
        for key in ('raw_source_invariants','aggregated_invariants'):
            assert a[key].get('hit_evidence_sha256') and b[key].get('hit_evidence_sha256')
            assert a[key]==b[key],(a['capture_path'],key)
        assert a['profession_summary'].keys()==b['profession_summary'].keys()
        for job,v in a['profession_summary'].items():
            u=b['profession_summary'][job]
            for key in ('total_direct_hits','total_direct_damage','unattributed_hits','unattributed_damage'):
                assert u.get(key,0)==v.get(key,0),(a['capture_path'],job,key)
            if u!=v:
                assert job in {'影','黑暗萨满','噬魂者'},job
                assert v.get('deterministic_hits',0)>=u.get('deterministic_hits',0)
                assert v.get('unattributed_hits',0)<=u.get('unattributed_hits',0)
                changes.append(dict(capture=a['capture_path'],job=job,
                    confirmed_gain=v.get('deterministic_hits',0)-u.get('deterministic_hits',0),
                    newly_named_hits=u.get('unattributed_hits',0)-v.get('unattributed_hits',0)))
    return result,changes


def main():
    before=read(OUT/'composed36_after_kali.json')
    after=read(OUT/'after_late_field36_final/profession_direct_damage_report.json')
    assert after['successful_captures']==36 and not after['failures']
    h0=hashes(OUT/'kali_parent_frozen_after')
    h1=hashes(OUT/'late_field_final')
    differences={k for k in h0.keys()|h1.keys()if h0.get(k)!=h1.get(k)}
    assert differences=={f'dragon_nest_builder/live_dps/{s}' for s in
                         ('damage_object_origins.py','late_field_hit_evidence_v32.py','v576/core_decoder.py')},differences
    for filename,digest in after['analysis_signature']['files'].items():
        assert sha256((OUT/'late_field_final'/filename).read_bytes()).hexdigest()==digest
    result,changes=check(before,after)
    proof=dict(full_replays=36,failures=0,data_identical=True,
               runtime_differences=sorted(differences),physical_conserved=True,
               real_hit_flags_unchanged=True,new_confirmed_hits=sum(r['confirmed_gain']for r in changes),
               newly_named_hits=sum(r['newly_named_hits']for r in changes),changes=changes,
               before_signature=before['analysis_signature'],after_signature=after['analysis_signature'])
    (OUT/'late_field_acceptance.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'late_field36_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'late_field36_comparison.md').write_text(markdown(result),encoding='utf-8')
    print(json.dumps({k:v for k,v in proof.items()if k not in('changes','before_signature','after_signature')},ensure_ascii=False))
    print([(r['job'],r['before']['named_coverage_percent'],r['after']['named_coverage_percent'])
           for r in result['jobs']if r['before']!=r['after']])


if __name__=='__main__':main()
