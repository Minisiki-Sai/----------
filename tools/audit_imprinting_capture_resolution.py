"""Replay inspect captures and audit client-equivalent imprint resolution.

The tooltip observations bundled with the project are validation fixtures;
they are deliberately not used to decode an item in this audit.  For every
fixture-backed record this tool prints the complete 171-byte wire record and
the six native state/value pairs carried by the profile.  That makes it
possible to verify a recovered client mapping without silently teaching the
decoder the expected answer.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.capture_sources import (
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.inspect_catalog import (
    load_inspect_engraving_potence_catalog,
    load_inspect_item_catalog,
)
from dragon_nest_builder.live_dps.models import PlayerInspectEvent
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_tcp_profile_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument(
        "--raw-records",
        action="store_true",
        help="print fixture-backed 171-byte records and native attributes",
    )
    args = parser.parse_args()

    total = decoded = empty = unresolved = 0
    items = load_inspect_item_catalog(DATA_DIR)
    potences = load_inspect_engraving_potence_catalog(DATA_DIR)
    audited_records: set[tuple[int, str]] = set()
    for path in args.captures:
        events = V576DiagnosticReplaySource(path, DATA_DIR).poll()
        rows = [
            detail
            for event in events
            if isinstance(event, PlayerInspectEvent)
            for key, detail in event.equipment
            if key.startswith("刻印|")
        ]
        path_decoded = sum(
            "额外属性：" in row
            and "额外属性：无" not in row
            and "额外属性：未解码" not in row
            for row in rows
        )
        path_empty = sum("额外属性：无" in row for row in rows)
        path_unresolved = sum("额外属性：未解码" in row for row in rows)
        total += len(rows)
        decoded += path_decoded
        empty += path_empty
        unresolved += path_unresolved
        print(
            f"{path.name}: rows={len(rows)} decoded={path_decoded} "
            f"empty={path_empty} unresolved={path_unresolved}"
        )
        for row in rows:
            if "额外属性：未解码" in row:
                print(f"  {row}")
        if args.raw_records:
            for event in events:
                if not isinstance(event, PlayerInspectEvent):
                    continue
                try:
                    payload = bytes.fromhex(event.raw_payload_hex)
                except ValueError:
                    continue
                profile = decode_tcp_profile_payload(payload)
                if profile is None:
                    continue
                for record in profile.records:
                    item = items.get(record.template_id)
                    if item is None or item.category != "刻印":
                        continue
                    route_key = (item.engraving_potence_id, record.field_29)
                    record_key = (record.template_id, record.instance_token)
                    if record_key in audited_records:
                        continue
                    audited_records.add(record_key)
                    raw = profile.plaintext[
                        record.offset : record.offset + 171
                    ]
                    print(
                        "  RAW "
                        f"template={record.template_id} "
                        f"selector=({record.field_29},{record.field_30}) "
                        f"fields26/27/50={record.field_26}/"
                        f"{record.field_27}/{record.field_50} "
                        f"client={potences.get(route_key)!r} "
                        f"dynamic={record.dynamic_attributes!r}"
                    )
                    print(
                        "    header="
                        + " ".join(
                            f"{index:02d}:{raw[index]:02X}"
                            for index in range(22, 53)
                        )
                    )
                    print(f"    bytes={raw.hex().upper()}")
    print(
        f"TOTAL rows={total} decoded={decoded} empty={empty} "
        f"unresolved={unresolved}"
    )
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
