from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    decode_captured_frame,
    decode_v576_payload,
    split_v576_payload,
)
from dragon_nest_builder.live_dps.sources import _load_skill_catalog


_HEADER = struct.Struct("<dBII")


def read_trace(path: Path):
    with path.open("rb") as handle:
        if handle.read(6) != b"DNDP1\x00":
            raise ValueError("not a Dragon Nest Npcap diagnostic trace")
        while header := handle.read(_HEADER.size):
            if len(header) != _HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = _HEADER.unpack(header)
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            yield timestamp, adapter, original_length, frame


def analyze(path: Path) -> dict[str, object]:
    skill_catalog = _load_skill_catalog(
        str(Path("dragon_nest_builder/data").resolve())
    )
    frame_count = 0
    logical_count = 0
    decoded_counts: Counter[str] = Counter()
    signature_counts: Counter[str] = Counter()
    skill_casts: list[dict[str, object]] = []
    hits: list[dict[str, object]] = []
    seen_cross_adapter: dict[tuple[object, ...], tuple[float, int]] = {}
    cross_adapter_duplicates = 0

    for timestamp, adapter, original_length, frame in read_trace(path):
        frame_count += 1
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for payload in split_v576_payload(packet.payload):
            logical_count += 1
            key = (
                packet.transport,
                packet.source_ip,
                packet.destination_ip,
                packet.source_port,
                packet.destination_port,
                payload,
            )
            previous = seen_cross_adapter.get(key)
            if (
                previous is not None
                and previous[1] != adapter
                and abs(timestamp - previous[0]) <= 0.020
            ):
                cross_adapter_duplicates += 1
                continue
            seen_cross_adapter[key] = (timestamp, adapter)

            direction = packet.inferred_direction()
            message = decode_v576_payload(payload, direction, skill_catalog)
            signature = payload[5:9].hex() if len(payload) >= 9 else "short"
            signature_counts[f"{len(payload)}:{signature}"] += 1
            if message is None:
                decoded_counts["unknown"] += 1
                continue
            decoded_counts[message.kind] += 1
            if message.kind in {"skill_cast", "skill_action"}:
                skill_casts.append(
                    {
                        "time": timestamp,
                        "kind": message.kind,
                        "skill_id": message.skill_id,
                        "actor_id": message.source_actor_id,
                    }
                )
            elif message.kind == "damage_hit":
                hits.append(
                    {
                        "time": timestamp,
                        "skill_id": message.skill_id,
                        "actor_id": message.source_actor_id,
                        "damage": message.damage,
                        "layout": message.layout,
                    }
                )

    cast_windows: list[dict[str, object]] = []
    accepted_casts: list[dict[str, object]] = []
    for cast in skill_casts:
        duplicate = next(
            (
                previous
                for previous in reversed(accepted_casts)
                if previous["skill_id"] == cast["skill_id"]
                and previous["actor_id"] == cast["actor_id"]
                and float(cast["time"]) - float(previous["time"]) <= 0.10
            ),
            None,
        )
        if duplicate is None:
            accepted_casts.append(cast)

    for cast in accepted_casts:
        start = float(cast["time"])
        related = [
            hit
            for hit in hits
            if 0.0 <= float(hit["time"]) - start <= 2.5
            and (
                hit["skill_id"] == cast["skill_id"]
                or (
                    hit["skill_id"] is None
                    and hit["actor_id"] == cast["actor_id"]
                )
            )
        ]
        cast_windows.append(
            {
                "time": start,
                "skill_id": cast["skill_id"],
                "actor_id": cast["actor_id"],
                "decoded_hit_count": len(related),
                "hits": [
                    {
                        **hit,
                        "offset": round(float(hit["time"]) - start, 4),
                    }
                    for hit in related
                ],
            }
        )

    return {
        "trace": str(path),
        "raw_frames": frame_count,
        "logical_messages": logical_count,
        "cross_adapter_duplicates": cross_adapter_duplicates,
        "decoded": dict(decoded_counts),
        "signatures": dict(signature_counts.most_common()),
        "cast_windows": cast_windows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.trace)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
