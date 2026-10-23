"""Inventory remaining self-attack slots from existing raw indexed windows.

This is discovery only. Old skill labels are never consulted. Complete typed
row walking is reused from the independent stateless compact-record probe.
"""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.probe_compact_entity_attack_selectors import walk
from dragon_nest_builder.live_dps.summon_attack_runtime import SUMMON_SELF_ATTACK_SPECS


def main():
    counts=Counter();examples={};seen=set()
    root=ROOT/'outputs/v25_entity_owner_independent'
    files=['0821硬核04_0_180_v2.json','0822硬核噬魂_0_180.json','0826硬核曜_0_180.json',
        '0830星期天_210_390.json','0831黑硬曜_0_180.json','20260822YEP黑硬曜_0_180.json']
    for f in files:
        d=json.loads((root/f).read_text('utf-8'))
        for hit in d['hits']:
            template=hit.get('template')
            if not template or not 30000<=template<40000:continue
            decoded=walk(bytes.fromhex(hit['wire']))
            if decoded is None:continue
            phase,body,records=decoded
            u32=lambda at:int.from_bytes(body[at:at+4],'little')
            for target,m,op,n in records:
                if op!=8 or n!=115 or u32(m+8)!=hit['source']:continue
                keyrow=(d['capture'],hit['frame'],hit.get('message',0),m)
                if keyrow in seen:continue
                seen.add(keyrow)
                if not int.from_bytes(body[m+19:m+27],'little'):continue
                self_skill,context=u32(m+102),u32(m+114)
                key=(template,self_skill,context,(template,self_skill)in SUMMON_SELF_ATTACK_SPECS)
                counts[key]+=1
                examples.setdefault(key,dict(capture=d['capture'],frame=hit['frame'],offset=hit['file_offset'],
                    message=hit.get('message',0),marker=m,phase=phase,wire=hit['wire']))
    rows=[dict(template=k[0],self_skill=k[1],context_only=k[2],mapped=k[3],candidate_hits=v,
        example=examples[k])for k,v in counts.most_common()]
    out=ROOT/'outputs/compact_entity_selectors_20260908/self_slot_inventory.json'
    out.write_text(json.dumps(dict(scope='6 existing windows; NOT full corpus coverage',rows=rows),
        ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([dict(template=r['template'],self_skill=r['self_skill'],context_only=r['context_only'],
        hits=r['candidate_hits'])for r in rows if not r['mapped']],ensure_ascii=False))


if __name__=='__main__':main()
