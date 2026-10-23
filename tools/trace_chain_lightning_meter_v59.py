"""Focused, reproducible audit of the built-in meter path for skill 3019.

This module is deliberately independent from production attribution.  It reads
the hash-pinned decoded client, one diagnostic capture, and prior audit
sidecars.  It never executes or loads the client and never mutates production
code or scoring data.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".tmp/reverse_tools_v39"))

import capstone

from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from tools.analyze_npcap_trace import read_trace
from tools.review_client_damage_types_v44 import BASE, StaticView


OUT = ROOT / "outputs/chain_lightning_meter_v59_20260909"
CAPTURE = Path(
    "C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/"
    "0821黑典女王.dpscap"
)
CLIENT = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
CAPTURE_SHA256 = "40be2e0bc2cf58cb3ba3dec707cfc119fc18bef29ebb2967b8779f20402c5d17"
SKILL_ID = 3019
SKILL_NAME = "连锁闪电"
FOCUS_ACTOR_ID = 1314843433
NATIVE_ROWS = ROOT / "outputs/chain_lightning_capture_v59_20260909/native_rows.json"
FIXED_BASELINE = ROOT / "outputs/current_native_baseline_v57_20260909/profession_direct_damage_report.json"


STATIC_RANGES = {
    "trans_action_dispatch": (0xA57A60, 0xA5AD60),
    "type_0e_action_branch": (0xA59780, 0xA59A07),
    "type_66_register": (0xA5A801, 0xA5A87F),
    "type_67_clear": (0xA5A884, 0xA5A8C9),
    "type_68_batch_register": (0xA5A8CE, 0xA5A960),
    "actor_register_by_skill": (0xA4A950, 0xA4AA52),
    "manager_resolve_skill": (0x38E180, 0x38E21D),
    "manager_register_pair": (0x38D3F0, 0x38D78C),
    "manager_clear_channel": (0x38EB30, 0x38ED78),
    "skill_user_find_by_row": (0x376AA0, 0x376C26),
    "skill_user_activate_by_row": (0x37C460, 0x37C714),
    "current_pair_setter": (0x375940, 0x3766E8),
    "remove_by_skill_and_clear_current": (0x379090, 0x379AA8),
    "meter_key_resolution": (0x6A5646, 0x6A5916),
    "meter_remembered_key_writer": (0x6A6C60, 0x6A6EF2),
    "action_transition_to_meter_writer": (0x5286C0, 0x528D00),
}


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _disassemble(view: StaticView, start: int, end: int) -> list[dict[str, object]]:
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    rows = []
    for ins in decoder.disasm(view.read(start, end - start), BASE + start):
        rows.append(
            {
                "rva": int(ins.address - BASE),
                "bytes": ins.bytes.hex(),
                "instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
            }
        )
    return rows


def _static_evidence() -> dict[str, object]:
    if _digest(CLIENT) != CLIENT_SHA256:
        raise ValueError("client hash mismatch")
    view = StaticView()
    ranges = {
        name: _disassemble(view, start, end)
        for name, (start, end) in STATIC_RANGES.items()
    }
    by_rva = {
        int(row["rva"]): str(row["instruction"])
        for rows in ranges.values()
        for row in rows
    }
    required_fragments = {
        0xA597B0: "r8d, [rbx + 4]",
        0xA597BD: "call 0x14022f260",
        0xA5990E: "edx, dword ptr [rsp + 0x58]",
        0xA59A00: "call qword ptr [rax + 0x80]",
        0x37C497: "call 0x140376aa0",
        0x37C69B: "call qword ptr [rax + 0x10]",
        0x375AC7: "movups xmm0, xmmword ptr [r12]",
        0x375ACC: "movups xmmword ptr [rsi + 0x10], xmm0",
        0xA5A86F: "r8d, dword ptr [rsp + 0x54]",
        0xA5A878: "call qword ptr [rax + 0x410]",
        0xA5A8C2: "call qword ptr [rax + 0x418]",
        0xA5A952: "call qword ptr [rax + 0x410]",
        0xA4A97D: "call 0x14038e180",
        0xA4AA1A: "call 0x14038d3f0",
        0x6A5717: "mov esi, dword ptr [r11 + rcx*8 + 0x10]",
        0x6A57C8: "lea rcx, [rax + 0x6a0]",
        0x6A5823: "call qword ptr [rax + 0x10]",
        0x528C3B: "call qword ptr [rdx + 0x10]",
        0x528C4B: "call 0x1406a6c60",
        0x6A6D65: "mov dword ptr [rbp - 0x20], r14d",
    }
    for rva, fragment in required_fragments.items():
        if rva not in by_rva or fragment not in by_rva[rva]:
            raise AssertionError(
                f"static contract mismatch at 0x{rva:X}: "
                f"expected {fragment!r}, got {by_rva.get(rva)!r}"
            )
    return {
        "client": str(CLIENT),
        "client_sha256": CLIENT_SHA256,
        "decoded_segment_manifest_sha256": view.inputs.get(
            str(ROOT / "outputs/client_protection_v44_20260908/segments_manifest.json")
        ),
        "ranges": ranges,
        "required_instructions": {
            hex(rva): by_rva[rva] for rva in sorted(required_fragments)
        },
    }


def _native_focus_rows() -> tuple[list[dict[str, object]], dict[str, list[dict[str, object]]]]:
    report = json.loads(NATIVE_ROWS.read_text(encoding="utf-8"))
    capture = report["captures"][0]
    if capture["capture_sha256"] != CAPTURE_SHA256:
        raise ValueError("native-row capture hash mismatch")
    rows = [
        row
        for row in capture["rows"]
        if int(row.get("source_actor_id") or 0) == FOCUS_ACTOR_ID
    ]
    by_sha: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_sha[str(row["record_sha256"])].append(row)
    return rows, by_sha


def _u32(raw: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<I", raw, offset)[0]


def _parse_control_record(record_type: int, payload: bytes) -> dict[str, object] | None:
    if record_type == 0x0E and len(payload) in (0x1F, 0x23):
        return {
            "operation": "ACTION",
            "skill_id": _u32(payload),
            "action_variant": payload[4],
            "branch_flag": payload[5],
            "tail_hex": payload[6:].hex(),
        }
    if record_type == 0x12 and len(payload) == 0x20:
        return {
            "operation": "SECONDARY_ACTION",
            "skill_id": _u32(payload),
            "flag": payload[4],
            "tail_hex": payload[5:].hex(),
        }
    if record_type == 0x66 and len(payload) == 9:
        return {
            "operation": "REGISTER",
            "channel": _u32(payload),
            "skill_id": _u32(payload, 4),
            "flag": payload[8],
        }
    if record_type == 0x67 and len(payload) == 4:
        return {"operation": "CLEAR", "channel": _u32(payload)}
    if record_type == 0x68 and len(payload) >= 4:
        count = _u32(payload)
        if len(payload) != 4 + count * 8:
            return None
        return {
            "operation": "BATCH_REGISTER",
            "entries": [
                {"channel": _u32(payload, 4 + index * 8),
                 "skill_id": _u32(payload, 8 + index * 8)}
                for index in range(count)
            ],
        }
    return None


def _scan_capture(native_by_sha: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    if _digest(CAPTURE) != CAPTURE_SHA256:
        raise ValueError("capture hash mismatch")

    transport = protocol.V576TransportReassembler()
    first_timestamp: float | None = None
    counts: Counter[str] = Counter()
    record_shapes: Counter[str] = Counter()
    focus_events: list[dict[str, object]] = []
    focus_hits: list[dict[str, object]] = []
    registrations: dict[int, dict[int, int]] = defaultdict(dict)
    current_skill: dict[int, int] = {}
    current_channels: dict[int, tuple[int, ...]] = {}
    state_revision: Counter[int] = Counter()
    unresolved_native_rows = {
        id(row): row
        for rows in native_by_sha.values()
        for row in rows
    }

    sequence = 0
    for frame, (timestamp, _adapter, original_length, raw) in enumerate(
        read_trace(CAPTURE), 1
    ):
        if first_timestamp is None:
            first_timestamp = timestamp
        packet = protocol.decode_captured_frame(timestamp, raw, original_length)
        if packet is None:
            continue
        for logical_index, logical_packet in enumerate(transport.feed(packet)):
            if logical_packet.transport != "UDP":
                continue
            direction = logical_packet.inferred_direction()
            for message_index, wire in enumerate(protocol.split_v576_payload(logical_packet.payload)):
                inventory = inspect_udp_damage_records(wire, direction)
                if not inventory.complete:
                    counts[f"unframed:{inventory.rejection_reason}"] += 1
                    continue
                elapsed_ms = round((timestamp - first_timestamp) * 1000.0)
                for record_index, typed in enumerate(inventory.typed_records):
                    sequence += 1
                    record_type = int(typed.record_type)
                    actor_id = _u32(typed.record_bytes)
                    payload = typed.record_bytes[7:]
                    record_shapes[f"0x{record_type:02X}:{typed.declared_length}"] += 1
                    parsed = _parse_control_record(record_type, payload)
                    if parsed is not None:
                        event = {
                            "sequence": sequence,
                            "frame": frame,
                            "logical_index": logical_index,
                            "message_index": message_index,
                            "record_index": record_index,
                            "elapsed_ms": elapsed_ms,
                            "actor_id": actor_id,
                            "record_type": f"0x{record_type:02X}",
                            "declared_length": int(typed.declared_length),
                            "wire_record_sha256": typed.wire_record_sha256,
                            **parsed,
                        }
                        operation = str(parsed["operation"])
                        if operation == "REGISTER":
                            registrations[actor_id][int(parsed["channel"])] = int(parsed["skill_id"])
                        elif operation == "BATCH_REGISTER":
                            for entry in parsed["entries"]:
                                registrations[actor_id][int(entry["channel"])] = int(entry["skill_id"])
                        elif operation == "CLEAR":
                            channel = int(parsed["channel"])
                            removed = registrations[actor_id].pop(channel, None)
                            event["removed_skill_id"] = removed
                            if channel in current_channels.get(actor_id, ()):
                                current_skill.pop(actor_id, None)
                                current_channels.pop(actor_id, None)
                                state_revision[actor_id] += 1
                        elif operation == "ACTION":
                            skill_id = int(parsed["skill_id"])
                            channels = tuple(
                                channel
                                for channel, registered_skill in registrations[actor_id].items()
                                if registered_skill == skill_id
                            )
                            # The client resolves by exact SkillTable RowId.  A
                            # packet replay may retain the RowId even if an
                            # earlier registration packet is absent from the
                            # capture; registry presence is therefore recorded
                            # as a separate confidence fact, not fabricated.
                            event["registered_channels"] = list(channels)
                            event["registry_contains_skill"] = bool(channels)
                            current_skill[actor_id] = skill_id
                            current_channels[actor_id] = channels
                            state_revision[actor_id] += 1
                            event["state_revision"] = state_revision[actor_id]
                        if (
                            actor_id == FOCUS_ACTOR_ID
                            or int(parsed.get("skill_id") or 0) == SKILL_ID
                            or any(int(entry.get("skill_id") or 0) == SKILL_ID
                                   for entry in parsed.get("entries", []))
                            or int(event.get("removed_skill_id") or 0) == SKILL_ID
                        ):
                            focus_events.append(event)

                    if record_type != 0x08:
                        continue
                    # There can be only one physical damage row for this typed
                    # boundary.  Match it to the prior native audit by the
                    # record hash plus immutable wire fields.
                    damage_row = next(
                        (
                            row
                            for row in inventory.damage_records
                            if row.wire_record_sha256 == typed.wire_record_sha256
                        ),
                        None,
                    )
                    if damage_row is None or int(damage_row.source_actor_id) != FOCUS_ACTOR_ID:
                        continue
                    matches = [
                        row
                        for row in native_by_sha.get(damage_row.wire_record_sha256, ())
                        if int(row["source_actor_id"]) == int(damage_row.source_actor_id)
                        and int(row["timestamp_ms"]) == elapsed_ms
                        and int(row["record_offset"]) == int(damage_row.record_offset)
                        and int(row["target_actor_id"]) == int(damage_row.target_actor_id)
                        and int(row["result_group_id"]) == int(damage_row.damage_result_group_id)
                        and (
                            damage_row.damage_object_id is None
                            or int(row.get("damage_object_id") or 0) == int(damage_row.damage_object_id)
                        )
                    ]
                    native = matches[0] if len(matches) == 1 else None
                    if native is not None:
                        unresolved_native_rows.pop(id(native), None)
                    active_skill = int(current_skill.get(FOCUS_ACTOR_ID, 0))
                    focus_hits.append(
                        {
                            "sequence": sequence,
                            "frame": frame,
                            "message_index": message_index,
                            "record_index": record_index,
                            "elapsed_ms": elapsed_ms,
                            "family": damage_row.family,
                            "target_actor_id": int(damage_row.target_actor_id),
                            "damage": int(damage_row.damage),
                            "damage_object_id": (
                                int(damage_row.damage_object_id)
                                if damage_row.damage_object_id is not None
                                else None
                            ),
                            "result_group_id": int(damage_row.damage_result_group_id),
                            "wire_record_sha256": damage_row.wire_record_sha256,
                            "record_offset": int(damage_row.record_offset),
                            "active_skill_id": active_skill,
                            "active_state_revision": int(state_revision[FOCUS_ACTOR_ID]),
                            "active_registered_channels": list(
                                current_channels.get(FOCUS_ACTOR_ID, ())
                            ),
                            "native_match_count": len(matches),
                            "native_skill_id": int(native.get("skill_id") or 0) if native else 0,
                            "native_skill_name": str(native.get("skill_name") or "") if native else "",
                            "native_method": str(native.get("method") or "") if native else "",
                            "native_deterministic": bool(native.get("independent_deterministic")) if native else False,
                        }
                    )

    focus_3019 = [row for row in focus_hits if int(row["native_skill_id"]) == SKILL_ID]
    deterministic = [row for row in focus_3019 if row["native_deterministic"]]
    nondeterministic = [row for row in focus_3019 if not row["native_deterministic"]]
    state_candidates = [row for row in focus_hits if int(row["active_skill_id"]) == SKILL_ID]
    truth_candidates = [row for row in state_candidates if row["native_deterministic"]]
    truth_matches = [row for row in truth_candidates if int(row["native_skill_id"]) == SKILL_ID]
    truth_conflicts = [row for row in truth_candidates if int(row["native_skill_id"]) != SKILL_ID]
    recovered = [
        row
        for row in state_candidates
        if not row["native_deterministic"] and int(row["native_skill_id"]) == SKILL_ID
    ]
    recovered_other_labels = [
        row
        for row in state_candidates
        if not row["native_deterministic"]
        and int(row["native_skill_id"]) not in (0, SKILL_ID)
    ]
    return {
        "capture": str(CAPTURE),
        "capture_sha256": CAPTURE_SHA256,
        "focus_actor_id": FOCUS_ACTOR_ID,
        "record_shapes": dict(sorted(record_shapes.items())),
        "counts": {
            **dict(sorted(counts.items())),
            "focus_actor_native_rows_seen": len(focus_hits),
            "focus_actor_native_rows_unmatched": len(unresolved_native_rows),
            "skill_3019_rows": len(focus_3019),
            "skill_3019_deterministic_rows": len(deterministic),
            "skill_3019_nondeterministic_rows": len(nondeterministic),
            "skill_3019_deterministic_result_groups": len(
                {int(row["result_group_id"]) for row in deterministic}
            ),
            "skill_3019_nondeterministic_result_groups": len(
                {int(row["result_group_id"]) for row in nondeterministic}
            ),
            "state_candidate_rows": len(state_candidates),
            "state_candidate_deterministic_truth_rows": len(truth_candidates),
            "state_candidate_truth_matches": len(truth_matches),
            "state_candidate_truth_conflicts": len(truth_conflicts),
            "state_candidate_recovered_3019_rows": len(recovered),
            "state_candidate_other_nondeterministic_labels": len(recovered_other_labels),
        },
        "state_candidate_truth_conflict_skills": dict(sorted(Counter(
            str(row["native_skill_id"]) for row in truth_conflicts
        ).items())),
        "state_candidate_other_nondeterministic_skills": dict(sorted(Counter(
            str(row["native_skill_id"]) for row in recovered_other_labels
        ).items())),
        "candidate_status": {
            "client_state_reconstructed": False,
            "deterministic_promotions": 0,
            "recovered_field_is_only_same_label_candidate": True,
            "reason": "0x0E receipt is not proof of successful client activation or remembered-meter lifecycle",
        },
        "focus_control_events": focus_events,
        "focus_hits": focus_hits,
        "skill_3019_hits": focus_3019,
        "state_candidate_truth_conflicts": truth_conflicts,
        "state_candidate_recovered_3019": recovered,
    }


def _fixed_baseline() -> dict[str, object]:
    data = json.loads(FIXED_BASELINE.read_text(encoding="utf-8"))
    # Locate the canonical 雷神/3019 entry without relying on console encoding.
    found = []
    def walk(value: object) -> None:
        if isinstance(value, dict):
            if str(value.get("skill_id") or "") == str(SKILL_ID):
                text = json.dumps(value, ensure_ascii=False)
                if "雷神" in text:
                    found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data)
    compact = []
    seen = set()
    for item in found:
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if signature not in seen:
            seen.add(signature)
            compact.append(item)
    return {"matches": compact}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/chain_lightning_meter_v60_20260909/v59_completed.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    native_rows, native_by_sha = _native_focus_rows()
    static = _static_evidence()
    capture = _scan_capture(native_by_sha)
    report = {
        "schema": "chain-lightning-meter-v59",
        "scope": "skill 3019 only; static client plus ordered capture replay",
        "skill": {"skill_id": SKILL_ID, "skill_name": SKILL_NAME},
        "policy": {
            "discarded_skill_id": 3404,
            "forbidden": [
                "time proximity",
                "fixed action window",
                "damage fingerprint",
                "Hit spacing",
            ],
            "production_files_modified": False,
        },
        "inputs": {
            "client": str(CLIENT),
            "client_sha256": CLIENT_SHA256,
            "capture": str(CAPTURE),
            "capture_sha256": CAPTURE_SHA256,
            "native_rows": str(NATIVE_ROWS),
            "native_rows_sha256": _digest(NATIVE_ROWS),
            "fixed_baseline": str(FIXED_BASELINE),
            "fixed_baseline_sha256": _digest(FIXED_BASELINE),
        },
        "static_evidence": static,
        "fixed35_baseline": _fixed_baseline(),
        "capture_evidence": capture,
        "native_focus_actor_rows": len(native_rows),
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Markdown is written after capture evidence has been reviewed.  Keeping
    # this first pass JSON-only prevents the prose from overstating a rule.
    print(json.dumps({"output": str(args.output), "counts": capture["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
