"""Diagnose unproved named entity hits at the current post-admission boundary."""
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture',type=Path)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
    from dragon_nest_builder.live_dps.damage_attribution import is_deterministic_attribution
    from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import PhysicalHit,read_self_attack_slot
    from dragon_nest_builder.live_dps.summon_attack_runtime import SUMMON_SELF_ATTACK_SPECS
    source=V576DiagnosticReplaySource(a.capture,ROOT/'dragon_nest_builder/data',{})
    d=source._decoder
    original=d._finalize_wire_damage_owner
    counts=Counter(); examples=defaultdict(list)
    def finalize(event,packet):
        out=original(event,packet)
        eid=out.wire_source_actor_id
        if (out.has_real_hit_evidence is not True or out.healing or not 0<eid<65536
                or not str(out.skill_id).isdigit() or is_deterministic_attribution(out.attribution_method)):
            return out
        template=d.monster_entities.get(eid)
        tid=template.template_id if template else 0
        binding=d.entity_owner_evidence.resolve(eid,tid,packet.timestamp_seconds)
        target=d.monster_entities.get(out.wire_target_actor_id)
        why=d.entity_owner_evidence.negative_state(eid,timestamp=packet.timestamp_seconds) or 'owner_ok'
        h=PhysicalHit('diagnostic','no-certification',(d._damage_object_packet_serial,0,out.wire_record_offset),
            eid,binding.generation if binding else 0,tid,out.wire_target_actor_id,out.amount,
            out.wire_damage_object_id,out.wire_result_group_id,packet.payload,packet.inferred_direction())
        slot=read_self_attack_slot(h)
        sid=slot.monster_skill_id if slot else -1
        key=(out.job,tid,out.skill_id,why,bool(target and target.boss),sid,(tid,sid) in SUMMON_SELF_ATTACK_SPECS)
        counts[key]+=1
        if len(examples[key])<2:
            examples[key].append(dict(time=packet.timestamp_seconds,event=asdict(out),wire=packet.payload.hex(),
                binding=asdict(binding) if binding else None))
        return out
    d._finalize_wire_damage_owner=finalize
    try:
        for _ in source.iter_batches(): pass
    finally: source.close()
    result=dict(capture=str(a.capture),rows=[dict(job=k[0],template=k[1],skill=k[2],owner=k[3],
        boss=k[4],self_slot=k[5],spec=k[6],hits=n,examples=examples[k]) for k,n in counts.most_common()])
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{k:v for k,v in x.items() if k!='examples'} for x in result['rows'][:35]],ensure_ascii=False))


if __name__=='__main__':main()
