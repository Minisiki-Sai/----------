"""Read-only real-wire verification of the narrowly scoped C2S relay reader."""
from __future__ import annotations
import argparse, dataclasses, hashlib, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body, decode_captured_frame,
    decode_v576_damage_object_registration, split_v576_payload,
)
from dragon_nest_builder.live_dps.player_root_action_objects import _parse_player_root

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    fixtures = json.loads((ROOT / "outputs/verification/relay_envelope_v475/fixtures.json").read_text(encoding="utf8"))
    digest = hashlib.sha256(args.capture.read_bytes()).hexdigest()
    assert {f["capture_sha256"] for f in fixtures.values()} == {digest}
    wanted = {bytes.fromhex(f["outer_hex"]): key for key, f in fixtures.items()}
    matched = set()
    for stamp, adapter, size, raw in read_trace(args.capture):
        if adapter >= 240:
            continue
        packet = decode_captured_frame(stamp, raw, size)
        if packet is None or packet.transport != "UDP" or packet.inferred_direction() != "client_to_server":
            continue
        key = wanted.get(packet.payload)
        if key is not None:
            matched.add(key)
    assert matched == set(fixtures)
    results = []
    for key, fixture in fixtures.items():
        outer = bytes.fromhex(fixture["outer_hex"])
        inner = bytes.fromhex(fixture["inner_hex"][0])
        assert hashlib.sha256(outer).hexdigest() == fixture["outer_sha256"]
        assert hashlib.sha256(inner).hexdigest() == fixture["inner_sha256"][0]
        relayed = decode_v576_damage_object_registration(outer, "client_to_server", transport="UDP")
        ordinary = decode_v576_damage_object_registration(inner, "client_to_server", transport="UDP")
        assert ordinary is not None
        assert relayed == dataclasses.replace(ordinary, record_offset=21, xor_phase=11)
        body = _decode_v576_body(outer, relayed.xor_phase)[relayed.record_offset + 1:]
        assert body == _decode_v576_body(inner, ordinary.xor_phase)[ordinary.record_offset + 1:]
        synthetic_kind9 = body[:4] + b"\x09" + (len(body) - 4).to_bytes(2, "little") + body[4:]
        root = _parse_player_root(synthetic_kind9)
        assert root is not None
        assert root.object_id == relayed.damage_object_id and root.config_row == relayed.projectile_template_id
        assert split_v576_payload(outer) == (outer,)
        results.append({"outer_sha256": fixture["outer_sha256"], "root": dataclasses.asdict(root)})
    print(json.dumps({"capture_sha256": digest, "matched_real_fixtures": len(matched), "records": results}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
