from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
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
        return "alive_before"
    if transition.last_action - 2.0 <= seconds <= transition.last_action + 6.0:
        return "death"
    if transition.last_action + 6.0 < seconds < transition.first_action - 2.0:
        return "dead"
    if transition.first_action - 2.0 <= seconds <= transition.first_action + 3.0:
        return "revive"
    if transition.first_action + 3.0 < seconds <= transition.first_action + 20.0:
        return "alive_after"
    return None


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
        if (
            packet.transport.casefold() != "udp"
            or packet.inferred_direction() != "server_to_client"
        ):
            return
        seconds = packet.timestamp_seconds - origin
        active = {
            actor_id: transition
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
            for actor_id, transition in active.items():
                marker = actor_id.to_bytes(4, "little")
                actor_offset = decoded.find(marker, 9)
                while actor_offset >= 0:
                    label = _window(seconds, transition)
                    assert label is not None
                    # HP/current-state fields observed so far are naturally
                    # aligned to the record, not necessarily to the packet.
                    # Probe every relative byte offset while excluding bytes
                    # that overlap the runtime actor identifier itself.
                    for relative in range(-20, 69):
                        field_offset = actor_offset + relative
                        if field_offset < 9 or field_offset + 4 > len(decoded):
                            continue
                        if relative < 4 and relative + 4 > 0:
                            continue
                        value = int.from_bytes(
                            decoded[field_offset : field_offset + 4], "little"
                        )
                        # Keep values in the plausible state/HP range. Large
                        # IDs and encrypted-looking words only add noise.
                        if value > 100_000_000:
                            continue
                        key = (
                            actor_id,
                            label,
                            len(payload),
                            actor_offset,
                            relative,
                            decoded[actor_offset + 4 : actor_offset + 7].hex(),
                        )
                        samples[key].append((round(seconds, 6), value))
                    actor_offset = decoded.find(marker, actor_offset + 1)

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical_packet in reassembler.feed(packet):
            accept(logical_packet)
    for logical_packet in reassembler.flush():
        accept(logical_packet)

    by_layout: dict[
        tuple[int, int, int, str], dict[int, dict[str, list[tuple[float, int]]]]
    ] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for (actor_id, label, length, actor_offset, relative, suffix), values in samples.items():
        by_layout[(length, actor_offset, relative, suffix)][actor_id][label].extend(values)

    candidates: list[dict[str, object]] = []
    for layout, actor_rows in by_layout.items():
        actor_evidence: list[dict[str, object]] = []
        for actor_id, windows in actor_rows.items():
            death_zero = [row for row in windows.get("death", ()) if row[1] == 0]
            dead_zero = [row for row in windows.get("dead", ()) if row[1] == 0]
            before_positive = [
                row for row in windows.get("alive_before", ()) if row[1] > 0
            ]
            revive_positive = [
                row for row in windows.get("revive", ()) if row[1] > 0
            ]
            after_positive = [
                row for row in windows.get("alive_after", ()) if row[1] > 0
            ]
            if not (death_zero or dead_zero):
                continue
            if not before_positive and not revive_positive and not after_positive:
                continue
            actor_evidence.append(
                {
                    "actor_id": f"0x{actor_id:08X}",
                    "zero_times": [row[0] for row in (death_zero + dead_zero)[:12]],
                    "before_positive": before_positive[-4:],
                    "revive_positive": revive_positive[:8],
                    "after_positive": after_positive[:4],
                }
            )
        if not actor_evidence:
            continue
        candidates.append(
            {
                "length": layout[0],
                "actor_offset": layout[1],
                "relative_offset": layout[2],
                "suffix_hex": layout[3],
                "actor_count": len(actor_evidence),
                "actors": actor_evidence,
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
    return {
        "capture": str(path),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--transition",
        action="append",
        required=True,
        help="ACTOR_ID:LAST_ACTION_SECONDS:FIRST_ACTION_SECONDS",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    transitions: dict[int, Transition] = {}
    for value in args.transition:
        actor, last_action, first_action = value.split(":")
        transitions[int(actor, 0)] = Transition(
            last_action=float(last_action), first_action=float(first_action)
        )
    result = analyze(args.capture, transitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"candidates={result['candidate_count']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
