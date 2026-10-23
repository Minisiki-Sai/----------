from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    V576TransportReassembler,
    decode_captured_frame,
    split_v576_payload,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover
    from tools.analyze_npcap_trace import read_trace


@dataclass(frozen=True, slots=True)
class Transition:
    last_action: float
    first_action: float


def _window(seconds: float, transition: Transition) -> str | None:
    if transition.last_action - 20.0 <= seconds < transition.last_action - 2.0:
        return "before"
    if transition.last_action + 5.0 <= seconds <= transition.first_action - 3.0:
        return "dead"
    if transition.first_action + 3.0 < seconds <= transition.first_action + 20.0:
        return "after"
    return None


def _dominant(values: list[tuple[float, int]]) -> tuple[int, int, int] | None:
    if not values:
        return None
    counts = Counter(value for _seconds, value in values)
    value, count = counts.most_common(1)[0]
    return value, count, len(values)


def analyze(path: Path, transitions: dict[int, Transition]) -> dict[str, object]:
    reassembler = V576TransportReassembler()
    origin: float | None = None
    samples: dict[
        tuple[int, str, int, int, int, str], list[tuple[float, int]]
    ] = defaultdict(list)

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        if packet.transport.casefold() != "udp" or packet.inferred_direction() != "server_to_client":
            return
        seconds = packet.timestamp_seconds - origin
        active = {
            actor_id: (transition, _window(seconds, transition))
            for actor_id, transition in transitions.items()
            if _window(seconds, transition) is not None
        }
        if not active:
            return
        for payload in split_v576_payload(packet.payload):
            if len(payload) <= 9:
                continue
            phase = _V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(payload[5])
            if phase is None:
                continue
            decoded = _decode_v576_body(payload, phase)
            for actor_id, (_transition, label) in active.items():
                assert label is not None
                marker = actor_id.to_bytes(4, "little")
                actor_offset = decoded.find(marker, 9)
                while actor_offset >= 0:
                    suffix = decoded[actor_offset + 4 : actor_offset + 8].hex()
                    for relative in range(-16, 65):
                        field_offset = actor_offset + relative
                        if field_offset < 9 or field_offset >= len(decoded):
                            continue
                        if 0 <= relative <= 3:
                            continue
                        key = (
                            actor_id,
                            label,
                            len(payload),
                            actor_offset,
                            relative,
                            suffix,
                        )
                        samples[key].append((round(seconds, 6), decoded[field_offset]))
                    actor_offset = decoded.find(marker, actor_offset + 1)

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in reassembler.feed(packet):
            accept(logical)
    for logical in reassembler.flush():
        accept(logical)

    layouts: dict[
        tuple[int, int, int, str], dict[int, dict[str, list[tuple[float, int]]]]
    ] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for (actor, label, length, actor_offset, relative, suffix), values in samples.items():
        layouts[(length, actor_offset, relative, suffix)][actor][label].extend(values)

    candidates: list[dict[str, object]] = []
    for layout, actor_rows in layouts.items():
        evidence = []
        for actor_id, windows in actor_rows.items():
            before = _dominant(windows.get("before", []))
            dead = _dominant(windows.get("dead", []))
            after = _dominant(windows.get("after", []))
            if before is None or dead is None or after is None:
                continue
            if before[0] != after[0] or dead[0] == before[0]:
                continue
            # State fields should be stable, not one random byte winning a
            # noisy distribution by a single occurrence.
            if min(before[1] / before[2], dead[1] / dead[2], after[1] / after[2]) < 0.6:
                continue
            evidence.append(
                {
                    "actor_id": f"0x{actor_id:08X}",
                    "before": before,
                    "dead": dead,
                    "after": after,
                    "dead_times": [
                        seconds for seconds, value in windows["dead"] if value == dead[0]
                    ][:12],
                }
            )
        if not evidence:
            continue
        candidates.append(
            {
                "length": layout[0],
                "actor_offset": layout[1],
                "relative_offset": layout[2],
                "suffix_hex": layout[3],
                "actor_count": len(evidence),
                "actors": evidence,
            }
        )
    candidates.sort(
        key=lambda row: (
            -int(row["actor_count"]),
            int(row["length"]),
            int(row["actor_offset"]),
            int(row["relative_offset"]),
        )
    )
    return {"capture": str(path), "candidate_count": len(candidates), "candidates": candidates}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--transition", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    transitions: dict[int, Transition] = {}
    for raw in args.transition:
        actor, last_action, first_action = raw.split(":")
        transitions[int(actor, 0)] = Transition(float(last_action), float(first_action))
    result = analyze(args.capture, transitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"candidates={result['candidate_count']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
