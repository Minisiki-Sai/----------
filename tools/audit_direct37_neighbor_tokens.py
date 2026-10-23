from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import (  # noqa: E402
    CombatEvent,
    SkillCastEvent,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


def _direct_body(packet: object, decoded: object) -> bytes | None:
    payload = bytes(getattr(packet, "payload", b""))
    marker = int(getattr(decoded, "record_offset", 0) or 0)
    source = int(getattr(decoded, "source_actor_id", 0) or 0)
    damage = int(getattr(decoded, "damage", 0) or 0)
    for phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, phase)
        if marker < 0 or marker + 27 > len(body):
            continue
        if body[marker : marker + 3] != b"\x08\x73\x00":
            continue
        if int.from_bytes(body[marker + 8 : marker + 12], "little") != source:
            continue
        if int.from_bytes(body[marker + 19 : marker + 27], "little") != damage:
            continue
        return body
    return None


def audit_capture(path: Path, window_ms: int) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed
    packet_rows: list[dict[str, Any]] = []
    direct_rows: list[dict[str, Any]] = []

    def feed(packet, packet_features=None):
        packet_index = len(packet_rows)
        event = original_feed(packet, packet_features)
        decoded = decoder.last_decoded_message
        payload = bytes(packet.payload)
        phase_bodies = tuple(
            _decode_v576_body(payload, phase)
            for phase in _v576_candidate_xor_phases(payload)
        )
        packet_rows.append(
            {
                "timestamp_ms": round(float(packet.timestamp_seconds) * 1000),
                "direction": packet.inferred_direction(),
                "signature": payload[5:9].hex() if len(payload) >= 9 else "",
                "length": len(payload),
                "bodies": phase_bodies,
                "event_kind": type(event).__name__ if event is not None else "",
                "event_skill_id": (
                    str(event.skill_id) if isinstance(event, SkillCastEvent) else ""
                ),
                "decoded_kind": str(getattr(decoded, "kind", "") or ""),
                "decoded_skill_id": int(getattr(decoded, "skill_id", 0) or 0),
                "decoded_skill_exact": bool(
                    getattr(decoded, "skill_id_exact", False)
                ),
            }
        )
        layout = str(getattr(decoded, "layout", "") or "")
        if (
            isinstance(event, CombatEvent)
            and not event.healing
            and decoded is not None
            and "direct37" in layout
        ):
            body = _direct_body(packet, decoded)
            marker = int(getattr(decoded, "record_offset", 0) or 0)
            if body is not None and marker + 17 <= len(body):
                direct_rows.append(
                    {
                        "packet_index": packet_index,
                        "timestamp_ms": int(event.timestamp_ms),
                        "source_id": str(event.source_id),
                        "assigned_skill_id": str(event.skill_id),
                        "assigned_skill_name": str(event.skill_name),
                        "assigned_method": str(event.attribution_method),
                        "wire_skill_id": int(getattr(decoded, "skill_id", 0) or 0),
                        "wire_skill_exact": bool(
                            getattr(decoded, "skill_id_exact", False)
                        ),
                        "result_group_id": int.from_bytes(
                            body[marker + 4 : marker + 8], "little"
                        ),
                        "damage_object_id": int.from_bytes(
                            body[marker + 13 : marker + 17], "little"
                        ),
                    }
                )
        return event

    decoder.feed = feed
    try:
        source.poll()
    finally:
        source.close()

    timestamps = [int(row["timestamp_ms"]) for row in packet_rows]
    hits = Counter()
    samples: list[dict[str, Any]] = []
    for direct in direct_rows:
        if direct["wire_skill_exact"]:
            continue
        packet_index = int(direct["packet_index"])
        center = int(packet_rows[packet_index]["timestamp_ms"])
        left = bisect_left(timestamps, center - window_ms)
        right = bisect_right(timestamps, center + window_ms)
        token_matches: list[dict[str, Any]] = []
        for token_name in ("result_group_id", "damage_object_id"):
            token = int(direct[token_name])
            if token <= 0:
                continue
            encoded4 = token.to_bytes(4, "little")
            encoded3 = encoded4[:3]
            for other_index in range(left, right):
                if other_index == packet_index:
                    continue
                other = packet_rows[other_index]
                offsets4: list[int] = []
                offsets3: list[int] = []
                for body in other["bodies"]:
                    start = body.find(encoded4)
                    if start >= 0:
                        offsets4.append(start)
                    start3 = body.find(encoded3)
                    if start3 >= 0:
                        offsets3.append(start3)
                if not offsets4 and not offsets3:
                    continue
                match = {
                    "token": token_name,
                    "delta_ms": int(other["timestamp_ms"]) - center,
                    "direction": other["direction"],
                    "signature": other["signature"],
                    "length": other["length"],
                    "offsets4": offsets4,
                    "offsets3": offsets3,
                    "event_kind": other["event_kind"],
                    "event_skill_id": other["event_skill_id"],
                    "decoded_kind": other["decoded_kind"],
                    "decoded_skill_id": other["decoded_skill_id"],
                    "decoded_skill_exact": other["decoded_skill_exact"],
                }
                token_matches.append(match)
                hits[
                    (
                        token_name,
                        str(other["direction"]),
                        str(other["signature"]),
                        int(other["length"]),
                        str(other["event_kind"]),
                        str(other["decoded_kind"]),
                    )
                ] += 1
        special_matches = [
            match
            for match in token_matches
            if match["signature"] == "6eccdd00"
            and match["direction"] == "client_to_server"
        ]
        if special_matches:
            for match in special_matches:
                other_index = next(
                    (
                        index
                        for index in range(left, right)
                        if index != packet_index
                        and packet_rows[index]["signature"]
                        == match["signature"]
                        and int(packet_rows[index]["timestamp_ms"]) - center
                        == match["delta_ms"]
                    ),
                    -1,
                )
                if other_index < 0:
                    continue
                match["decoded_bodies_hex"] = [
                    body.hex(" ") for body in packet_rows[other_index]["bodies"]
                ]
            if len(samples) < 300:
                samples.append({**direct, "matches": special_matches[:10]})
        elif token_matches and len(samples) < 80:
            samples.append({**direct, "matches": token_matches[:40]})

    summary = [
        {
            "token": key[0],
            "direction": key[1],
            "packet_signature": key[2],
            "payload_length": key[3],
            "event_kind": key[4],
            "decoded_kind": key[5],
            "matches": count,
        }
        for key, count in hits.most_common()
    ]
    return {
        "capture": str(path),
        "window_ms": window_ms,
        "packets": len(packet_rows),
        "direct37_rows": len(direct_rows),
        "nonexact_rows": sum(not row["wire_skill_exact"] for row in direct_rows),
        "neighbor_match_summary": summary,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--window-ms", type=int, default=1000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_capture(args.capture.resolve(), max(1, args.window_ms))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
