"""Observe current post-admission gates, without changing decoder decisions."""
from collections import Counter,defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.compact_entity_object_evidence import CompactEntityObjectLedger
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import PhysicalHit,read_self_attack_slot
from dragon_nest_builder.live_dps.damage_attribution import is_deterministic_attribution,attribution_layer,AttributionLayer


def main(path):
    original=CompactEntityObjectLedger.annotate
    counts=Counter();examples=defaultdict(list)
    def wrap(self,event,context,owner_proof,**options):
        result=original(self,event,context,owner_proof,**options)
        if (owner_proof is None or owner_proof.template_id!=32005 or event.has_real_hit_evidence is not True
                or event.healing or not str(event.skill_id).isdigit()
                or int(event.skill_id)<=0 or is_deterministic_attribution(event.attribution_method)
                or attribution_layer(event.attribution_method) is AttributionLayer.STATUS):
            return result
        r=self.objects.get((owner_proof.entity_id,owner_proof.generation,event.wire_damage_object_id))
        reasons=[]
        if result.attribution_method=='packet_entity_projectile_selector':reasons=['confirmed']
        elif options['pve_confirmed']is not True:reasons=['pve_missing']
        elif r is None:reasons=['registration_missing']
        elif r.spec is None:reasons=['selector_invalidated']
        else:
            group,attack,projectile,lifetime=r.spec
            checks={
                'expired':not 0<=context.timestamp-r.timestamp<=lifetime,
                'before_registration':r.order>context.order,
                'owner_id':r.owner!=owner_proof.owner_id,
                'birth_hash':r.birth_hash!=owner_proof.birth_fingerprint,
                'owner_hash':r.owner_hash!=owner_proof.owner_fingerprint,
                'owner_evidence_id':r.owner_evidence_id!=owner_proof.owner_evidence_id,
                'job_conflict':options['job_conflicts'](r.owner,group),
                'wire_hash':sha256(context.wire).hexdigest()!=owner_proof.hit_wire_sha256,
                'entity_event':event.entity_owner_evidence_id!=owner_proof.owner_evidence_id,
            }
            reasons=[k for k,v in checks.items()if v]
            physical=PhysicalHit('diagnostic','no-promotion',context.order,r.record.entity,r.generation,r.template,
                event.wire_target_actor_id,event.amount,event.wire_damage_object_id,event.wire_result_group_id,
                context.wire,context.direction)
            slot=read_self_attack_slot(physical)
            if slot is None:reasons.append('self_slot_unreadable')
            elif slot.monster_skill_id!=0xffffffff:reasons.append(f'self_slot:{slot.monster_skill_id}')
            if not reasons:reasons=['other_guard']
        key=(event.job,'+'.join(reasons));counts[key]+=1
        if len(examples[key])<2:
            examples[key].append(dict(event=asdict(event),time=context.timestamp,
                order=context.order,origin=asdict(r)if r else None,proof=asdict(owner_proof),
                wire=context.wire.hex(),direction=context.direction))
        return result
    CompactEntityObjectLedger.annotate=wrap
    source=V576DiagnosticReplaySource(path,ROOT/'dragon_nest_builder/data',{})
    try:
        for _ in source.iter_batches():pass
    finally:
        source.close();CompactEntityObjectLedger.annotate=original
    result=dict(capture=str(path),capture_sha256=sha256(path.read_bytes()).hexdigest(),
        scope='pre-aggregation diagnostics, no promotion',rows=[dict(job=k[0],reason=k[1],hits=v,examples=examples[k])for k,v in counts.most_common()])
    out=ROOT/'outputs/compact_rejection_v33_20260908';out.mkdir(exist_ok=True)
    (out/(path.stem+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=list),encoding='utf-8')
    print([(k,v)for k,v in counts.most_common()],flush=True)


if __name__=='__main__':main(Path(sys.argv[1]))
