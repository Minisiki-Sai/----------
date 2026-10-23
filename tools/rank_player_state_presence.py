from __future__ import annotations

import argparse
from collections import defaultdict
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


def _window(seconds: float, row: Transition) -> str | None:
    if row.last_action - 20.0 <= seconds < row.last_action - 2.0:
        return "before"
    if row.last_action + 3.0 <= seconds <= row.first_action - 3.0:
        return "dead"
    if row.first_action + 3.0 < seconds <= row.first_action + 20.0:
        return "after"
    return None


def analyze(path: Path, transitions: dict[int, Transition]) -> dict[str, object]:
    origin: float | None = None
    reassembler = V576TransportReassembler()
    occurrences: dict[
        tuple[int, int, str, int, str], dict[int, dict[str, list[float]]]
    ] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    records: dict[str, dict[int, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        if packet.transport.casefold() != "udp" or packet.inferred_direction() != "server_to_client":
            return
        seconds = packet.timestamp_seconds - origin
        active = {
            actor: label
            for actor, transition in transitions.items()
            if (label := _window(seconds, transition)) is not None
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
            signature = payload[5:9].hex()
            for actor_id, label in active.items():
                marker = actor_id.to_bytes(4, "little")
                offset = decoded.find(marker, 14)
                while offset >= 0:
                    # Keep only the record discriminator. Coordinates, amounts
                    # and timestamps immediately following it are actor-specific
                    # and would split one lifecycle family into many fake keys.
                    suffix = decoded[offset + 4 : offset + 7].hex()
                    key = (len(payload), offset, signature, phase, suffix)
                    occurrences[key][actor_id][label].append(round(seconds, 6))
                    records[suffix][actor_id][label].append(round(seconds, 6))
                    offset = decoded.find(marker, offset + 1)

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in reassembler.feed(packet):
            accept(logical)
    for logical in reassembler.flush():
        accept(logical)

    candidates = []
    for key, actor_rows in occurrences.items():
        evidence = []
        for actor_id, windows in actor_rows.items():
            dead = windows.get("dead", [])
            if not dead:
                continue
            before = windows.get("before", [])
            after = windows.get("after", [])
            if before or after:
                continue
            evidence.append(
                {
                    "actor_id": f"0x{actor_id:08X}",
                    "dead_count": len(dead),
                    "dead_times": dead[:20],
                }
            )
        if not evidence:
            continue
        candidates.append(
            {
                "length": key[0],
                "actor_offset": key[1],
                "signature": key[2],
                "xor_phase": key[3],
                "suffix_hex": key[4],
                "actor_count": len(evidence),
                "dead_count": sum(int(row["dead_count"]) for row in evidence),
                "actors": evidence,
            }
        )
    candidates.sort(
        key=lambda row: (
            -int(row["actor_count"]),
            -int(row["dead_count"]),
            int(row["length"]),
        )
    )
    record_candidates = []
    for suffix, actor_rows in records.items():
        evidence = []
        for actor_id, windows in actor_rows.items():
            dead = windows.get("dead", [])
            if not dead or windows.get("before") or windows.get("after"):
                continue
            evidence.append(
                {
                    "actor_id": f"0x{actor_id:08X}",
                    "dead_count": len(dead),
                    "dead_times": dead[:20],
                }
            )
        if evidence:
            record_candidates.append(
                {
                    "suffix_hex": suffix,
                    "actor_count": len(evidence),
                    "dead_count": sum(int(row["dead_count"]) for row in evidence),
                    "actors": evidence,
                }
            )
    record_candidates.sort(
        key=lambda row: (-int(row["actor_count"]), -int(row["dead_count"]))
    )
    return {
        "capture": str(path),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "record_candidates": record_candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--transition", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    transitions = {}
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
