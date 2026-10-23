"""Single-worker raw structural survey, with no scoring or runtime changes.

Find a repeated-owner/five-zero/full-object/Projectile prefix at ANY offset.
Survey is not a permissive runtime decoder: unaudited lengths are reported,
not admitted. The 120/127/130 family has a separate closed guarded probe.
No job, current skill name or nearest action is used to select a template.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.audit_remaining_registration_packet_decoder import u32
from tools.audit_remaining_registration_server_variants import DATA_DIR, hashes, load_templates
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource

FOCUS=frozenset({3025,3022,4004,63,5515,5516,5517,2002,11084})


def prefix_rows(decoded, templates):
    z=decoded.find(bytes(5),13)
    while z>=0:
        o=z-4
        if o+49<=len(decoded):
            owner,obj,tpl=u32(decoded,o),u32(decoded,o+9),u32(decoded,o+13)
            if (0x01000000<=owner<0xffffffff and owner==u32(decoded,o+45)
                    and 0<obj<=0xffffff and tpl in templates):
                yield o,owner,obj,tpl
        z=decoded.find(bytes(5),z+1)


def describe_tail(tail):
    n=len(tail);word=u32(tail,49) if n>=53 else 0
    ffs=[i for i in range(49,min(n-7,110)) if tail[i:i+8]==b'\xff'*8 and (i==49 or tail[i-1]!=255)]
    suffix_at=n-32
    suffix=u32(tail,suffix_at) if suffix_at>=53 else None
    resource=word&0xffff
    branch='other';delta=None;parent=None
    head_guard=False
    if n>=61 and tail[53:61]==b'\xff'*8 and resource:
        branch='resource';delta=suffix_at-65;head_guard=True
    elif (n>=66 and tail[49]==0 and 0<u32(tail,50)<=0xffffff
          and u32(tail,50)!=u32(tail,9) and u32(tail,54)&0xffff==1
          and tail[58:66]==b'\xff'*8):
        branch='parent';delta=suffix_at-70;parent=u32(tail,50);head_guard=True
    relation=('equal' if suffix==resource and resource else 'FFFF' if suffix==65535
              else 'other' if suffix is not None else 'absent')
    # Last32 is a positional hypothesis except for already demonstrated
    # complete tails97/102/110. Do not rename it a verified resource field.
    fourff=bool(suffix_at>=8 and tail[suffix_at-8:suffix_at-4]==b'\xff'*4)
    return dict(branch=branch,tail_length=n,head_word=word,resource_head=resource,
                eight_ff_starts=ffs,head_guard=head_guard,
                end_relative_suffix_offset=suffix_at,end_relative_suffix=suffix,
                suffix_relation=relation,extra_before_scalar_bytes=delta,
                four_ff_before_scalar=fourff,parent_candidate=parent,
                opaque_insert9_hex=tail[61:70].hex() if branch=='resource' and delta==13 else None)


def closed_resource13_probe(payload, candidates):
    """Strict format-only probe; no skill assignment or FFFF admission.

    127/130 are hypotheses unless actually observed and fixture-validated.
    Guard validation uses ALL13 phases already surveyed, no phase guessing.
    """
    layout={120:(10,12),127:(17,6),130:(20,9)}.get(len(payload))
    if layout is None:return None
    o,phase=layout
    valid=[r for r in candidates if r['offset']==o and r['tail_length']==110
           and r['branch']=='resource' and r['suffix_relation']=='equal'
           and r['head_guard'] and r['four_ff_before_scalar']
           and r['extra_before_scalar_bytes']==13]
    if len(valid)!=1 or valid[0]['phase']!=phase:return None
    if len({r['phase'] for r in candidates})!=1:return None
    return valid[0]


def audit(capture,templates,output):
    start=time.monotonic();before=hashes();src=V576DiagnosticReplaySource(capture,DATA_DIR,{})
    serial=0;origin=None;last_print=start
    groups=Counter();rows=[];summary=Counter();examples=defaultdict(list)
    # Exact-tail replicas are collapsed regardless of timestamp reversal.
    current={};recent={};generations=Counter();wrapper_pairs=Counter()
    def feed(packet):
        nonlocal serial,origin,last_print
        serial+=1
        if str(packet.transport).upper()!='UDP':return ()
        payload=bytes(packet.payload);t=float(packet.timestamp_seconds)
        if origin is None:origin=t
        if len(payload)<59:return ()
        found=[]
        # Discovery explicitly examines all13 phases, even if an old signature
        # table advertises a preferred phase. Full repeat guards prevent votes
        # from arbitrary integers and damaged offsets.
        for phase in range(13):
            b=_decode_v576_body(payload,phase)
            for o,owner,obj,tpl in prefix_rows(b,templates):
                tail=b[o:];shape=describe_tail(tail)
                r=dict(p=serial,t=t,capture_seconds=round(t-origin,6),
                       owner=owner,obj=obj,template=tpl,length=len(b),offset=o,phase=phase,
                       direction=packet.inferred_direction(),signature=payload[5:9].hex(),
                       **shape,tail_hex=tail.hex(),wire_hex=payload.hex())
                found.append(r)
        phases={r['phase'] for r in found}
        strict=closed_resource13_probe(payload,found)
        for r in found:
            r['phase_unique']=len(phases)==1
            r['strict_resource13_probe']=r is strict
            summary['repeated_owner_prefix_rows']+=1
            summary['phase_unique_rows']+=int(r['phase_unique'])
            summary['strict_resource13_rows']+=int(r is strict)
            slot=(r['owner'],r['obj']);token=(*slot,r['tail_hex'])
            prior=recent.get(token)
            duplicate=prior is not None and abs(t-prior['t'])<=.25
            if duplicate:
                generation=prior['audit_generation']
                summary['exact_tail_replicas']+=1
                summary['replica_timestamp_backward']+=int(t<prior['t'])
                wrapper_pairs[f"{prior['length']}->{r['length']}"]+=1
                r['replica_of_packet']=prior['p']
            else:
                generations[slot]+=1;generation=generations[slot];current[slot]=generation
                r['replica_of_packet']=None
            r['audit_generation']=generation
            if not duplicate:recent[token]=r
            key=(f"L{r['length']}/O{r['offset']}/P{r['phase']}/T{r['template']}/"
                 f"{r['branch']}/extra{r['extra_before_scalar_bytes']}/{r['suffix_relation']}/"
                 f"4FF{int(r['four_ff_before_scalar'])}")
            groups[key]+=1
            rows.append(r)
            if len(examples[key])<2:examples[key].append(r)
        if time.monotonic()-last_print>25:
            print(json.dumps({'capture':capture.name,'packets':serial,'prefix_rows':len(rows),
                              'strict_resource13_rows':summary['strict_resource13_rows']},ensure_ascii=False),flush=True)
            last_print=time.monotonic()
        return ()
    src._decoder.feed_events=feed
    try:src.poll()
    finally:src.close()
    after=hashes();report={'capture':str(capture),'feed_packets':serial,
                          'elapsed_seconds':round(time.monotonic()-start,3),
                          'core_before':before,'core_after':after,'core_unchanged':before==after,
                          'policy':'raw UDP only; ALL13 phases; prefix identity+template existence; end-relative suffix is hypothesis outside validated tails; no scoring, no skill assignment',
                          'summary':summary,'groups':groups,'wrapper_pairs':wrapper_pairs,
                          'examples':examples,'rows':rows}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'capture':capture.name,'feed_packets':serial,'seconds':report['elapsed_seconds'],
                      'core_unchanged':report['core_unchanged'],'summary':summary,
                      'focus_shapes':{k:v for k,v in groups.items() if any(f'/T{tpl}/' in k for tpl in FOCUS)}},ensure_ascii=False),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('captures',nargs='+',type=Path)
    p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--resource-root',type=Path,default=Path(r'E:\ext0811.rar\Resource00\Resource00\resource'))
    a=p.parse_args();sys.stdout.reconfigure(encoding='utf8');a.output_dir.mkdir(parents=True,exist_ok=True)
    templates=load_templates(a.resource_root)
    for cap in a.captures:
        dest=a.output_dir/(cap.stem+'.json')
        if dest.exists():print('EXISTS not overwritten',dest,flush=True);continue
        print('START',cap.name,flush=True);audit(cap,templates,dest)


if __name__=='__main__':main()
