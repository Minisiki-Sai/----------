from __future__ import annotations

"""Audit externally reported v576 packet findings against local ``.dpscap`` files.

The scanner intentionally reads below the normal decoder white lists.  It is a
read-only proof tool for three claims that must be verified per server build:

* delayed, byte-identical packets crossing different captured connections;
* the 127-byte wrapped damage-object registration;
* ``1C 1B 00 xx`` HP-delta tails and the skill id in their last four bytes.

Usage::

    python tools/audit_external_toolbox_findings.py capture1.dpscap [...]
"""

import argparse
import collections
import json
import struct
import sys
from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.event_ledger import v576_wire_event_id
from dragon_nest_builder.live_dps.npcap import (
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
)
from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_STATUS_DAMAGE_TAILS,
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_captured_frame,
    V576TransportReassembler,
)


MAGIC = b"DNDP1\x00"
FRAME_HEADER = struct.Struct("<dBII")
SKIP_ADAPTERS = {
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
}
TUNNEL_PREFIX = b"\x00\x00\x5e\x00\x00\x00"


@dataclass(frozen=True, slots=True)
class _SeenPacket:
    timestamp: float
    connection: tuple[object, ...]


def _connection(packet: object) -> tuple[object, ...]:
    return (
        str(getattr(packet, "transport", "")).upper(),
        getattr(packet, "source_ip", ""),
        getattr(packet, "destination_ip", ""),
        int(getattr(packet, "source_port", 0) or 0),
        int(getattr(packet, "destination_port", 0) or 0),
    )


def _is_registration_127(payload: bytes) -> bool:
    """Match the complete, repeated-field 127-byte wrapper reported by SEA."""

    if len(payload) != 127:
        return False
    decoded = _decode_v576_body(payload, 6)
    owner_offset = 30

    def u32(relative: int) -> int:
        return int.from_bytes(
            decoded[owner_offset + relative : owner_offset + relative + 4],
            "little",
        )

    owner = u32(0)
    object_id = u32(9)
    template_id = u32(13)
    word = u32(49)
    resource = word & 0xFFFF
    return bool(
        0 < owner < 0xFFFFFFFF
        and owner == u32(45)
        and decoded[owner_offset + 4 : owner_offset + 9] == bytes(5)
        and 0 < object_id <= 0xFFFFFF
        and 0 < template_id <= 1_000_000
        and 0 < resource < 0xFFFF
        and resource == u32(65)
        and decoded[owner_offset + 53 : owner_offset + 61] == b"\xff" * 8
    )


def _status_tail_rows(payload: bytes) -> list[tuple[int, int, bytes]]:
    rows: list[tuple[int, int, bytes]] = []
    if len(payload) < 48:
        return rows
    seen: set[tuple[int, int, int, int]] = set()
    for phase in _v576_candidate_xor_phases(payload):
        decoded = _decode_v576_body(payload, phase)
        if not int.from_bytes(decoded[10:14], "little"):
            continue
        for subtype in range(5):
            marker = bytes((0x1C, 0x1B, 0x00, subtype))
            offset = decoded.find(marker, 9)
            while offset >= 0:
                if offset >= 4 and offset + 30 <= len(decoded):
                    target = int.from_bytes(decoded[offset - 4 : offset], "little")
                    delta = int.from_bytes(
                        decoded[offset + 4 : offset + 12], "little", signed=True
                    )
                    source = int.from_bytes(
                        decoded[offset + 12 : offset + 16], "little"
                    )
                    tail = decoded[offset + 16 : offset + 30]
                    fingerprint = (offset, target, source, delta)
                    if (
                        target
                        and source
                        and -1_000_000_000_000 <= delta < 0
                        and tail[8:10] == b"\xb1\x00"
                        and fingerprint not in seen
                    ):
                        seen.add(fingerprint)
                        rows.append((subtype, -delta, tail))
                offset = decoded.find(marker, offset + 1)
    return rows


def analyze(path: Path) -> dict[str, object]:
    transport = V576TransportReassembler()
    first_timestamp: float | None = None
    packet_count = 0
    tunnel_outer_packets = 0
    registration_127 = 0
    status_rows: collections.Counter[tuple[int, int, bool]] = collections.Counter()
    status_damage: collections.Counter[tuple[int, int, bool]] = collections.Counter()
    mirror_delays: list[float] = []
    seen_packets: dict[tuple[str, int, bytes], _SeenPacket] = {}

    with path.open("rb") as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"not a dpscap file: {path}")
        while True:
            header = stream.read(FRAME_HEADER.size)
            if not header:
                break
            if len(header) != FRAME_HEADER.size:
                raise ValueError(f"truncated frame header: {path}")
            timestamp, adapter, original_length, captured_length = FRAME_HEADER.unpack(
                header
            )
            frame = stream.read(captured_length)
            if len(frame) != captured_length:
                raise ValueError(f"truncated frame body: {path}")
            if first_timestamp is None:
                first_timestamp = timestamp
            if adapter in SKIP_ADAPTERS:
                continue
            raw = decode_captured_frame(timestamp, frame, original_length)
            if raw is None:
                continue
            if TUNNEL_PREFIX in bytes(raw.payload):
                tunnel_outer_packets += 1
            batches = (transport.feed(raw), transport.flush_stale(float(timestamp)))
            for batch in batches:
                for packet in batch:
                    packet_count += 1
                    payload = bytes(packet.payload)
                    if _is_registration_127(payload):
                        registration_127 += 1

                    wire_id = v576_wire_event_id(payload)
                    if wire_id is not None:
                        packet_key = (
                            str(packet.transport).upper(),
                            wire_id,
                            blake2b(payload, digest_size=8).digest(),
                        )
                        previous = seen_packets.get(packet_key)
                        current_connection = _connection(packet)
                        if (
                            previous is not None
                            and previous.connection != current_connection
                        ):
                            delay = abs(packet.timestamp_seconds - previous.timestamp)
                            if delay <= 3.0:
                                mirror_delays.append(delay)
                        seen_packets[packet_key] = _SeenPacket(
                            packet.timestamp_seconds, current_connection
                        )

                    for subtype, damage, tail in _status_tail_rows(payload):
                        tail_skill = int.from_bytes(tail[10:14], "little", signed=True)
                        accepted = tail in _V576_STATUS_DAMAGE_TAILS
                        key = (subtype, tail_skill, accepted)
                        status_rows[key] += 1
                        status_damage[key] += damage

    delay_buckets = collections.Counter(
        "<=5ms"
        if delay <= 0.005
        else "<=100ms"
        if delay <= 0.1
        else "<=500ms"
        if delay <= 0.5
        else "<=1s"
        if delay <= 1.0
        else "<=3s"
        for delay in mirror_delays
    )
    status = []
    for key, count in sorted(
        status_rows.items(), key=lambda item: (-status_damage[item[0]], item[0])
    ):
        subtype, tail_skill, accepted = key
        status.append(
            {
                "subtype": subtype,
                "tailSkillId": tail_skill,
                "acceptedByCurrentDecoder": accepted,
                "records": count,
                "damage": status_damage[key],
            }
        )
    return {
        "capture": path.name,
        "packets": packet_count,
        "tunnelOuterPackets": tunnel_outer_packets,
        "registration127": registration_127,
        "crossConnectionCopiesWithin3s": len(mirror_delays),
        "copyDelayBuckets": dict(delay_buckets),
        "maximumCopyDelayMs": round(max(mirror_delays, default=0.0) * 1000, 3),
        "statusDamageTails": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = [analyze(path.resolve()) for path in args.captures]
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
