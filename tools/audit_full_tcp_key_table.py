from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Iterable

from dragon_nest_builder.live_dps.protocol_v576 import (
    PacketRecord,
    V576TransportReassembler,
    decode_captured_frame,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
    decrypt_tcp_profile_body,
)
from tools.analyze_npcap_trace import read_trace


def load_full_table(path: Path) -> dict[int, tuple[int, int, int, int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_keys = payload.get("keys", {})
    keys: dict[int, tuple[int, int, int, int]] = {}
    for raw_index, raw_words in raw_keys.items():
        index = int(raw_index)
        if not isinstance(raw_words, list) or len(raw_words) != 4:
            raise ValueError(f"invalid key-table row {raw_index}")
        words = tuple(
            int(word, 0) if isinstance(word, str) else int(word)
            for word in raw_words
        )
        keys[index] = words  # type: ignore[assignment]
    if set(keys) != set(range(256)):
        missing = sorted(set(range(256)) - set(keys))
        raise ValueError(f"key table is incomplete; missing={missing}")
    return keys


def iter_logical_packets(path: Path) -> Iterable[PacketRecord]:
    transport = V576TransportReassembler()
    for timestamp, adapter, original_length, frame in read_trace(path):
        if adapter in {251, 254, 255}:
            continue
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        if packet.transport.upper() == "TCP":
            yield from transport.feed(packet)
        else:
            # UDP has its own v576 reliable-message/XOR pipeline.  Rebuilding
            # all UDP message groups is deliberately outside this TCP-table
            # audit and would dominate runtime on long raid captures.
            yield packet
    yield from transport.flush()


def envelope_direction(packet: PacketRecord) -> str:
    direction = packet.inferred_direction()
    if direction in {"client_to_server", "server_to_client"}:
        return direction
    payload = packet.payload
    if len(payload) >= 3 and int.from_bytes(payload[:3], "little") == len(payload):
        return "client_to_server"
    if len(payload) >= 3 and int.from_bytes(payload[:2], "little") == len(payload):
        return "server_to_client"
    return "unknown"


def full_table_decode(
    payload: bytes,
    direction: str,
    keys: dict[int, tuple[int, int, int, int]],
) -> tuple[bytes, int, int] | None:
    if len(payload) < 7:
        return None
    if direction == "client_to_server":
        if int.from_bytes(payload[:3], "little") != len(payload):
            return None
    elif direction == "server_to_client":
        if int.from_bytes(payload[:2], "little") != len(payload):
            return None
    else:
        return None
    body = payload[3:]
    key_index = len(body) % 256
    rounds = (len(body) % 2) + 1
    plaintext = decrypt_tcp_profile_body(body, keys[key_index], rounds)
    if len(plaintext) < 4 or int.from_bytes(plaintext[:2], "little") != len(body):
        return None
    return plaintext, key_index, rounds


def current_decode(packet: PacketRecord, direction: str):
    if direction == "client_to_server":
        return decode_registered_tcp_envelope(packet.payload)
    if direction == "server_to_client":
        return decode_registered_server_tcp_envelope(
            packet.payload,
            connection_context=packet,
        )
    return None


def audit_capture(
    path: Path,
    keys: dict[int, tuple[int, int, int, int]],
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    discrepancies: Counter[str] = Counter()
    table_failed_indexes: Counter[int] = Counter()
    table_only_indexes: Counter[int] = Counter()
    current_only_indexes: Counter[int] = Counter()
    failure_shapes: Counter[tuple[str, int, int, str]] = Counter()
    failure_examples: list[dict[str, object]] = []
    current_only_examples: list[dict[str, object]] = []
    differing_plaintext_examples: list[dict[str, object]] = []
    for packet in iter_logical_packets(path):
        counts["logical_packets"] += 1
        transport = packet.transport.upper()
        counts[f"{transport.lower()}_packets"] += 1
        if transport != "TCP":
            # The extracted table is the TCP XTEA table. UDP v576 messages use
            # their existing signature/phase XOR path and are intentionally
            # counted here without ever being passed through this table.
            continue
        direction = envelope_direction(packet)
        counts[f"tcp_direction_{direction}"] += 1
        body_size = max(0, len(packet.payload) - 3)
        key_index = body_size % 256
        framed = direction != "unknown" and len(packet.payload) >= 7
        if framed:
            counts["tcp_candidate_envelopes"] += 1
        table_decoded = full_table_decode(packet.payload, direction, keys)
        current_decoded = current_decode(packet, direction)
        table_ok = table_decoded is not None
        current_ok = current_decoded is not None
        if table_ok:
            counts["full_table_valid"] += 1
        if current_ok:
            counts["current_decoder_valid"] += 1
        if table_ok and current_ok:
            counts["both_valid"] += 1
            if table_decoded[0] != current_decoded[0]:
                counts["both_valid_plaintext_differs"] += 1
                discrepancies[f"both-different:index-{key_index}"] += 1
                if len(differing_plaintext_examples) < 24:
                    differing_plaintext_examples.append(
                        {
                            "direction": direction,
                            "wire_size": len(packet.payload),
                            "body_size": body_size,
                            "key_index": key_index,
                            "current_key_source": current_decoded[3],
                            "full_table_plaintext_prefix_hex": table_decoded[0][:64].hex(),
                            "current_plaintext_prefix_hex": current_decoded[0][:64].hex(),
                            "source": f"{packet.source_ip}:{packet.source_port}",
                            "destination": f"{packet.destination_ip}:{packet.destination_port}",
                            "wire_prefix_hex": packet.payload[:48].hex(),
                        }
                    )
        elif table_ok:
            counts["full_table_only"] += 1
            table_only_indexes[key_index] += 1
        elif current_ok:
            counts["current_decoder_only"] += 1
            current_only_indexes[key_index] += 1
            if len(current_only_examples) < 12:
                current_only_examples.append(
                    {
                        "direction": direction,
                        "wire_size": len(packet.payload),
                        "body_size": body_size,
                        "key_index": key_index,
                        "key_source": current_decoded[3],
                        "plaintext_prefix_hex": current_decoded[0][:48].hex(),
                        "source": f"{packet.source_ip}:{packet.source_port}",
                        "destination": f"{packet.destination_ip}:{packet.destination_port}",
                        "wire_prefix_hex": packet.payload[:48].hex(),
                    }
                )
        elif framed:
            counts["neither_valid"] += 1
            table_failed_indexes[key_index] += 1
            shape = (
                direction,
                body_size,
                key_index,
                packet.payload[3:7].hex(),
            )
            failure_shapes[shape] += 1
            if len(failure_examples) < 24:
                failure_examples.append(
                    {
                        "direction": direction,
                        "wire_size": len(packet.payload),
                        "body_size": body_size,
                        "key_index": key_index,
                        "source": f"{packet.source_ip}:{packet.source_port}",
                        "destination": f"{packet.destination_ip}:{packet.destination_port}",
                        "wire_prefix_hex": packet.payload[:48].hex(),
                    }
                )
    return {
        "capture": str(path),
        "size_bytes": path.stat().st_size,
        "counts": dict(counts),
        "full_table_only_indexes": dict(table_only_indexes.most_common()),
        "current_decoder_only_indexes": dict(current_only_indexes.most_common()),
        "full_table_failed_indexes": dict(table_failed_indexes.most_common()),
        "plaintext_discrepancies": dict(discrepancies.most_common()),
        "failure_shapes": [
            {
                "direction": key[0],
                "body_size": key[1],
                "key_index": key[2],
                "body_prefix_hex": key[3],
                "count": value,
            }
            for key, value in failure_shapes.most_common(100)
        ],
        "failure_examples": failure_examples,
        "current_only_examples": current_only_examples,
        "differing_plaintext_examples": differing_plaintext_examples,
    }


def expand_inputs(values: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        if value.is_dir():
            paths.extend(value.rglob("*.dpscap"))
        elif value.is_file():
            paths.append(value)
    return sorted(set(paths), key=lambda path: str(path).casefold())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare the extracted 256-row TCP key table with ACT's current decoder."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--key-table",
        type=Path,
        default=Path("outputs/client_unpack_with_keys/tcp_key_table_v576.json"),
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    paths = expand_inputs(args.inputs)
    if not paths:
        parser.error("no .dpscap captures found")
    keys = load_full_table(args.key_table)
    reports = [audit_capture(path, keys) for path in paths]
    totals: Counter[str] = Counter()
    table_only: Counter[int] = Counter()
    current_only: Counter[int] = Counter()
    failed: Counter[int] = Counter()
    for report in reports:
        totals.update(report["counts"])
        table_only.update({int(k): int(v) for k, v in report["full_table_only_indexes"].items()})
        current_only.update({int(k): int(v) for k, v in report["current_decoder_only_indexes"].items()})
        failed.update({int(k): int(v) for k, v in report["full_table_failed_indexes"].items()})
    result = {
        "schema_version": 1,
        "key_table": str(args.key_table),
        "capture_count": len(reports),
        "totals": dict(totals),
        "full_table_only_indexes": dict(table_only.most_common()),
        "current_decoder_only_indexes": dict(current_only.most_common()),
        "full_table_failed_indexes": dict(failed.most_common()),
        "captures": reports,
    }
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
