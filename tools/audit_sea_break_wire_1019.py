"""Read-only packet evidence for head/tail breaks; independent of skill whitelist."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_party_hp_wire import _iter_packets
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body, _V576_BODY_XOR_KEY, _v576_wire_may_contain_marker,
)

OUT = ROOT / "outputs/verification/sea_support_timeline_1019"
CAP = Path("C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics")


def run(n):
    stem = f"1019噬魂竞速第{n}把"
    h = json.loads((OUT / "hp_actions" / (stem + ".json")).read_text(encoding="utf8"))
    hp = [x for x in h["BossHealthEvent"] if x["target_id"].endswith("@4237023")]
    if not hp:
        return
    actors = {int(x["target_id"].split("@")[0], 16) for x in hp}
    start = min(x["timestamp_ms"] for x in hp) / 1000 - 2
    markers = (b"\x0e\x16\x00", b"\x03\x14\x00")
    origin = None
    rows = []
    seen = set()
    for packet in _iter_packets(CAP / (stem + ".dpscap")):
        if origin is None:
            origin = packet.timestamp_seconds
        sec = packet.timestamp_seconds - origin
        if sec < start or packet.transport.upper() != "UDP":
            continue
        if packet.inferred_direction() != "server_to_client":
            continue
        if not _v576_wire_may_contain_marker(packet.payload, markers, start_offset=13):
            continue
        matches = {}
        for phase in range(len(_V576_BODY_XOR_KEY)):
            body = _decode_v576_body(packet.payload, phase)
            found = []
            for marker in markers:
                off = body.find(marker, 13)
                while off >= 0:
                    actor = int.from_bytes(body[off-4:off], "little")
                    if actor in actors and off + 9 <= len(body):
                        if marker == markers[0]:
                            value = int.from_bytes(body[off+3:off+7], "little")
                            valid = 30000 <= value <= 999999 and body[off+7] == 1
                        else:
                            value = body[off+3]
                            valid = value < 128 and body[off+7:off+9] == b"\0\0"
                        if valid:
                            found.append(dict(t=round(sec, 3), actor=actor,
                                marker=marker.hex(), value=value, phase=phase,
                                record=body[off-4:off+25].hex()))
                    off = body.find(marker, off+1)
            if found:
                matches[phase] = found
        if len(matches) == 1:
            for row in next(iter(matches.values())):
                key = (row["t"], row["marker"], row["value"], row["record"])
                if key not in seen:
                    rows.append(row)
                    seen.add(key)
    dest = OUT / "break_wire" / (stem + ".json")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(dict(file=stem, origin=origin, rows=rows),
                               ensure_ascii=False, indent=2), encoding="utf8")
    print(stem, len(rows), [(x["t"], x["marker"], x["value"]) for x in rows
                          if x["value"] in (17, 18, 325461, 325462)], flush=True)


if __name__ == "__main__":
    for number in sys.argv[1:] or ["五", "十", "十四", "十七", "十九"]:
        run(number)
