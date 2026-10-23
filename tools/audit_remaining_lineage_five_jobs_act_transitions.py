"""Read-only ACT next-action/string-reference audit, no generated/runtime writes."""
from __future__ import annotations
import json
import struct
import sys
from pathlib import Path
from collections import defaultdict, deque

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.dont_write_bytecode=True
from tools.build_attack_instance_catalog import _Reader, ACT_SIGNATURE

JOBS={'时空领主','噬魂者','黑暗萨满','圣骑士','十字军'}

def parse_full(path):
    r=_Reader(path.read_bytes(),path)
    assert r.take(32).startswith(ACT_SIGNATURE)
    version=r.i32(); count=r.count('actions'); actions=[]
    for _ in range(count):
        action={'offset':r.offset,'name':r.string(),'animation':r.string(),'frames':r.i32(),'next_action':r.string(),'blend':r.i32(),'next_frame':r.i32()}
        r.take({1:0,2:4,3:8,4:9,5:13,6:14}[version])
        signals=[]
        for _ in range(r.count('signals')):
            s={'offset':r.offset,'type':r.i32(),'start':r.i32(),'end':r.i32(),'order':r.i32()}
            data=r.take(256)
            s['u32']=list(struct.unpack('<64I',data))
            s['tables']={}
            for w,label in [(8,'vec2'),(12,'vec3'),(16,'vec4')]:
                n=r.count(label); indexes=[r.i32() for _ in range(n)]; values=[list(struct.unpack('<'+'f'*(w//4),r.take(w))) for _ in range(n)]
                if n:s['tables'][label]=list(zip(indexes,values))
            n=r.count('strings'); indexes=[r.i32() for _ in range(n)]; values=[r.string() for _ in range(n)]
            s['strings']=list(zip(indexes,values))
            if version>=6:r.u8()
            signals.append(s)
        action['signals']=signals; actions.append(action)
    assert r.remaining()==0
    return {'version':version,'actions':actions}

def main():
    catalog=json.loads((ROOT/'outputs/damage_attribution/attack_instance_catalog_v576.json').read_text(encoding='utf8'))
    resources=Path(catalog['resource_root'])
    skills=[s for s in catalog['skills'] if s['job'] in JOBS]
    filenames=sorted({a['file'] for s in skills for a in s['matched_actions']})
    parsed={f:parse_full(resources/f) for f in filenames}
    graphs={}; findings=[]
    for file,p in parsed.items():
        byname={a['name'].casefold():a for a in p['actions']}; edges=[]
        for a in p['actions']:
            nxt=a['next_action'].casefold()
            if nxt in byname:
                edges.append({'from':a['name'],'to':byname[nxt]['name'],'evidence':'explicit_next_action','offset':a['offset']})
            for sig in a['signals']:
                for index,value in sig['strings']:
                    if value.casefold() in byname:
                        edges.append({'from':a['name'],'to':byname[value.casefold()]['name'],'evidence':'exact_string_reference_semantics_pending','signal_type':sig['type'],'string_index':index,'offset':sig['offset']})
        graphs[file]=edges
    for skill in skills:
        entry={(a['file'],a['action_name']) for a in skill['matched_actions']}
        for file,name in sorted(entry):
            byname={a['name']:a for a in parsed[file]['actions']}
            for mode in ('next_only','next_and_string_candidates'):
                q=deque([(name,[])]); seen=set()
                while q:
                    node,path=q.popleft()
                    if node in seen:continue
                    seen.add(node)
                    act=byname[node]
                    typed=[s for s in act['signals'] if s['type'] in (4,15)]
                    if path and (file,node) not in entry and typed:
                        findings.append({'skill_id':skill['skill_id'],'canonical':skill['canonical_skill_id'],'name':skill['name'],'job':skill['job'],'file':file,'entry':name,'mode':mode,'path':path,'destination':node,'typed':[{'type':s['type'],'offset':s['offset'],'start':s['start'],'end':s['end'],'template':s['u32'][0] if s['type']==15 else None,'u32':s['u32'][:16]} for s in typed]})
                    # Do not traverse shared locomotion actions as skill ownership.
                    for edge in graphs[file]:
                        if edge['from']==node and (mode!='next_only' or edge['evidence']=='explicit_next_action') and edge['to'].lower().startswith('skill_'):
                            q.append((edge['to'],path+[edge]))
    output=ROOT/'outputs/damage_attribution/audit_remaining_lineage_five_jobs_act_transitions.json'
    output.write_text(json.dumps({'resource_root':str(resources),'policy':'Next-action is explicit linkage; string refs are candidates, not automatic control-flow claims. Prefix only blocks common locomotion, never creates edges.','findings':findings,'edges':graphs,'parsed':parsed},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(output),'files':len(parsed),'findings':findings},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
