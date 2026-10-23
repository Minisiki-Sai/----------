from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    V576_PACKET_ACTION,
    V576_PACKET_BOSS_ACTION,
    V576_PACKET_COMPOUND,
    V576_PACKET_EMBEDDED_DIRECT_DAMAGE,
    V576_PACKET_HEALING,
    V576_PACKET_HEALTH,
    V576_PACKET_LIFECYCLE,
    V576_PACKET_PRIMARY,
    V576_PACKET_STATUS,
    V576_PACKET_STATUS_DAMAGE,
    _V576_ACTION_MARKERS,
    _V576_MESSAGE_LENGTH_BY_SIGNATURE,
    _V576_PACKET_MARKER_ROUTES,
    _decode_v576_body,
    decode_v576_damage_object_registration,
    decode_captured_frame,
    decode_v576_map_identity,
    decode_v576_monster_spawn,
    decode_v576_movement,
    decode_v576_payload,
    decode_v576_player_health,
    decode_v576_status_events,
    scan_v576_packet_features,
)
from tools.analyze_npcap_trace import read_trace
from tools.audit_client_derived_udp_key import expand_inputs, split_client_frames


LEGACY_PHASE_BYTES = {
    0xA7,
    0x18,
    0xCE,
    0x65,
    0xE5,
    0x97,
    0xFE,
    0x7C,
    0x2E,
    0x6E,
    0x87,
    0x92,
    0x28,
    0xD0,
}

FEATURES = (
    (V576_PACKET_PRIMARY, "primary"),
    (V576_PACKET_ACTION, "action"),
    (V576_PACKET_STATUS, "status"),
    (V576_PACKET_HEALTH, "health"),
    (V576_PACKET_LIFECYCLE, "lifecycle"),
    (V576_PACKET_BOSS_ACTION, "boss_action"),
    (V576_PACKET_STATUS_DAMAGE, "status_damage"),
    (V576_PACKET_HEALING, "healing"),
    (V576_PACKET_EMBEDDED_DIRECT_DAMAGE, "embedded_direct_damage"),
    (V576_PACKET_COMPOUND, "compound"),
)


def readable_strings(payload: bytes) -> list[str]:
    output: list[str] = []
    for match in re.findall(rb"[ -~]{5,}", payload):
        text = match.decode("ascii", errors="ignore").strip()
        if text and text not in output:
            output.append(text[:80])
    index = 0
    while index < len(payload):
        try:
            text = payload[index:].decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            break
        for match in re.findall(r"[\u3400-\u9fff]{2,}", text):
            if match not in output:
                output.append(match[:40])
        break
    return output[:8]


def feature_names(value: int) -> list[str]:
    return [name for flag, name in FEATURES if value & flag]


def exact_plaintext_features(decoded: bytes, signature: bytes, direction: str) -> tuple[int, list[str]]:
    """Classify only markers that really exist in the client-decoded plaintext.

    ``scan_v576_packet_features`` intentionally has a conservative all-decoder
    fallback when a wire-level prefilter and the preferred phase disagree.  That
    is safe at runtime but overstates what an inventory has actually identified.
    This audit path therefore records only exact plaintext marker matches.
    """
    routed = V576_PACKET_PRIMARY if signature in _V576_MESSAGE_LENGTH_BY_SIGNATURE else 0
    marker_names: list[str] = []
    allowed = set(_V576_ACTION_MARKERS) if direction == "client_to_server" else None
    for marker, start_offset, marker_features in _V576_PACKET_MARKER_ROUTES:
        if allowed is not None and marker not in allowed:
            continue
        search_from = start_offset
        found = decoded.find(marker, search_from)
        if found < 0:
            continue
        routed |= marker_features
        marker_names.append(marker.hex())
        if marker == b"\x00\x00\x00\xff\xff\xff\xff\xff\xff":
            routed |= V576_PACKET_STATUS
    return routed, marker_names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = expand_inputs(args.inputs)

    totals = Counter()
    feature_totals = Counter()
    conservative_feature_totals = Counter()
    marker_totals = Counter()
    semantic_decoder_totals = Counter()
    signature_totals = Counter()
    unrouted_shapes = Counter()
    routed_shapes = Counter()
    new_phase_shapes = Counter()
    captures_by_shape = defaultdict(set)
    examples = defaultdict(list)

    for path in paths:
        for timestamp, adapter, original_length, raw_frame in read_trace(path):
            if adapter in {251, 254, 255}:
                continue
            packet = decode_captured_frame(timestamp, raw_frame, original_length)
            if packet is None or packet.transport.upper() != "UDP":
                continue
            direction = packet.inferred_direction() or "unknown"
            frames = split_client_frames(packet.payload)
            for wire in frames:
                totals["client_frames"] += 1
                signature_byte = int(wire[5])
                signature = wire[5:9].hex()
                phase = (11 - signature_byte) % 13
                decoded = _decode_v576_body(wire, phase)
                body = decoded[10:]
                conservative_features = scan_v576_packet_features(wire, direction, "UDP")
                features, exact_markers = exact_plaintext_features(
                    decoded, wire[5:9], direction
                )
                is_new_phase = signature_byte not in LEGACY_PHASE_BYTES
                if is_new_phase:
                    totals["frames_without_legacy_phase_mapping"] += 1
                    movement_rows = decode_v576_movement(wire, direction)
                    if movement_rows:
                        semantic_decoder_totals[("movement", signature)] += len(movement_rows)
                        semantic_decoder_totals[("movement_packets", signature)] += 1
                    registration = decode_v576_damage_object_registration(wire, direction)
                    if registration is not None:
                        semantic_decoder_totals[(
                            f"damage_object:{registration.layout}", signature
                        )] += 1
                    monster = decode_v576_monster_spawn(wire, direction)
                    if monster is not None:
                        semantic_decoder_totals[("monster_spawn", signature)] += 1
                    map_identity = decode_v576_map_identity(wire, direction)
                    if map_identity is not None:
                        semantic_decoder_totals[(
                            f"map_identity:{map_identity.layout}", signature
                        )] += 1
                    if features:
                        primary = decode_v576_payload(
                            wire,
                            direction,
                            packet_features=features,
                        )
                        if primary is not None:
                            semantic_decoder_totals[(
                                f"primary:{primary.kind}:{primary.layout}", signature
                            )] += 1
                    if features & V576_PACKET_STATUS:
                        statuses = decode_v576_status_events(wire, direction)
                        if statuses:
                            semantic_decoder_totals[("status_rows", signature)] += len(statuses)
                            semantic_decoder_totals[("status_packets", signature)] += 1
                    if features & V576_PACKET_HEALTH:
                        health_rows = decode_v576_player_health(wire, direction)
                        if health_rows:
                            semantic_decoder_totals[("health_rows", signature)] += len(health_rows)
                            semantic_decoder_totals[("health_packets", signature)] += 1
                else:
                    totals["frames_with_legacy_phase_mapping"] += 1
                if features:
                    totals["currently_routed"] += 1
                    if is_new_phase:
                        totals["new_phase_currently_routed"] += 1
                    for name in feature_names(features):
                        feature_totals[("new_phase" if is_new_phase else "legacy_phase", name)] += 1
                else:
                    totals["currently_unrouted"] += 1
                    if is_new_phase:
                        totals["new_phase_currently_unrouted"] += 1

                for name in feature_names(conservative_features):
                    conservative_feature_totals[("new_phase" if is_new_phase else "legacy_phase", name)] += 1
                for marker in exact_markers:
                    marker_totals[(
                        "new_phase" if is_new_phase else "legacy_phase",
                        direction,
                        signature,
                        marker,
                    )] += 1

                signature_totals[("new_phase" if is_new_phase else "legacy_phase", direction, signature)] += 1
                prefix = body[:4].hex()
                shape = (
                    "new_phase" if is_new_phase else "legacy_phase",
                    direction,
                    signature,
                    len(wire),
                    prefix,
                )
                if is_new_phase:
                    new_phase_shapes[shape] += 1
                if features:
                    routed_shapes[(
                        *shape,
                        ",".join(exact_markers),
                        ",".join(feature_names(features)),
                    )] += 1
                if not features:
                    unrouted_shapes[shape] += 1
                    captures_by_shape[shape].add(path.name)
                    if len(examples[shape]) < 3:
                        examples[shape].append(
                            {
                                "capture": path.name,
                                "timestamp_seconds": round(timestamp, 6),
                                "phase": phase,
                                "wire_length": len(wire),
                                "wire_signature": signature,
                                "decoded_header_hex": decoded[:16].hex(),
                                "decoded_body_prefix_hex": body[:96].hex(),
                                "zero_ratio": round(body.count(0) / max(1, len(body)), 4),
                                "readable_strings": readable_strings(body),
                                "body_sha256_prefix": hashlib.sha256(body).hexdigest()[:16],
                            }
                        )

    result = {
        "schema_version": 1,
        "method": (
            "UDP key and phase formula come only from the current client; captures are "
            "used to inventory plaintext shapes after decryption"
        ),
        "capture_count": len(paths),
        "totals": dict(totals),
        "feature_totals": [
            {"phase_group": key[0], "feature": key[1], "count": value}
            for key, value in feature_totals.most_common()
        ],
        "conservative_feature_totals": [
            {"phase_group": key[0], "feature": key[1], "count": value}
            for key, value in conservative_feature_totals.most_common()
        ],
        "exact_marker_totals": [
            {
                "phase_group": key[0],
                "direction": key[1],
                "signature_hex": key[2],
                "marker_hex": key[3],
                "count": value,
            }
            for key, value in marker_totals.most_common()
        ],
        "new_phase_semantic_decoder_totals": [
            {
                "decoder": key[0],
                "signature_hex": key[1],
                "count": value,
            }
            for key, value in semantic_decoder_totals.most_common()
        ],
        "signature_totals": [
            {
                "phase_group": key[0],
                "direction": key[1],
                "signature_hex": key[2],
                "count": value,
            }
            for key, value in signature_totals.most_common()
        ],
        "top_new_phase_shapes": [
            {
                "phase_group": key[0],
                "direction": key[1],
                "signature_hex": key[2],
                "wire_length": key[3],
                "decoded_prefix_hex": key[4],
                "count": value,
            }
            for key, value in new_phase_shapes.most_common(300)
        ],
        "top_exact_routed_shapes": [
            {
                "phase_group": key[0],
                "direction": key[1],
                "signature_hex": key[2],
                "wire_length": key[3],
                "decoded_prefix_hex": key[4],
                "marker_hex": key[5],
                "features": key[6],
                "count": value,
            }
            for key, value in routed_shapes.most_common(500)
        ],
        "unrouted_shapes": [
            {
                "phase_group": key[0],
                "direction": key[1],
                "signature_hex": key[2],
                "wire_length": key[3],
                "decoded_prefix_hex": key[4],
                "count": value,
                "capture_count": len(captures_by_shape[key]),
                "captures": sorted(captures_by_shape[key]),
                "examples": examples[key],
            }
            for key, value in unrouted_shapes.most_common(500)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "totals": result["totals"],
                "unrouted_shape_count": len(unrouted_shapes),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
