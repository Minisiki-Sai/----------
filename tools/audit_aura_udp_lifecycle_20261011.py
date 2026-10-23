"""Read-only aura response/instance inventory. No production state is changed.

An instance reference outside its application is a candidate, not automatically
a removal or a range-exit certificate. Physical packet coordinates are retained.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body, _v576_candidate_xor_phases, decode_captured_frame,
    decode_v576_status_events, split_v576_payload,
)
from dragon_nest_builder.live_dps.udp_damage_records import _walk

AURAS = frozenset({2213, 2214, 3211, 3213})


def wires(path):
    origin = None
    for frame_index, (stamp, adapter, original, frame) in enumerate(read_trace(path)):
        if adapter >= 251:
            continue
        if origin is None:
            origin = stamp
        packet = decode_captured_frame(stamp, frame, original)
        if packet is None or packet.transport.upper() != "UDP":
            continue
        for message_index, wire in enumerate(split_v576_payload(packet.payload)):
            yield stamp - origin, frame_index, message_index, packet.inferred_direction(), wire


def audit(path):
    rows = []
    by_instance = defaultdict(list)
    applications = set()
    counts = Counter()
    for seconds, frame, message, direction, wire in wires(path):
        counts["udp_messages"] += 1
        for status in decode_v576_status_events(wire, direction, AURAS):
            if status.skill_id not in AURAS or not status.permanent:
                continue
            body = _decode_v576_body(wire, status.xor_phase)
            tail = status.record_offset + 20 + len(status.parameters.encode("utf-8"))
            # The raw tail is reported even for effects whose instance is not
            # retained by the production status decoder (e.g. class 38).
            instance = None
            if tail + 5 <= len(body) and body[tail] == 0:
                value = int.from_bytes(body[tail + 1:tail + 5], "little")
                if value not in (0, 0xffffffff):
                    instance = value
                    applications.add((frame, message, status.xor_phase, tail + 1))
            row = dict(t=round(seconds, 6), frame=frame, message=message,
                       phase=status.xor_phase, offset=status.record_offset,
                       source=status.source_actor_id, target=status.target_actor_id,
                       skill=status.skill_id, effect_class=status.effect_class_id,
                       code=status.effect_code, parameters=status.parameters,
                       permanent=status.permanent, instance=instance)
            rows.append(row)
            if instance:
                by_instance[instance].append(row)
    references = []
    shape_counts = Counter()
    if by_instance:
        pattern = re.compile(b"|".join(re.escape(i.to_bytes(4, "little"))
                                      for i in sorted(by_instance)))
        for seconds, frame, message, direction, wire in wires(path):
            for phase in _v576_candidate_xor_phases(wire):
                body = _decode_v576_body(wire, phase)
                typed = _walk(body)
                boundaries = {(r[1] + 3, r[3], r[4]) for r in typed or ()}
                for match in pattern.finditer(body, 10):
                    offset = match.start()
                    if (frame, message, phase, offset) in applications:
                        continue
                    instance = int.from_bytes(match.group(), "little")
                    prior = [r for r in by_instance[instance] if r["t"] <= seconds]
                    if not prior:
                        continue
                    application = max(prior, key=lambda r: r["t"])
                    # A structural candidate: target:u32, kind:u8, size:u16,
                    # instance:u32. We do not assume what the kind means.
                    prefix = body[max(10, offset - 7):offset]
                    same_target = (len(prefix) == 7 and
                                   int.from_bytes(prefix[:4], "little") == application["target"])
                    kind = prefix[4] if len(prefix) == 7 else None
                    size = int.from_bytes(prefix[5:7], "little") if len(prefix) == 7 else None
                    shape_counts[(kind, size, same_target, direction)] += 1
                    references.append(dict(t=round(seconds, 6), frame=frame,
                                           message=message, phase=phase, offset=offset,
                                           instance=instance, apply_t=application["t"],
                                           target=application["target"],
                                           skill=application["skill"],
                                           effect_class=application["effect_class"],
                                           same_target=same_target, kind=kind, size=size,
                                           complete_typed_stream=typed is not None,
                                           typed_boundary=(offset, kind, size) in boundaries,
                                           trailing_byte=(body[offset+4] if offset+4 < len(body) else None),
                                           direction=direction, wirelen=len(wire),
                                           context=body[max(10, offset-12):offset+20].hex()))
    groups = defaultdict(list)
    for row in rows:
        groups[(row["skill"], row["source"], row["target"], row["effect_class"])].append(row)
    matched = [r for r in references if r["same_target"]]
    return dict(capture=str(path), counters=dict(counts), application_count=len(rows),
                instance_count=len(by_instance), applications=rows,
                groups=[dict(key=k, count=len(v), times=[r["t"] for r in v])
                        for k, v in groups.items()],
                reference_shapes=[dict(key=k, count=v) for k, v in shape_counts.most_common()],
                same_target_reference_count=len(matched),
                complete_typed_same_target_references=sum(r["typed_boundary"] for r in matched),
                same_target_references=matched,
                other_reference_count=len(references)-len(matched),
                other_reference_samples=[r for r in references if not r["same_target"]][:12],
                limitation="Repeated applications/references alone do not certify range entry/exit.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    for path in args.captures:
        result = audit(path)
        if args.summary:
            result.pop("applications")
            result["groups"] = [{**r, "times": r["times"][:8]} for r in result["groups"]]
            result["same_target_references"] = result["same_target_references"][:6]
        print(json.dumps(result, ensure_ascii=False), flush=True)
