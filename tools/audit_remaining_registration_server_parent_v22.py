"""Cached server09 parent audit; never opens a capture or runs a replay."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'outputs/audit_remaining_registration_3021_context_20260907'
OUT = ROOT / 'outputs/audit_remaining_registration_server_parent_v22_20260907'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def u32(b, p):
    return int.from_bytes(b[p:p+4], 'little')


def parent(b, m):
    if m < 18 or m + 101 > len(b) or b[m:m+3] != b'\x09\x62\0':
        return None
    owner, obj, tpl = u32(b, m-4), u32(b, m+8), u32(b, m+12)
    pid, branch, suffix = u32(b, m+49), u32(b, m+53), u32(b, m+69)
    if not (0 < owner < 0xffffffff and owner == u32(b, m+44)
            and b[m+3:m+8] == bytes(5) and 0 < obj <= 0xffffff
            and 0 < tpl <= 0x7fffffff and b[m+48] == 0
            and 0 < pid <= 0xffffff and pid != obj
            and branch & 0xffff == 1 and b[m+57:m+65] == b'\xff'*8
            and 0 < suffix < 0xffff):
        return None
    return dict(owner=owner, object_id=obj, template=tpl, parent_id=pid,
                sequence=branch >> 16, suffix=suffix,
                normalized_tail=(b[m-4:m]+b[m+3:m+101]).hex(),
                record_end=m+101, marker_offset=m)


def main():
    OUT.mkdir(exist_ok=True)
    ck = load(RAW/'scan_checkpoint.json')
    fx = load(RAW/'fixtures_final.json')
    protocol_path = ROOT/'outputs/damage_attribution_candidate_v20_20260907/protocol_v576.py'
    spec = importlib.util.spec_from_file_location('audit_v20_server_parent', protocol_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    packets, rows = ck['packets'], []
    seen = set()
    for g in ck['generations']:
        for raw in [g, *g['replicas']]:
            if raw['kind'] != 'server09':
                continue
            key = (raw['fixture'], raw['m'])
            if key in seen:
                continue
            seen.add(key)
            f = packets[raw['fixture']]
            b = bytes.fromhex(f['decoded_hex'])
            r = parent(b, raw['m'])
            if r:
                r.update(fixture=raw['fixture'], t=raw['t'], order=raw['order'],
                         generation=g['generation'], generation_cache_id=g['id'],
                         packet_length=f['length'], phase=f['phase'])
                rows.append(r)
    selected = [r for r in fx['final_parent_evidence'] if r['server_tail']]
    baseline = []
    for h in selected:
        f = packets[h['child_fixture']]
        wire = bytes.fromhex(f['payload_hex'])
        matches = [r for r in module.decode_v576_server_damage_object_registrations(wire, None, (3021,), include_unmapped=True)
                   if r.source_actor_id == h['evidence']['source_actor_id'] and r.damage_object_id == h['child_obj']]
        assert len(matches) == 1 and matches[0].parent_damage_object_id == 0
        baseline.append(dict(child=h['child_obj'], baseline_parent=matches[0].parent_damage_object_id,
                             expected_parent=h['evidence']['parent_object_id_candidate']))
    bykey = defaultdict(list)
    for row in rows:
        bykey[(row['owner'], row['object_id'])].append(row)
    nearby = []
    for key, records in bykey.items():
        records.sort(key=lambda r:r['order'])
        for a, b in zip(records, records[1:]):
            if 0 <= b['t']-a['t'] <= 8:
                delta=[i for i,(x,y) in enumerate(zip(bytes.fromhex(a['normalized_tail']), bytes.fromhex(b['normalized_tail']))) if x != y]
                nearby.append(dict(owner=key[0], object_id=key[1], first=a, second=b,
                                   seconds=b['t']-a['t'], differing_normalized_offsets=delta))
    # Preserve at least three independent parent generations and both envelope forms.
    chosen=[]; parents=set(); lengths=set()
    for h in selected:
        pk=(h['evidence']['parent_object_id_candidate'],h['parent_generation'])
        length=packets[h['child_fixture']]['length']
        if pk not in parents or length not in lengths:
            chosen.append(h);parents.add(pk);lengths.add(length)
    ids={}
    cases=[]
    for h in chosen:
        row=next(r for r in rows if r['fixture']==h['child_fixture'] and r['object_id']==h['child_obj'])
        keys={h[k] for k in ('child_fixture','parent_fixture','hit_fixture')}
        casts=[c for c in fx['expected_casts'] if c['skill_id']==2353 and c['epoch'] in h['root_action_epochs']]
        keys.update(c['fixture'] for c in casts)
        for key in keys:ids[key]=packets.get(key,fx['records'].get(key))
        cases.append(dict(expected=row, parent_evidence=h, real_root_actions=casts))
    result=dict(capture_sha256=ck['capture_sha256'], scope='Previously saved owner-specific raw registrations; no capture reopened',
                strict_rows=len(rows), strict_templates=dict(Counter(r['template'] for r in rows)),
                envelopes=dict(Counter(r['packet_length'] for r in rows)),
                selected_hit_count=len(selected), selected_damage=sum(h['damage'] for h in selected),
                baseline_parent_drops=baseline, nearby_same_object_records=nearby,
                cases=cases, records=ids, all_strict_rows=rows)
    (OUT/'cached_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('strict_rows','strict_templates','envelopes','selected_hit_count','selected_damage')},ensure_ascii=False))
    print(json.dumps(dict(cases=len(cases),records=len(ids),nearby_same_object_records=len(nearby),
                          same_identity_changed_tail=sum(bool(r['differing_normalized_offsets']) and r['first']['parent_id']==r['second']['parent_id'] and r['first']['template']==r['second']['template'] for r in nearby))))


if __name__=='__main__':main()
