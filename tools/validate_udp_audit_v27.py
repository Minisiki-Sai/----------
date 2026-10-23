"""Validate final audit inputs and full event equality to individually reviewed v3."""
from pathlib import Path
import gzip
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'outputs/udp_integrity_v27_20260908'
CASES=('0808竞速最佳','0817黑典噬魂','0830星期天','0831黑硬曜')


def digest(path, compressed=False):
    h=hashlib.sha256()
    opener=gzip.open if compressed else open
    with opener(path,'rb') as stream:
        while block:=stream.read(1024*1024):h.update(block)
    return h.hexdigest()


def main():
    snapshot=AUDIT/'candidate_final_v4'
    manifest=json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
    for rel, expected in manifest['files'].items():
        if digest(snapshot/rel)!=expected or digest(ROOT/rel)!=expected:
            raise ValueError('Runtime drift: '+rel)
    rows=[]
    for case in CASES:
        v3=json.loads((AUDIT/(case+'_final_v3.json')).read_text(encoding='utf-8'))
        v4=json.loads((AUDIT/(case+'_final_v4.json')).read_text(encoding='utf-8'))
        assert v3['completed'] and v4['completed']
        assert v3['capture_sha256']==v4['capture_sha256']==digest(Path(v4['capture']))
        assert v4['manifest_sha256']==digest(snapshot/'manifest.json')
        for version in (v3,v4):
            assert digest(Path(version['event_details']['path']))==version['event_details']['sha256']
        before_hash=digest(Path(v3['event_details']['path']),True)
        final_hash=digest(Path(v4['event_details']['path']),True)
        assert before_hash==final_hash,(case,'event differences require a fresh individual review')
        for field in ('raw_decoder','normalized','aggregate','counters','owner_refinements','owner_changes','independent_inventory'):
            assert v3[field]==v4[field],(case,field)
        causal_path=ROOT/'outputs/v27_owner_integrity_review_20260908/final_summary_delta_01/final_v3_checked'/(case+'_causal.json')
        causal=json.loads(causal_path.read_text(encoding='utf-8'))
        assert not causal['missing_final_physical_records']
        assert not causal['unverified_raw_removals']
        assert not causal['critical_errors']
        rows.append({'capture':case,'capture_sha256':v4['capture_sha256'],
            'full_uncompressed_event_stream_sha256':final_hash,
            'v3_v4_every_event_field_equal':True,'causal_review':str(causal_path),
            'causal_review_sha256':digest(causal_path),
            'causal_summary':causal['summary'],
            'real_hit_flag_changes':len(causal['real_flag_changes']),
            'remaining_hidden_owner_groups':causal['hidden_groups']})
    result={'scope':'Four captures only; not a claim of complete UDP decoding or 100% player/skill ownership',
        'snapshot':str(snapshot),'manifest_sha256':digest(snapshot/'manifest.json'),
        'workspace_matches_frozen_runtime':True,'captures':rows,
        'tests':{'focused_passed':1959,'focused_subtests_passed':111,'known_unfixed_xfail':7,
                 'legacy_live_plus_cache_passed':499,'pre_existing_failures':10},
        'limitations':['Unbound raw-source records remain unassigned in player aggregate.',
                       'Unknown/unverified envelopes are not assumed to be damage.',
                       'Real-hit classification and downstream skill inference are not guaranteed unchanged.']}
    with (AUDIT/'VALIDATION_FINAL_V4.json').open('x',encoding='utf-8') as out:
        json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps({'validated':True,'captures':len(rows),'remaining_owner_groups':[r['remaining_hidden_owner_groups'] for r in rows]},ensure_ascii=False))


if __name__=='__main__':main()
