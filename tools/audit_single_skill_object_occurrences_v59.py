"""Inspect every raw UDP occurrence of selected damage-object IDs.

This is a focused, read-only discovery aid.  Token co-occurrence is never
treated as skill identity; the output is intended to locate the lifecycle
record that the production decoder still misses.
"""
from __future__ import annotations

import argparse
from dataclasses import fields, is_dataclass
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from tools.analyze_npcap_trace import read_trace
from tools.probe_compact_entity_attack_selectors import walk


def _contexts(body: bytes, token: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    offset = body.find(token, 9)
    while offset >= 0:
        start = max(0, offset - 28)
        end = min(len(body), offset + len(token) + 44)
        rows.append(
            {
                "offset": offset,
                "context_start": start,
                "context_hex": body[start:end].hex(),
            }
        )
        offset = body.find(token, offset + 1)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--objects", required=True)
    parser.add_argument("--actor", type=lambda value: int(value, 0), default=0)
    parser.add_argument("--start", required=True, type=float)
    parser.add_argument("--end", required=True, type=float)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    object_ids = tuple(
        int(value, 0) for value in args.objects.split(",") if value.strip()
    )
    tokens = {value: value.to_bytes(4, "little") for value in object_ids}
    first_timestamp: float | None = None
    rows: list[dict[str, object]] = []
    decoded_events: list[dict[str, object]] = []
    source = V576DiagnosticReplaySource(args.capture, DATA_DIR, {})
    decoder = source._decoder
    transport = protocol.V576TransportReassembler()
    ledger = V576EventLedger()
    for frame, (timestamp, _adapter, original_length, raw) in enumerate(
        read_trace(args.capture), 1
    ):
        if first_timestamp is None:
            first_timestamp = timestamp
        elapsed = timestamp - first_timestamp
        if elapsed > args.end:
            break
        packet = protocol.decode_captured_frame(timestamp, raw, original_length)
        if packet is None:
            continue
        for logical_packet in transport.feed(packet):
            if not ledger.accept(logical_packet):
                continue
            for event in decoder.feed_events(logical_packet):
                event_ms = int(getattr(event, "timestamp_ms", -1))
                if event_ms < round(args.start * 1000.0) or event_ms > round(args.end * 1000.0):
                    continue
                values: dict[str, object] = {}
                if is_dataclass(event):
                    for field in fields(event):
                        value = getattr(event, field.name)
                        if isinstance(value, (str, int, float, bool)) or value is None:
                            values[field.name] = value
                if args.actor:
                    actor_tokens = {
                        args.actor,
                        f"0x{args.actor:08X}",
                        f"{args.actor}",
                    }
                    observed = {
                        values.get("source_id"),
                        values.get("target_id"),
                        values.get("source_actor_id"),
                        values.get("target_actor_id"),
                        values.get("wire_source_actor_id"),
                        values.get("wire_target_actor_id"),
                    }
                    if not (actor_tokens & observed):
                        continue
                decoded_events.append({"type": type(event).__name__, **values})
        if elapsed < args.start or packet.transport != "UDP":
            continue
        direction = packet.inferred_direction()
        for message_index, wire in enumerate(protocol.split_v576_payload(packet.payload)):
            exact_hits = protocol.decode_v576_embedded_direct_damage_events(
                wire, direction, ()
            )
            hit_objects = {
                int(hit.damage_object_id or 0)
                for hit in exact_hits
                if int(hit.damage_object_id or 0) in tokens
            }
            registrations = protocol.decode_v576_server_damage_object_registrations(
                wire, direction, include_unmapped=True
            )
            registration_rows = [
                {
                    "owner": int(row.source_actor_id),
                    "object": int(row.damage_object_id),
                    "template": int(row.projectile_template_id),
                    "parent": int(row.parent_damage_object_id),
                    "layout": row.layout,
                    "record_offset": int(row.record_offset),
                }
                for row in registrations
                if int(row.damage_object_id) in tokens
            ]
            seen_bodies: set[bytes] = set()
            for phase in protocol._v576_candidate_xor_phases(wire):
                body = protocol._decode_v576_body(wire, phase)
                if body in seen_bodies:
                    continue
                seen_bodies.add(body)
                occurrences = {
                    str(object_id): _contexts(body, token)
                    for object_id, token in tokens.items()
                    if token in body[9:]
                }
                if not occurrences:
                    continue
                parsed = walk(wire)
                parsed_rows: list[dict[str, object]] = []
                if parsed and int(parsed[0]) == int(phase):
                    _parsed_phase, _parsed_body, items = parsed
                    parsed_rows = [
                        {
                            "actor": int(actor),
                            "marker": int(marker),
                            "op": int(op),
                            "length": int(length),
                        }
                        for actor, marker, op, length in items
                    ]
                rows.append(
                    {
                        "frame": frame,
                        "elapsed_ms": round(elapsed * 1000.0),
                        "message_index": message_index,
                        "direction": direction,
                        "wire_signature": wire[5:9].hex(),
                        "wire_length": len(wire),
                        "xor_phase": int(phase),
                        "direct_hit_objects": sorted(hit_objects),
                        "registrations": registration_rows,
                        "parsed_records": parsed_rows,
                        "occurrences": occurrences,
                    }
                )

    result = {
        "schema": "single-skill-object-occurrences-v59",
        "capture": str(args.capture),
        "range_seconds": [args.start, args.end],
        "object_ids": list(object_ids),
        "policy": "raw token occurrence is discovery evidence only, never skill identity",
        "decoded_events": decoded_events,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"rows": len(rows), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
