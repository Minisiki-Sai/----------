"""Find pre-hit wire occurrences for Soul Eater 5207 damage objects.

The input report is produced by ``audit_soul_eater_field_v135.py``.  This
tool deliberately does not infer a skill from time.  Packet index is used
only to select a bounded raw search region before an already observed Hit;
every reported occurrence is a byte-exact owner/object candidate for later
protocol work.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_captured_frame,
)

try:  # noqa: E402
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover
    from tools.analyze_npcap_trace import read_trace


def _targets(report_path: Path) -> tuple[Path, list[dict[str, int]]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema") == "dark-shaman-resent-v2-audit-v1":
        rows: dict[tuple[int, int], dict[str, int]] = {}
        for hit in report.get("events", ()):
            category = (
                "known_5207"
                if str(hit.get("skill_id")) == "5207"
                else "unknown_phase"
                if (
                    str(hit.get("skill_id")) == "-1"
                    and bool(hit.get("field_phase_match"))
                )
                else ""
            )
            if not category or int(hit.get("damage_object_id", 0)) <= 0:
                continue
            # This report does not retain packet ordinals.  A zero ordinal
            # asks the scanner to search the complete capture; the exact
            # direct37 slot is still identified later from object/source.
            key = (int(hit["timestamp_ms"]), int(hit["damage_object_id"]))
            rows[key] = {
                "hit_packet": 0,
                "hit_offset": int(hit.get("wire_record_offset", 0)),
                "object": key[1],
                "actor": int(hit.get("wire_source_actor_id", 0)),
                "method": str(hit.get("method", "")),
                "generation_template": int(hit.get("object_template", 0)),
                "category": category,
                "timestamp_ms": key[0],
                "amount": int(hit.get("amount", 0)),
                "result_group": int(hit.get("result_group", 0)),
            }
        return Path(report["capture"]), list(rows.values())
    capture = report["captures"][0]
    rows: dict[tuple[int, int, int], dict[str, int]] = {}
    for cast in capture["casts"]:
        for hit in cast["damage"]:
            if (
                int(hit.get("skill_id", -1)) != 5207
                or "direct37" not in str(hit.get("layout", ""))
                or int(hit.get("object", 0)) <= 0
            ):
                continue
            key = (
                int(hit["packet_index"]),
                int(hit["record_offset"]),
                int(hit["object"]),
            )
            rows[key] = {
                "hit_packet": key[0],
                "hit_offset": key[1],
                "object": key[2],
                "actor": int(cast["actor"]),
                "method": str(hit.get("method", "")),
                "generation_template": int(hit.get("generation_template", 0)),
            }
    return Path(capture["capture"]), list(rows.values())


def audit(
    report_path: Path, lookback_packets: int, lookahead_packets: int
) -> dict[str, object]:
    capture_path, targets = _targets(report_path)
    by_object: dict[int, list[dict[str, int]]] = defaultdict(list)
    for target in targets:
        by_object[target["object"]].append(target)
    occurrences: list[dict[str, object]] = []
    transport = V576TransportReassembler()
    packet_index = 0
    origin_timestamp: float | None = None
    full_capture_scan = bool(targets) and not any(
        int(target["hit_packet"]) > 0 for target in targets
    )

    def accept(packet) -> None:
        nonlocal packet_index, origin_timestamp
        packet_index += 1
        if origin_timestamp is None:
            origin_timestamp = float(packet.timestamp_seconds)
        active = (
            set(by_object)
            if full_capture_scan
            else {
                object_id
                for object_id, hits in by_object.items()
                if any(
                    hit["hit_packet"] - lookback_packets
                    <= packet_index
                    <= hit["hit_packet"] + lookahead_packets
                    for hit in hits
                )
            }
        )
        if not active:
            return
        for phase in _v576_candidate_xor_phases(packet.payload):
            decoded = _decode_v576_body(packet.payload, phase)
            for object_id in active:
                needle = object_id.to_bytes(4, "little")
                offset = decoded.find(needle, 9)
                while offset >= 0:
                    matching_hits = (
                        by_object[object_id]
                        if full_capture_scan
                        else [
                            hit for hit in by_object[object_id]
                            if hit["hit_packet"] - lookback_packets
                            <= packet_index
                            <= hit["hit_packet"] + lookahead_packets
                        ]
                    )
                    for hit in matching_hits:
                        is_own_direct37_slot = (
                            (
                                packet_index == hit["hit_packet"]
                                or full_capture_scan
                            )
                            and offset == hit["hit_offset"] + 13
                            and decoded[max(0, hit["hit_offset"]):hit["hit_offset"] + 3]
                            == b"\x08\x73\x00"
                            and int.from_bytes(
                                decoded[hit["hit_offset"] + 8:hit["hit_offset"] + 12],
                                "little",
                            ) == hit["actor"]
                            and int.from_bytes(
                                decoded[hit["hit_offset"] + 19:hit["hit_offset"] + 27],
                                "little",
                            ) == int(hit.get("amount", 0))
                        )
                        occurrences.append(
                            {
                                **hit,
                                "packet_index": packet_index,
                                "packet_timestamp_ms": round(
                                    (float(packet.timestamp_seconds) - origin_timestamp)
                                    * 1000.0
                                ),
                                "delta_ms": (
                                    round(
                                        (float(packet.timestamp_seconds) - origin_timestamp)
                                        * 1000.0
                                    )
                                    - int(hit.get("timestamp_ms", 0))
                                ),
                                "packets_before_hit": hit["hit_packet"] - packet_index,
                                "phase": phase,
                                "offset": offset,
                                "is_own_direct37_slot": is_own_direct37_slot,
                                "same_owner_typed09": (
                                    offset >= 12
                                    and decoded[offset - 8] == 0x09
                                    and int.from_bytes(
                                        decoded[offset - 12 : offset - 8],
                                        "little",
                                    ) == hit["actor"]
                                ),
                                "direction": packet.inferred_direction(),
                                "transport": packet.transport,
                                "payload_length": len(packet.payload),
                                "packet_signature": (
                                    packet.payload[5:9].hex()
                                    if len(packet.payload) >= 9 else ""
                                ),
                                "context_hex": decoded[
                                    max(0, offset - 24):offset + 48
                                ].hex(),
                            }
                        )
                    offset = decoded.find(needle, offset + 1)

    for timestamp, _adapter, original_length, frame in read_trace(capture_path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in transport.feed(packet):
            accept(logical)
    for logical in transport.flush():
        accept(logical)

    non_hit = [row for row in occurrences if not row["is_own_direct37_slot"]]
    return {
        "schema": "soul-eater-field-object-chain-v136",
        "policy": "byte-exact object occurrence inventory; no time-based skill identity",
        "capture": str(capture_path),
        "report": str(report_path),
        "lookback_packets": lookback_packets,
        "lookahead_packets": lookahead_packets,
        "target_hits": len(targets),
        "target_objects": len(by_object),
        "occurrences": occurrences,
        "non_direct37_occurrences": non_hit,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--lookback-packets", type=int, default=3000)
    parser.add_argument("--lookahead-packets", type=int, default=3000)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(
        args.report,
        max(1, args.lookback_packets),
        max(0, args.lookahead_packets),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)
    print("targets", result["target_hits"], "objects", result["target_objects"])
    print("non-direct37 occurrences", len(result["non_direct37_occurrences"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
