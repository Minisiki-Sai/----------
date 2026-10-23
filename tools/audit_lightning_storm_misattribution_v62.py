"""Bounded 0821 cleric object-generation misattribution diagnostics only.

No production writes, game execution, all-corpus replay, current-action
attribution, label propagation, or damage/time-fingerprint joins.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from hashlib import sha256
import argparse
import json
from dataclasses import asdict
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.trace_projectile_config_v45 import parse_dnt
from tools.probe_magma_signal_flags_v36 import parse_act6
from tools.trace_actor_action_namespace_v47 import manifest_tokens
from tools.probe_compact_entity_attack_selectors import walk
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from dragon_nest_builder.live_dps.native_damage_skill_keys import _selected_native_key

OUT = ROOT / 'outputs/lightning_storm_misattribution_v62_20260909'
RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
PRIOR = ROOT / 'outputs/chain_lightning_meter_v60_20260909'
ACTOR = 1314843433
CAPTURE = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/0821黑典女王.dpscap')
INPUTS = {}

def load(path):
    data = path.read_bytes()
    INPUTS[str(path)] = sha256(data).hexdigest()
    return data

def read(path):
    return json.loads(load(path))

def write(name, data):
    (OUT/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def u32(b, p):
    return struct.unpack_from('<I', b, p)[0]

def resources():
    tables = {}
    skills = {3019, 3401, 3402, 3407, 3408, 3425, 3554, 3555, 3556}
    templates = {63, 47, 4004, 4011, 4018, 4024}
    for filename in ('skilltable_character.dnt','skillleveltable_charactercleric.dnt',
                     'weapontable_projectile.dnt','filetable.dnt'):
        path = RESOURCE/'ext'/filename
        raw = load(path)
        tables[filename] = parse_dnt(raw)
    skillrows = [r for r in tables['skilltable_character.dnt']['rows'] if r['cells']['RowId']['value'] in skills]
    levelrows = [r for r in tables['skillleveltable_charactercleric.dnt']['rows'] if r['cells']['_SkillIndex']['value'] in skills]
    weapons = [r for r in tables['weapontable_projectile.dnt']['rows'] if r['cells']['RowId']['value'] in templates]
    fileids = {r['cells']['_ActName']['value'] for r in weapons}
    files = [r for r in tables['filetable.dnt']['rows'] if r['cells']['RowId']['value'] in fileids]
    action_params = defaultdict(set)
    for row in levelrows:
        for name, cell in row['cells'].items():
            if name.startswith('_ProcessParam') and isinstance(cell['value'],str) and cell['value']:
                action_params[cell['value']].add(row['cells']['_SkillIndex']['value'])
    acts, skipped = [], []
    directory = RESOURCE/'char/player/cleric'
    for token in manifest_tokens(load(directory/'cleric.act')):
        path = directory/token.decode('ascii')
        raw = load(path)
        version = u32(raw,32)
        if version != 6:
            skipped.append({'path':str(path),'version':version,'reason':'outside ACT6 parser; not used to assert global uniqueness'})
            continue
        parsed = parse_act6(raw)
        for ordinal, action in enumerate(parsed['actions']):
            spawns = [s for s in action['signals'] if s['type']==15 and s['u32'][0] in templates]
            if spawns or action['name'] in action_params:
                acts.append({'path':str(path),'ordinal':ordinal,'name':action['name'],
                    'offset':action['offset'],'next_action':action['next_action'],
                    'processor_skills':sorted(action_params[action['name']]),
                    'spawns':spawns,'all_type15':[{'offset':s['offset'],'template':s['u32'][0]} for s in action['signals'] if s['type']==15]})
    result = {'authority':'old unpacked resource snapshot, not assumed byte-identical to current installed encrypted PAK',
        'skill_rows':skillrows,'level_rows':levelrows,'weapon_rows':weapons,'file_rows':files,'cleric_actions':acts,
        'unparsed_non_ACT6_shards':skipped}
    write('resource_rows.json',result)
    summary = {'skills':[{'id':r['cells']['RowId']['value'],'name':r['cells'].get('_StaticName',{}).get('value')} for r in skillrows],
        'weapons':[{k:v['value'] for k,v in r['cells'].items() if k in ('RowId','_ActName','_Duration','_Durability','_Length','_StaticName')} for r in weapons],
        'files':[{k:v['value'] for k,v in r['cells'].items()} for r in files],
        'action_spawns':[{'name':a['name'],'skills':a['processor_skills'],'spawns':[s['u32'][0] for s in a['spawns']]} for a in acts if a['spawns']]}
    write('resource_summary.json',summary)
    return summary

def inventory():
    gaps = read(PRIOR/'remaining_325.json')['rows']
    prior = read(PRIOR/'v59_completed.json')['capture_evidence']
    stages = read(PRIOR/'event_quantities.json')['stages']['aggregated']
    final = {r['physical_sequence']:r for r in stages if r['physical_sequence'] is not None}
    regrows = read(ROOT/'outputs/projectile_root_skill_v60_20260909/all_cleric_registrations.json')['rows']
    # Raw typed registration offsets here are only inventory candidates; the
    # wire pass later must validate subtype, redundant owner and framing.
    byobj = defaultdict(list)
    for row in regrows:
        b = bytes.fromhex(row['record_hex'])
        if row['length'] not in (93,97,106,110):
            continue
        assert b[7:12] == bytes(5) and u32(b,0)==u32(b,48)
        byobj[u32(b,12)].append(row|{'object_candidate':u32(b,12),'template_candidate':u32(b,16),
            'redundant_owner_candidate':u32(b,48)})
    hitsbyobj = defaultdict(list)
    for h in prior['focus_hits']:
        hitsbyobj[h['damage_object_id']].append(h)
    rows = []
    for g in gaps:
        h = g['physical']
        obj = h['damage_object_id']
        regs = byobj[obj]
        before = [r for r in regs if (r['frame'],r['message'],r['marker']) < (h['frame'],h['message_index'],h['record_offset'])]
        diagnostic = final[h['sequence']]
        rows.append(g|{'aggregated_evidence':diagnostic,'all_numeric_id_registrations':regs,
            'last_prior_registration_candidate':before[-1] if before else None,
            'all_numeric_id_labelled_hits':[x for x in hitsbyobj[obj] if x['native_deterministic']],
            'numeric_id_only_is_not_generation':True})
    selected = [r for r in rows if r['same_id_group']=='same_id_other_independent_skill' or
        any(x['template_candidate'] in (4011,4018,4024,4004) for x in r['all_numeric_id_registrations'])]
    write('inventory_325.json',rows)
    write('priority_cases.json',selected)
    result = {'gaps':len(gaps),'priority_union':len(selected),
        'same_id_other':sum(r['same_id_group']=='same_id_other_independent_skill' for r in rows),
        'known_other_template_numeric_id':sum(any(x['template_candidate'] in (4011,4018,4024,4004) for x in r['all_numeric_id_registrations']) for r in rows),
        'priority':[{ 'frame':r['physical']['frame'],'ms':r['physical']['elapsed_ms'],'obj':r['physical']['damage_object_id'],
            'old_independent_labels':r['same_id_independent_skill_ids'],
            'regs':[(x['frame'],x['elapsed_ms'],x['template_candidate']) for x in r['all_numeric_id_registrations']]} for r in selected]}
    write('inventory_summary.json',result)
    return result

def wire_audit():
    cases = read(OUT/'priority_cases.json')
    ids = sorted({r['physical']['damage_object_id'] for r in cases})
    tokens = {n:n.to_bytes(4,'little') for n in ids}
    key = bytes.fromhex('c8bfcda4943f2642fd8a723474')
    encoded = {bytes(v ^ key[(j+phase)%13] for j,v in enumerate(token)) for token in tokens.values() for phase in range(13)}
    pattern = re.compile(b'|'.join(re.escape(t) for t in sorted(encoded)))
    rows, rejected, scenes = [], [], []
    counts = Counter()
    transport = p.V576TransportReassembler()
    digest = sha256()
    first = None
    frame = 0
    with CAPTURE.open('rb') as f:
        magic=f.read(6); digest.update(magic)
        assert magic==b'DNDP1\0'
        while header:=f.read(17):
            assert len(header)==17
            t,adapter,original,size=struct.unpack('<dBII',header)
            raw=f.read(size); assert len(raw)==size
            digest.update(header);digest.update(raw);frame+=1
            if first is None:first=t
            packet=p.decode_captured_frame(t,raw,original)
            if packet is None:continue
            # Complete scene records use only the protocol's framing decoders.
            logicals=transport.feed(packet) if packet.transport=='TCP' else (packet,)
            for logical in logicals:
                for mi,wire in enumerate(p.split_v576_payload(logical.payload)):
                    direction=logical.inferred_direction()
                    meta={'frame':frame,'message':mi,'elapsed_ms':round((t-first)*1000),
                        'transport':logical.transport,'direction':direction,'wire_sha256':sha256(wire).hexdigest()}
                    scene_decoders=(('teardown',p.decode_v576_scene_teardown),('map',p.decode_v576_map_identity),('ack',p.decode_v576_scene_transition_ack))
                    for name,decoder in scene_decoders:
                        event=decoder(wire,direction)
                        if event:
                            scenes.append(meta|{'kind':name,'event':asdict(event),'wire_hex':wire.hex()})
                    if logical.transport!='UDP':continue
                    counts['udp_messages']+=1
                    if not pattern.search(wire[9:]):continue
                    counts['lossless_token_candidates']+=1
                    parsed=walk(wire)
                    if not parsed:
                        matches=[]
                        for phase in range(13):
                            body=p._decode_v576_body_uncached(wire,phase)
                            found=[n for n,b in tokens.items() if b in body[9:]]
                            if found:matches.append({'phase':phase,'objects':found,'plaintext_hex':body.hex()})
                        rejected.append(meta|{'wire_hex':wire.hex(),'unframed_matches':matches})
                        continue
                    phase,body,records=parsed
                    inv=None
                    for ri,(actor,m,op,n) in enumerate(records):
                        rb=body[m-4:m+3+n]
                        found={str(obj):[i for i in range(len(rb)-3) if rb[i:i+4]==token] for obj,token in tokens.items() if token in rb}
                        if not found:continue
                        row=meta|{'record_index':ri,'marker':m,'phase':phase,'actor':actor,'op':op,'length':n,
                            'occurrences':found,'record_hex':rb.hex(),'record_sha256':sha256(rb).hexdigest(),
                            'wire_hex':wire.hex()}
                        if op==9 and n>=45 and rb[7:12]==bytes(5):
                            row['object_prefix']={'owner':actor,'object_id':u32(rb,12),'template':u32(rb,16),
                                'redundant_owner':u32(rb,48) if len(rb)>=52 else None}
                        if op==8 and n in (115,119):
                            if inv is None:inv=inspect_udp_damage_records(wire,direction)
                            if inv.complete:
                                h=next((h for h in inv.damage_records if h.record_offset==m),None)
                                if h:
                                    row['hit']={'source':h.source_actor_id,'target':h.target_actor_id,'object_id':h.damage_object_id,
                                        'result_group':h.damage_result_group_id,'damage':h.damage,'admissible':h.admissible_fields,
                                        'native_selected_key':_selected_native_key(h),'raw_slots':h.raw_skill_slots,
                                        'source_kind':rb[11],'common_cc':rb[104 if n==119 else 100]}
                        rows.append(row)
                        counts[f'typed_{op:02x}_{n}']+=1
            if frame%100000==0:print(json.dumps({'progress_frame':frame,'selected_records':len(rows)},ensure_ascii=False),flush=True)
    assert digest.hexdigest()=='40be2e0bc2cf58cb3ba3dec707cfc119fc18bef29ebb2967b8779f20402c5d17'
    INPUTS[str(CAPTURE)]=digest.hexdigest()
    result={'schema':'targeted-object-lifetime-wire-v62','objects':ids,'frames':frame,'counts':dict(counts),
        'records':rows,'unframed_candidates':rejected,'scene_events':scenes,
        'no_deduplication':True,'scope':'single capture, selected numeric object tokens at all XOR phases, then unique complete typed walk; no labels used for parsing'}
    write('wire_history.json',result)
    print(json.dumps({'frames':frame,'records':len(rows),'unframed':len(rejected),'scenes':len(scenes),'counts':dict(counts)}),flush=True)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--wire',action='store_true')
    args=ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    summary = inventory()
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    print(json.dumps(resources(),ensure_ascii=False),flush=True)
    if args.wire:wire_audit()
    write('input_manifest.json',{'inputs':INPUTS,'tool_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
        'production_mutated':False,'capture_replayed_by_meter':False})

if __name__ == '__main__':
    main()
