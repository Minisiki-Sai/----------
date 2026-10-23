"""Re-extract hash-bound high-interest Rune RowId capture candidates.

This is a read-only companion to audit_player_skill_remap_v56.py.  It seeks
only retained frame offsets, verifies the decoded-message hash, and emits a
small JSON sidecar.  It does not execute or load the game client.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps import protocol_v576 as protocol  # noqa: E402


OUT = ROOT / "outputs/player_skill_remap_v56_20260909"
HEADER = struct.Struct("<dBII")
TARGET_ROWS = {268500301, 268500302}
ACTION_MARKERS = (b"\x0e\x23\x00", b"\x0e\x1f\x00", b"\x12\x24\x00", b"\x12\x20\x00")
IDENTITY_EVIDENCE = {
    7: ROOT / "outputs/v451_score_batch_20260902_0801_0831_103/capture_results/d1d2149fa802e1e23050e8eceb709a68aca5793864813b0d60a9676d8a6a9fe4.json",
    9: ROOT / "outputs/v451_score_batch_20260902_0801_0831_103/capture_results/ff8deab3d71df1a99f5bced42edaeae0f660db85d72e32f49576f23a971be649.json",
    10: ROOT / "outputs/v451_score_batch_20260902_0801_0831_103/capture_results/fa9e124cf328d045e42fa217ae4e00d60ce1e7a000bcdf2cbdc3b05a402d37bf.json",
}


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def jsonify(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, tuple):
        return [jsonify(item) for item in value]
    return value


def main() -> int:
    scan = json.loads((OUT / "capture_scan.json").read_text(encoding="utf-8"))
    old = json.loads((OUT / "old_resource_remap.json").read_text(encoding="utf-8"))
    known_skills = {
        value
        for row in old["rows"]
        for pair in row["skill_pairs"]
        for value in pair
        if value
    }
    target_rows = {
        row["row_id"]: row
        for row in old["rows"]
        if row["row_id"] in TARGET_ROWS
    }
    selected = [
        item
        for item in scan["examples"]
        if item["layer"] == "udp_v576_decoded_candidate"
        and item["phase_source"] == "signature"
        and item["payload_offset"] == 65
        and item["row_id"] in TARGET_ROWS
    ]
    rows = []
    for item in selected:
        capture = scan["captures"][item["capture_index"] - 1]
        path = Path(capture["path"])
        with path.open("rb") as stream:
            stream.seek(item["file_offset"])
            header = stream.read(HEADER.size)
            timestamp, _adapter, original_length, length = HEADER.unpack(header)
            raw = stream.read(length)
        packet = protocol.decode_captured_frame(timestamp, raw, original_length)
        if packet is None or packet.transport != "UDP":
            raise ValueError("retained frame no longer decodes as UDP")
        messages = protocol.split_v576_payload(packet.payload)
        wire = messages[item["message_index"]]
        decoded = protocol._decode_v576_body(wire, item["xor_phase"])
        if digest(decoded) != item["container_sha256"]:
            raise ValueError("decoded message hash changed")
        if int.from_bytes(decoded[65:69], "little") != item["row_id"]:
            raise ValueError("retained RowId changed")

        marker_rows = []
        for marker in ACTION_MARKERS:
            at = decoded.find(marker, 9)
            while at >= 0:
                marker_rows.append(
                    {
                        "offset": at,
                        "marker": marker.hex(),
                        "preceding_u32": int.from_bytes(decoded[at - 4:at], "little") if at >= 4 else None,
                        "following_u32": int.from_bytes(decoded[at + 3:at + 7], "little") if at + 7 <= len(decoded) else None,
                    }
                )
                at = decoded.find(marker, at + 1)
        structured = protocol.decode_v576_structured_actions(
            wire,
            item["direction"],
            known_skills,
            None,
            allow_unknown_actor_ids=True,
        )
        primary = protocol.decode_v576_payload(
            wire,
            item["direction"],
            known_skill_ids=known_skills,
            known_action_skill_ids=known_skills,
            known_action_actor_ids=None,
        )
        actor_a = int.from_bytes(decoded[10:14], "little")
        actor_b = int.from_bytes(decoded[14:18], "little")
        identity_cross_check = None
        identity_path = IDENTITY_EVIDENCE.get(item["capture_index"])
        if item["direction"] == "client_to_server" and identity_path is not None:
            identity_raw = identity_path.read_bytes()
            identity = json.loads(identity_raw)
            identity_key = f"0X{actor_a:08X}"
            identity_row = identity.get("character_identities", {}).get(identity_key)
            score_actor_ids = {row.get("sourceId") for row in identity.get("score_rows", [])}
            identity_cross_check = {
                "source": str(identity_path),
                "source_sha256": digest(identity_raw),
                "actor_id": f"0x{actor_a:08X}",
                "duplicated_at_offsets_10_14": actor_a == actor_b,
                "present_in_score_rows": f"0x{actor_a:08X}" in score_actor_ids,
                "party_identity": identity_row,
            }
            if not (
                actor_a == actor_b
                and identity_cross_check["present_in_score_rows"]
                and identity_row
                and identity_row.get("trusted") is True
            ):
                raise ValueError("independent local-actor cross-check changed")
        rows.append(
            {
                "capture_index": item["capture_index"],
                "capture_path": str(path),
                "capture_sha256": capture["sha256"],
                "frame_index": item["frame_index"],
                "file_offset": item["file_offset"],
                "timestamp": item["timestamp"],
                "direction": item["direction"],
                "wire_length": len(wire),
                "wire_signature": wire[5:9].hex(),
                "xor_phase": item["xor_phase"],
                "decoded_sha256": digest(decoded),
                "row_id_offset": 65,
                "row_id": item["row_id"],
                "actor_id_candidate": f"0x{actor_a:08X}",
                "actor_id_duplicate": f"0x{actor_b:08X}",
                "actor_identity_cross_check": identity_cross_check,
                "actor_row_window_offset": 9,
                "actor_row_window_hex": decoded[9:73].hex(),
                "fixed_u32": {
                    str(offset): int.from_bytes(decoded[offset:offset + 4], "little")
                    for offset in (9, 10, 13, 14, 64, 65, 69)
                },
                "action_markers": marker_rows,
                "structured_actions": jsonify(structured),
                "primary_decoder": jsonify(primary),
            }
        )

    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = (
            row["row_id"], row["direction"], row["wire_length"],
            row["wire_signature"], row["row_id_offset"],
        )
        groups[key].append(row)
    grouped = []
    for key, members in sorted(groups.items()):
        row_id, direction, wire_length, signature, row_offset = key
        grouped.append(
            {
                "row_id": row_id,
                "direction": direction,
                "wire_length": wire_length,
                "wire_signature": signature,
                "row_id_offset": row_offset,
                "count": len(members),
                "capture_indexes": sorted({item["capture_index"] for item in members}),
                "fixed_u32_distinct": {
                    offset: sorted({item["fixed_u32"][offset] for item in members})
                    for offset in members[0]["fixed_u32"]
                },
                "structured_action_count": sum(len(item["structured_actions"]) for item in members),
                "primary_decoder_nonnull": sum(item["primary_decoder"] is not None for item in members),
            }
        )
    actor_groups = []
    actor_index: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if row["direction"] == "client_to_server":
            actor_index[(row["actor_id_candidate"], row["row_id"])].append(row)
    for (actor_id, row_id), members in sorted(actor_index.items()):
        actor_groups.append(
            {
                "actor_id": actor_id,
                "row_id": row_id,
                "packet_count": len(members),
                "unique_decoded_message_count": len({item["decoded_sha256"] for item in members}),
                "capture_indexes": sorted({item["capture_index"] for item in members}),
                "actor_offsets": [10, 14],
                "row_id_offset": 65,
                "all_actor_duplicates_equal": all(item["actor_id_candidate"] == item["actor_id_duplicate"] for item in members),
                "all_independent_identity_checks_pass": all(bool(item["actor_identity_cross_check"]) for item in members),
            }
        )
    result = {
        "schema": "rune-rowid-high-interest-hit-analysis-v56",
        "capture_scan_sha256": digest((OUT / "capture_scan.json").read_bytes()),
        "old_resource_sha256": old["source_sha256"],
        "target_old_rows": target_rows,
        "groups": grouped,
        "client_to_server_actor_row_groups": actor_groups,
        "examples": rows,
        "assessment": {
            "stable_network_candidates": [268500301, 268500302],
            "candidate_field": "decoded UDP message offset +65 in signature e547d300, length 214",
            "actor_binding_observationally_cross_checked": True,
            "environment_pair_present": False,
            "setter_or_opcode_binding_proven": False,
            "production_actor_remap_input": False,
            "safe_shadow_observation_contract": "C2S only: UDP length 214, signature e547d300, phase 3, u32@+10 == u32@+14 == independently known actor, u32@+65 is a candidate Rune RowId.",
            "reason": "The actor is independently cross-checked, but the RowId field still has no static link to the +0x1384 setter/part slot, no environment pair, and only an older DNT snapshot. It is safe for shadow telemetry, not for functional remapping.",
        },
    }
    destination = OUT / "capture_hit_analysis.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"examples": len(rows), "groups": len(grouped), "output": str(destination)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
