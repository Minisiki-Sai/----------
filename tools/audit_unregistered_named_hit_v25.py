"""Bounded raw evidence inventory; never promote existing names to ground truth."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture', type=Path)
    ap.add_argument('--runtime-dir', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    if args.output.exists():
        raise RuntimeError('Refusing to overwrite evidence')
    from tools.audit_damage_attribution_paired import BaselineFinder, snapshot_modules
    modules = snapshot_modules(args.runtime_dir)
    hashes = {name:hashlib.sha256((args.runtime_dir/f).read_bytes()).hexdigest() for name,f in modules.items()}
    sys.meta_path.insert(0,BaselineFinder(args.runtime_dir,modules))
    from dragon_nest_builder.live_dps import sources, protocol_v576 as p
    from dragon_nest_builder.live_dps.models import CombatEvent
    from tools.audit_direct_damage_release_gate import _classification
    source = sources.V576DiagnosticReplaySource(args.capture,ROOT/'dragon_nest_builder/data',{})
    decoder = source._decoder
    original = decoder.feed_events
    counts = Counter()
    examples = defaultdict(list)
    serial = 0

    def feed(packet):
        nonlocal serial
        serial += 1
        events = original(packet)
        suspects = [e for e in events if isinstance(e,CombatEvent) and not e.healing
                    and e.has_real_hit_evidence is True and _classification(e) != 'deterministic'
                    and str(e.skill_id).isdigit() and not e.wire_entity_id]
        if not suspects:
            return events
        raw = p.decode_v576_embedded_direct_damage_events(packet.payload,packet.inferred_direction(),decoder.damage_skill_ids)
        for event in suspects:
            candidates = [h for h in raw if h.damage == event.amount
                          and h.damage_object_id == event.wire_damage_object_id
                          and h.damage_result_group_id == event.wire_result_group_id
                          and f'0x{h.source_actor_id:08X}' == event.source_id.split('@')[0]]
            if len(candidates) != 1:
                continue
            h = candidates[0]
            oid = event.wire_damage_object_id
            band = 'zero' if not oid else 'small_lt1000' if oid<1000 else 'medium_lt100000' if oid<100000 else 'runtime_ge100000'
            key = (event.job,event.skill_id,event.attribution_method,h.layout,band)
            counts[key] += 1
            if len(examples[key]) < 3:
                examples[key].append(dict(packet=serial,time=packet.timestamp_seconds,
                    wire=packet.payload.hex(),wire_sha256=hashlib.sha256(packet.payload).hexdigest(),
                    direction=packet.inferred_direction(),event=asdict(event),raw_hit=asdict(h)))
        return events

    decoder.feed_events = feed
    try:
        source.poll()
    finally:
        source.close()
    for name,f in modules.items():
        assert hashlib.sha256((args.runtime_dir/f).read_bytes()).hexdigest() == hashes[name]
    result = dict(capture=str(args.capture),capture_sha256=hashlib.sha256(args.capture.read_bytes()).hexdigest(),
                  runtime_hashes=hashes,policy='Names diagnostic only; no attribution changes or denominator exclusions.',
                  rows=[dict(job=k[0],skill=k[1],method=k[2],layout=k[3],object_band=k[4],hits=v,
                             examples=examples[k]) for k,v in counts.most_common()])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),hits=sum(counts.values()),groups=len(counts)),ensure_ascii=False))


if __name__ == '__main__':
    main()
