"""Bounded cached-payload microbenchmark; no replay and no runtime writes."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/audit_remaining_registration_server_parent_v22_20260907'


def main():
    saved=json.loads((ROOT/'outputs/audit_remaining_registration_3021_context_20260907/scan_checkpoint.json').read_text(encoding='utf-8'))
    packets=[bytes.fromhex(r['payload_hex']) for r in saved['packets'].values()]
    modules={};hashes={}
    for version in ('v21','v22'):
        p=ROOT/f'outputs/damage_attribution_candidate_{version}_20260907/protocol_v576.py'
        hashes[version]=hashlib.sha256(p.read_bytes()).hexdigest()
        spec=importlib.util.spec_from_file_location(f'perf_protocol_{version}',p)
        m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);modules[version]=m
    results={v:[] for v in modules}
    # Alternate execution order to reduce first-run/order bias.
    for repeat in range(5):
        for version in (('v21','v22') if repeat%2==0 else ('v22','v21')):
            m=modules[version];m._decode_v576_body_cached.cache_clear()
            start=time.perf_counter()
            rows=sum(len(m.decode_v576_server_damage_object_registrations(p,None,(3021,),include_unmapped=True)) for p in packets)
            results[version].append(dict(seconds=time.perf_counter()-start,rows=rows,
                cache=m._decode_v576_body_cached.cache_info()._asdict()))
    cache_examples=[]
    for name in ('f96282_m0_p5','f235006_m0_p0'):
        record=saved['packets'][name];wire=bytes.fromhex(record['payload_hex'])
        for version,m in modules.items():
            m._decode_v576_body_cached.cache_clear()
            rows=m.decode_v576_server_damage_object_registrations(wire,None,(3021,),include_unmapped=True)
            cache_examples.append(dict(fixture=name,length=len(wire),version=version,
                rows=len(rows),cache=m._decode_v576_body_cached.cache_info()._asdict()))
    med={v:statistics.median(r['seconds'] for r in runs) for v,runs in results.items()}
    output=dict(scope='3438 previously cached registration-related packets, one capture/owner; isolated parser only, NOT whole-replay/game performance',
        packets=len(packets),runs=results,median_seconds=med,
        delta_seconds=med['v22']-med['v21'],
        delta_microseconds_per_packet=(med['v22']-med['v21'])*1e6/len(packets),
        isolated_parser_ratio=med['v22']/med['v21'],cache_examples=cache_examples,
        protocol_hashes=hashes,
        unchanged_after=all(hashlib.sha256((ROOT/f'outputs/damage_attribution_candidate_{v}_20260907/protocol_v576.py').read_bytes()).hexdigest()==h for v,h in hashes.items()),
        no_runtime_edits=True,no_capture_read=True,no_replay=True)
    (OUT/'performance.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k not in ('runs','cache_examples')},ensure_ascii=False))
    print(json.dumps(cache_examples))


if __name__=='__main__':main()
