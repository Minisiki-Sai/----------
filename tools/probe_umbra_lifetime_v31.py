"""Small raw windows for the 8s default versus the documented 10s object.

Candidate inventory only: no promotion, no scoring, no aggregation.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records


def encode_bytes(value):
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(type(value).__name__)


def probe(capture, limit=100, template=5717, skills=(6401, 6553)):
    roots, lives, completed = [], {}, []
    offset_time = None
    with capture.open('rb') as f:
        assert f.read(6) == b'DNDP1\0'
        frame = 0
        while header := f.read(17):
            file_offset = f.tell()-17
            t, _, original, n = struct.unpack('<dBII', header)
            frame += 1
            packet = p.decode_captured_frame(t, f.read(n), original)
            if not packet:
                continue
            if offset_time is None:
                offset_time = t
            if t-offset_time > limit:
                break
            if packet.transport != 'UDP':
                continue
            direction = packet.inferred_direction()
            for part, wire in enumerate(p.split_v576_payload(packet.payload)):
                anchor = dict(frame=frame, file_offset=file_offset, message=part,
                              time=t, time_relative=t-offset_time, direction=direction,
                              wire=wire.hex(), wire_sha256=sha256(wire).hexdigest())
                for action in p.decode_v576_structured_actions(wire, direction,
                        frozenset(skills), (), allow_unknown_actor_ids=True):
                    if action.kind == 'skill_action':
                        roots.append(dict(anchor, action=asdict(action)))
                for reg in p.decode_v576_server_damage_object_registrations(
                        wire, direction, (), include_unmapped=True):
                    key = reg.source_actor_id, reg.damage_object_id
                    if reg.projectile_template_id == template:
                        if key in lives:
                            lives[key]['later_registrations'].append(dict(anchor, registration=asdict(reg)))
                        else:
                            life = dict(anchor, registration=asdict(reg), hits=[], later_registrations=[],
                                        roots=[a for a in roots if a['action']['source_actor_id'] == key[0]
                                               and 0 <= t-a['time'] <= 3])
                            completed.append(life)
                            lives[key] = life
                    elif key in lives:
                        lives[key]['invalidated_by'] = dict(anchor, registration=asdict(reg))
                        del lives[key]
                if not p._v576_wire_may_contain_marker(wire, (b'\x08\x73\0',)):
                    continue
                inv = inspect_udp_damage_records(wire, direction)
                if not inv.complete:
                    continue
                for hit in inv.damage_records:
                    life = lives.get((hit.source_actor_id, hit.damage_object_id))
                    if hit.family != 'direct37' or not hit.admissible_fields or life is None:
                        continue
                    if 0 <= t-life['time'] <= 12:
                        life['hits'].append(dict(anchor, age=t-life['time'], record=asdict(hit)))
    return dict(capture=str(capture), capture_sha256=sha256(capture.read_bytes()).hexdigest(),
                scope=f'first {limit}s raw only, not full replay acceptance', lives=completed)


if __name__ == '__main__':
    capture, output = map(Path, sys.argv[1:3])
    grudge = len(sys.argv)>4 and sys.argv[4] == 'grudge'
    result = probe(capture, float(sys.argv[3]) if len(sys.argv)>3 else 100,
                   5508 if grudge else 5717, (5218,) if grudge else (6401,6553))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=encode_bytes), encoding='utf-8')
    print(json.dumps([dict(object=x['registration']['damage_object_id'], owner=x['registration']['source_actor_id'],
          registration=x['time_relative'], roots=len(x['roots']),
          ages=[round(h['age'],3) for h in x['hits']],
          later=len(x['later_registrations']), invalidated='invalidated_by' in x)for x in result['lives']],ensure_ascii=False))
