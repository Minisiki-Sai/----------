"""Compare raw engraving instance bytes across diagnostic captures.

This is an evidence tool.  It deliberately does not load tooltip calibration
routes or infer an attribute.  It prints the bytes that the client receives so
table-version migrations can be distinguished from decoder-offset mistakes.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dragon_nest_builder.live_dps.npcap import (  # noqa: E402
    IDENTITY_METADATA_ADAPTER_INDEX,
    PREBATTLE_STATE_ADAPTER_INDEX,
    PROFILE_IDENTITY_ADAPTER_INDEX,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
    decode_captured_frame,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (  # noqa: E402
    decode_tcp_item_archive_payload,
    decode_tcp_profile_payload,
)


MAGIC = b"DNDP1\x00"
FRAME_HEADER = struct.Struct("<dBII")
SPECIAL_METADATA_ADAPTERS = frozenset(
    {
        IDENTITY_METADATA_ADAPTER_INDEX,
        PROFILE_IDENTITY_ADAPTER_INDEX,
        PREBATTLE_STATE_ADAPTER_INDEX,
    }
)


def iter_wire_payloads(capture: Path):
    """Yield reassembled application frames without profile-event merging."""

    transport = V576TransportReassembler()
    with capture.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"{capture}: not a DNDP1 diagnostic capture")
        while True:
            header = handle.read(FRAME_HEADER.size)
            if not header:
                break
            if len(header) != FRAME_HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = (
                FRAME_HEADER.unpack(header)
            )
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            if adapter in SPECIAL_METADATA_ADAPTERS:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical in transport.feed(packet):
                if (
                    logical.transport.upper() == "TCP"
                    and logical.inferred_direction() == "server_to_client"
                ):
                    yield logical.payload
    for logical in transport.flush():
        if (
            logical.transport.upper() == "TCP"
            and logical.inferred_direction() == "server_to_client"
        ):
            yield logical.payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--template", type=int, action="append", default=[])
    args = parser.parse_args()
    wanted = set(args.template)
    seen: set[tuple[str, str, int, str, bytes]] = set()

    for capture in args.captures:
        found = 0
        decoded_messages = 0
        for payload in iter_wire_payloads(capture):
            profile = decode_tcp_profile_payload(payload)
            if profile is None:
                archive = decode_tcp_item_archive_payload(payload)
                if archive is None or archive.archive_kind != "engravings":
                    continue
                source_kind = "07 94 engraving archive"
                plaintext = archive.plaintext
                records = archive.records
            else:
                source_kind = "equipment profile"
                plaintext = profile.plaintext
                records = profile.records
            decoded_messages += 1
            for record in records:
                if wanted:
                    if record.template_id not in wanted:
                        continue
                elif not 268420000 <= record.template_id < 268421000:
                    continue
                raw = plaintext[
                    record.offset : record.offset + 171
                ]
                key = (
                    capture.name,
                    source_kind,
                    record.template_id,
                    record.instance_token,
                    raw,
                )
                if key in seen:
                    continue
                seen.add(key)
                found += 1
                print(
                    f"{capture.name}\tsource={source_kind}\t"
                    f"template={record.template_id}\t"
                    f"token={record.instance_token}\t"
                    f"selector={record.field_29},{record.field_30}\t"
                    f"fields26,27,28,31,50="
                    f"{record.field_26},{record.field_27},{raw[28]},"
                    f"{raw[31]},{record.field_50}\t"
                    f"record={raw.hex().upper()}"
                )
        print(
            f"{capture.name}\tdecoded_messages={decoded_messages}\t"
            f"unique_records={found}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
