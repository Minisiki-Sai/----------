from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Iterable

from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_BODY_XOR_KEY,
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    decode_captured_frame,
    scan_v576_packet_features,
)
from tools.analyze_npcap_trace import read_trace


def load_client_profile(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    key = bytes.fromhex(str(payload["key_hex_client_order"]))
    forward = bytes.fromhex(str(payload["forward_bit_table_hex"]))
    inverse = bytes.fromhex(str(payload["inverse_bit_table_hex"]))
    if len(key) != 13 or len(forward) != 8 or len(inverse) != 8:
        raise ValueError("invalid client-derived UDP cipher profile")
    if sorted(forward) != list(range(8)) or sorted(inverse) != list(range(8)):
        raise ValueError("invalid client-derived UDP bit permutation")
    if any(inverse[forward[index]] != index for index in range(8)):
        raise ValueError("client UDP bit tables are not inverses")
    return {**payload, "key": key, "forward": forward, "inverse": inverse}


def inverse_header_byte(value: int, inverse: bytes) -> int:
    low = int(value) & 0x03
    middle = (int(value) >> 2) & 0x07
    high = (int(value) >> 5) & 0x07
    return (((inverse[middle] << 2) | low) << 3) | inverse[high]


def decode_client_frame(
    wire: bytes,
    *,
    key: bytes,
    inverse: bytes,
) -> bytes | None:
    """Apply the current client's exact 0x254220 decoder to one wire frame."""
    if len(wire) < 10:
        return None
    inner = bytearray(wire[3:])
    body_size = int.from_bytes(inner[:2], "little")
    if body_size + 7 != len(inner):
        return None
    encrypted_seed = int(inner[2])
    header_key = key[body_size % len(key)]
    inner[2] = inverse_header_byte(header_key ^ inner[2], inverse)
    inner[3] = inverse_header_byte(header_key ^ inner[3], inverse)
    inner[6] = inverse_header_byte(inner[6], inverse)
    for index in range(body_size):
        inner[7 + index] ^= key[(encrypted_seed + 7 + index) % len(key)]
    return wire[:3] + bytes(inner)


def split_client_frames(payload: bytes) -> tuple[bytes, ...]:
    """Split only by the framing rule proved by current-client function 0x254200."""
    frames: list[bytes] = []
    offset = 0
    while offset + 10 <= len(payload):
        body_size = int.from_bytes(payload[offset + 3 : offset + 5], "little")
        frame_size = body_size + 10
        if frame_size < 10 or offset + frame_size > len(payload):
            break
        frames.append(payload[offset : offset + frame_size])
        offset += frame_size
    if offset != len(payload):
        return ()
    return tuple(frames)


def expand_inputs(values: list[Path]) -> list[Path]:
    output: list[Path] = []
    for value in values:
        if value.is_dir():
            output.extend(value.rglob("*.dpscap"))
        elif value.is_file():
            output.append(value)
    return sorted(set(output), key=lambda item: str(item).casefold())


def iter_udp_payloads(
    path: Path,
) -> Iterable[tuple[bytes, str | None, tuple[tuple[str, int], tuple[str, int]]]]:
    for timestamp, adapter, original_length, frame in read_trace(path):
        del timestamp
        if adapter in {251, 254, 255}:
            continue
        packet = decode_captured_frame(0.0, frame, original_length)
        if packet is None or packet.transport.upper() != "UDP":
            continue
        endpoints = tuple(
            sorted(
                (
                    (packet.source_ip, int(packet.source_port)),
                    (packet.destination_ip, int(packet.destination_port)),
                )
            )
        )
        yield packet.payload, packet.inferred_direction(), endpoints  # type: ignore[misc]


def audit_capture(path: Path, profile: dict[str, object]) -> dict[str, object]:
    key = profile["key"]
    inverse = profile["inverse"]
    assert isinstance(key, bytes) and isinstance(inverse, bytes)
    counts: Counter[str] = Counter()
    signature_bytes: Counter[int] = Counter()
    missing_current_phase_bytes: Counter[int] = Counter()
    flow_total: Counter[tuple[tuple[str, int], tuple[str, int]]] = Counter()
    flow_exact: Counter[tuple[tuple[str, int], tuple[str, int]]] = Counter()
    mismatched_examples: list[dict[str, object]] = []
    for payload, direction, endpoints in iter_udp_payloads(path):
        counts["udp_datagrams"] += 1
        flow_total[endpoints] += 1
        frames = split_client_frames(payload)
        if not frames:
            counts["udp_datagrams_not_exact_client_frames"] += 1
            continue
        counts["udp_datagrams_exact_client_framing"] += 1
        flow_exact[endpoints] += 1
        if len(frames) > 1:
            counts["udp_offload_batches"] += 1
        for wire in frames:
            counts["client_frames"] += 1
            signature = int(wire[5])
            signature_bytes[signature] += 1
            if signature not in _V576_XOR_PHASE_BY_SIGNATURE_BYTE:
                missing_current_phase_bytes[signature] += 1
            decoded = decode_client_frame(wire, key=key, inverse=inverse)
            if decoded is None:
                counts["client_decode_failed"] += 1
                continue
            counts["client_decode_success"] += 1
            derived_phase = (11 - signature) % len(key)
            current = _decode_v576_body(wire, derived_phase)
            if current[10:] == decoded[10:]:
                counts["act_body_matches_client"] += 1
            else:
                counts["act_body_differs_client"] += 1
                if len(mismatched_examples) < 8:
                    mismatched_examples.append(
                        {
                            "wire_length": len(wire),
                            "signature_hex": wire[5:9].hex(),
                            "derived_phase": derived_phase,
                            "wire_sha256_prefix": __import__("hashlib").sha256(wire).hexdigest()[:16],
                        }
                    )
            if scan_v576_packet_features(wire, direction, "UDP"):
                counts["currently_routed_or_known_signature"] += 1
            else:
                counts["decrypted_but_no_current_route"] += 1
    encrypted_flows = set(flow_exact)
    rudp_control_or_ack = sum(
        total - flow_exact.get(flow, 0)
        for flow, total in flow_total.items()
        if flow in encrypted_flows
    )
    unrelated_udp = sum(
        total
        for flow, total in flow_total.items()
        if flow not in encrypted_flows
    )
    counts["rudp_control_or_ack_not_encrypted"] = rudp_control_or_ack
    counts["unrelated_udp_not_encrypted_by_game_cipher"] = unrelated_udp
    return {
        "capture": str(path),
        "size_bytes": path.stat().st_size,
        "counts": dict(counts),
        "signature_bytes": {f"0x{key:02X}": value for key, value in signature_bytes.most_common()},
        "missing_current_phase_bytes": {
            f"0x{key:02X}": value for key, value in missing_current_phase_bytes.most_common()
        },
        "mismatched_examples": mismatched_examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Blind-validate a current-client-derived UDP XOR table on dpscap files."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("outputs/client_unpack_with_udp_key/udp_key_v576.json"),
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    paths = expand_inputs(args.inputs)
    if not paths:
        parser.error("no .dpscap files found")
    profile = load_client_profile(args.profile)
    reports = [audit_capture(path, profile) for path in paths]
    totals: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    for report in reports:
        totals.update(report["counts"])
        signatures.update(report["signature_bytes"])
        missing.update(report["missing_current_phase_bytes"])
    raw_key = profile["key"]
    assert isinstance(raw_key, bytes)
    normalized_for_act = raw_key[8:] + raw_key[:8]
    result = {
        "schema_version": 1,
        "client_profile": str(args.profile),
        "client_source_sha256": profile["source_sha256"],
        "capture_count": len(reports),
        "method": (
            "key/table/decoder are taken only from the current client; captures are used "
            "afterward as blind validation inputs"
        ),
        "client_key_hex_raw_order": raw_key.hex(),
        "client_key_hex_act_phase_convention": normalized_for_act.hex(),
        "current_act_key_matches_client_normalization": normalized_for_act == _V576_BODY_XOR_KEY,
        "totals": dict(totals),
        "signature_bytes": dict(signatures.most_common()),
        "missing_current_phase_bytes": dict(missing.most_common()),
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
