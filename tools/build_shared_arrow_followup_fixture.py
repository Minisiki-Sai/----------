"""Extract actual wire witnesses; no manufactured action/owner/origin state."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.damage_object_origins import _SPEC_BY_PROJECTILE_TEMPLATE


def main():
    folder = ROOT / 'outputs/named_hit_chain_followup_20260908'
    data = json.loads((folder / 'windows_0812.json').read_text(encoding='utf-8'))
    case = next(c for c in data['cases'] if c['reason'] == 'object_only_provisional_lineage')
    owner, child = case['anchor']['state']['wire_source'], case['anchor']['state']['object']
    parent = case['anchor']['state']['origin']['parent_damage_object_id']
    end = case['anchor']['time']
    start = end - 8
    out = []
    with Path(data['capture']).open('rb') as f:
        assert f.read(6) == b'DNDP1\0'
        while head := f.read(17):
            offset = f.tell() - 17
            t, adapter, original, size = struct.unpack('<dBII', head)
            raw = f.read(size)
            if t > end + .00001:
                break
            packet = p.decode_captured_frame(t, raw, original)
            if not packet or packet.transport != 'UDP':
                continue
            for wire in p.split_v576_payload(packet.payload):
                roles = []
                regs = p.decode_v576_server_damage_object_registrations(wire, None, _SPEC_BY_PROJECTILE_TEMPLATE)
                for reg in regs:
                    if reg.source_actor_id == owner and reg.damage_object_id in (parent, child):
                        roles.append('parent' if reg.damage_object_id == parent else 'child')
                if start <= t <= end:
                    action = p.decode_v576_payload(wire, packet.inferred_direction(), {1208, 1352}, {1208, 1352}, (owner,))
                    actions = list(p.decode_v576_structured_actions(wire, packet.inferred_direction(), {1208,1352}, (owner,)))
                    if action:
                        actions.append(action)
                    if any(a.source_actor_id == owner and a.kind in ('skill_cast','skill_action')
                           and a.skill_id in (1208,1352) for a in actions):
                        roles.append('root')
                if wire.hex() == case['anchor']['wire']:
                    roles.append('hit')
                # Independently decoded target identity is only context, never skill evidence.
                monster = p.decode_v576_monster_spawn(wire, packet.inferred_direction())
                if monster and monster.actor_id == case['anchor']['event']['wire_target_actor_id']:
                    roles.append('target')
                if roles:
                    out.append(dict(t=t, file_offset=offset, roles=roles, transport=packet.transport,
                        source_ip=packet.source_ip, destination_ip=packet.destination_ip,
                        source_port=packet.source_port, destination_port=packet.destination_port,
                        wire_hex=wire.hex(), wire_sha256=hashlib.sha256(wire).hexdigest()))
    result = dict(capture=data['capture'], owner=owner, child=child, parent=parent,
        expected=case['anchor']['event'], packets=out)
    (folder / 'shared_arrow_raw_chain.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print([(r['t'],r['roles']) for r in out])


if __name__ == '__main__':
    main()
