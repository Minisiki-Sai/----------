from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body,
    decode_captured_frame,
    decode_v576_payload,
    read_pcapng_packets,
    split_v576_payload,
)
from dragon_nest_builder.live_dps.sources import _load_skill_catalog

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


def _iter_packets(path: Path):
    if path.suffix.lower() == ".dpscap":
        for timestamp, _adapter, original_length, frame in read_trace(path):
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is not None:
                yield packet
        return
    yield from read_pcapng_packets(path, deduplicate=True)


def _find_u32(data: bytes, value: int) -> tuple[int, ...]:
    needle = int(value).to_bytes(4, "little", signed=False)
    offsets: list[int] = []
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return tuple(offsets)
        offsets.append(offset)
        start = offset + 1


def analyze(path: Path, data_root: Path, cast_window: float) -> dict[str, object]:
    skill_catalog = _load_skill_catalog(str(data_root.resolve()))
    known_skills = set(skill_catalog)
    recent_casts: list[tuple[float, int, int]] = []
    candidates: list[dict[str, object]] = []
    groups: Counter[tuple[object, ...]] = Counter()
    samples: dict[tuple[object, ...], dict[str, object]] = {}

    for packet in _iter_packets(path):
        direction = packet.inferred_direction()
        for payload in split_v576_payload(packet.payload):
            decoded = decode_v576_payload(payload, direction)
            if decoded is not None:
                if (
                    decoded.kind in {"skill_cast", "skill_action"}
                    and decoded.skill_id in known_skills
                    and decoded.source_actor_id is not None
                ):
                    recent_casts.append(
                        (
                            packet.timestamp_seconds,
                            decoded.source_actor_id,
                            decoded.skill_id,
                        )
                    )
                    recent_casts = recent_casts[-128:]
                continue
            if direction != "server_to_client" or len(payload) < 80:
                continue

            nearby = [
                cast
                for cast in recent_casts
                if 0.0 <= packet.timestamp_seconds - cast[0] <= cast_window
            ]
            if not nearby:
                continue

            for phase in range(12):
                body = _decode_v576_body(payload, phase)
                for cast_time, actor_id, skill_id in reversed(nearby):
                    skill_offsets = _find_u32(body, skill_id)
                    actor_offsets = _find_u32(body, actor_id)
                    if not skill_offsets or not actor_offsets:
                        continue
                    signature = payload[5:9].hex()
                    key = (
                        len(payload),
                        signature,
                        phase,
                        actor_offsets,
                        skill_offsets,
                    )
                    groups[key] += 1
                    sample = {
                        "time": packet.timestamp_seconds,
                        "after_cast": round(packet.timestamp_seconds - cast_time, 4),
                        "length": len(payload),
                        "signature": signature,
                        "phase": phase,
                        "actor_id": actor_id,
                        "actor_offsets": actor_offsets,
                        "skill_id": skill_id,
                        "skill_name": skill_catalog.get(skill_id, (str(skill_id), "", 99))[0],
                        "skill_offsets": skill_offsets,
                        "decoded_hex": body.hex(),
                        "raw_hex": payload.hex(),
                    }
                    samples.setdefault(key, sample)
                    candidates.append(sample)
                    break

    grouped = []
    for key, count in groups.most_common():
        sample = samples[key]
        grouped.append({"count": count, **sample})
    return {
        "capture": str(path),
        "candidate_count": len(candidates),
        "groups": grouped,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("dragon_nest_builder/data"),
    )
    parser.add_argument("--cast-window", type=float, default=4.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    report = analyze(args.capture, args.data_root, args.cast_window)
    display_report = report
    if args.summary:
        display_report = {
            **report,
            "groups": [
                {
                    key: value
                    for key, value in group.items()
                    if key not in {"decoded_hex", "raw_hex"}
                }
                for group in report["groups"]
            ],
        }
    text = json.dumps(display_report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
