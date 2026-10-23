"""Frozen replay with exact target-correction witnesses and unchanged counters.

Recording wrappers only. No evidence, event or classification is fabricated.
The full physical lists allow review when the intentionally corrected target
makes the old all-fields hash differ; no changed field is silently excluded.
"""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('runtime',type=Path);ap.add_argument('capture',type=Path);ap.add_argument('output',type=Path)
    args=ap.parse_args();runtime=args.runtime.resolve()
    sys.path.insert(0,str(runtime))
    from tools import audit_direct_damage_by_profession as a
    from dragon_nest_builder.live_dps.models import CombatEvent
    from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
    from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
    lists=[];witnesses=[]
    invariants=a._raw_damage_invariants
    def save(events):
        lists.append([[e.timestamp_ms,e.source_id,e.target_id,e.amount,e.critical,
            e.wire_result_group_id,e.wire_damage_object_id,e.wire_source_actor_id,e.wire_target_actor_id,
            e.wire_record_offset,e.has_real_hit_evidence,e.skill_id,e.attribution_method]
            for e in events if isinstance(e,CombatEvent)and not e.healing])
        return invariants(events)
    a._raw_damage_invariants=save
    namespace=_V576PacketEventDecoder.feed.__globals__
    original=namespace['canonicalize_udp_damage_identity']
    def trace(packet,decoded):
        out=original(packet,decoded)
        if decoded is not None and decoded.kind=='damage_hit'and out.target_actor_id!=decoded.target_actor_id:
            inv=inspect_udp_damage_records(packet.payload,'server_to_client')
            row=next(r for r in inv.damage_records if r.record_offset==out.record_offset)
            assert row.admissible_fields and row.target_actor_id==out.target_actor_id
            witnesses.append(dict(time=packet.timestamp_seconds,before=asdict(decoded),after=asdict(out),
                target=row.target_actor_id,source=row.source_actor_id,marker=row.record_offset,phase=inv.xor_phase,
                wire=packet.payload.hex(),wire_sha256=inv.wire_sha256))
        return out
    namespace['canonicalize_udp_damage_identity']=trace
    signature_before=a._analysis_signature()
    report=a._audit_capture(str(args.capture.resolve()),tuple(a._second_jobs()))
    assert signature_before==a._analysis_signature()
    assert len(lists)==2
    report['physical_columns']=['time','source','target','amount','critical','group','object','wire_source','wire_target','offset','real','skill','method']
    report['raw_physical_rows'],report['aggregated_physical_rows']=lists
    report['target_correction_witnesses']=witnesses
    report['frozen_signature']=signature_before
    # Includes modules absent from old audit versions' signature lists.
    report['runtime_py_hashes']={p.relative_to(runtime).as_posix():sha256(p.read_bytes()).hexdigest()
        for p in (runtime/'dragon_nest_builder').rglob('*.py')}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(json.dumps(dict(capture=args.capture.name,runtime=str(runtime),corrections=len(witnesses),
        physical=report['raw_source_invariants'],professions=report['profession_summary']),ensure_ascii=False))


if __name__=='__main__':main()
