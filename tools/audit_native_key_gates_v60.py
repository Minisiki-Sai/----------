"""Read-only native key gate audit; stdout only, no attribution replay.

Default input is the hash-pinned 0821 black queen capture and existing v59
native-row sidecar. Counts are saved emission rows, NOT new unique hits. The
capture is walked once for exact physical bytes; no decoder state is replayed.
Only the direct41 common_cc offset is an integration candidate. Signed-to-A4
is a counterfactual, because the intervening client state is not on the wire.
Run: py -3.12 -B tools/audit_native_key_gates_v60.py

Audited 2026-09-09: 56,671 saved rows / 56,671 physical identities, zero
unmatched. All 24,627 direct41 rows have a different common_cc gate; 24,251
become resolvable and ALL already have the same packet_skill_id. The player
caller skips exact_packet_skill, so this is a dormant reader correction,
not a coverage gain. Signed-only opens 145 direct37 rows: 110 already have
packet_entity_template_owner(3409), 11 summon_owner(3412), and 24
skill_object_owner(2410). No observed label conflicts do not prove A4's
runtime reachability. Keep the signed change out of integration.patch.
All 1,521 saved 3019 rows have zero resolver change; new certified hits=0.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.native_damage_skill_keys import (
    STATIC_INTERNAL_ATTACK_PARENT, _selected_native_key,
)
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

CAPTURE = Path("C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/0821黑典女王.dpscap")
CAPTURE_SHA = "40be2e0bc2cf58cb3ba3dec707cfc119fc18bef29ebb2967b8779f20402c5d17"
SIDECAR = ROOT / "outputs/chain_lightning_capture_v59_20260909/native_rows.json"
SIDECAR_SHA = "484034095c865836b90f0a3024634450797783c3d81aa656e39ee7d4978f26c9"
PATCH = ROOT / "outputs/native_key_gates_v60_20260909/integration.patch"
ENTRY_CLASSES = frozenset((42, 43, 44, 51, 111, 112, 113, 114, 136, 149,
                          157, 158, 161, 170, 177, 223, 236, 244, 325, 624, 638))
SPECIAL_LABEL_CLASSES = frozenset((15, 16, 42, 44, 136, 177, 325, 624, 638))
NATIVE_METHODS = frozenset(("packet_skill_id", "packet_native_attack_key",
                           "packet_entity_native_attack_key"))


def meter_entry_allows(amount: int, flag76: int, result94: int) -> bool:
    """Only the numeric entry gate; caller/pair/meter state also must be valid."""
    return amount > 0 and (flag76 != 0 or result94 in ENTRY_CLASSES)


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def production_signature() -> str:
    paths = sorted((ROOT / "dragon_nest_builder").rglob("*.py"))
    return sha256("\n".join(f"{p.relative_to(ROOT)}:{digest(p)}" for p in paths).encode()).hexdigest()


def signed32(value: int) -> int:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("not a wire u32")
    return value - 0x100000000 if value >= 0x80000000 else value


def selected_candidate(record, *, fix_offset: bool, fix_signed: bool):
    """Counterfactual selector, NOT a complete implementation of the meter.

    Preserve unsigned raw IDs in evidence. Signed comparison alone does not
    establish that A4 is reachable; it is deliberately an audit-only switch.
    """
    raw = record.record_bytes
    cc_at = 104 if fix_offset and record.family == "direct41" else 100
    if len(raw) <= cc_at:
        return None
    kind = int.from_bytes(raw[11:12], "little", signed=True)
    cc = raw[cc_at]
    primary_at, fallback_at = (106, 118) if record.family == "direct37" else (110, 122)

    def u32(at):
        return int.from_bytes(raw[at:at + 4], "little") if at + 4 <= len(raw) else 0

    primary, fallback = u32(primary_at), u32(fallback_at)
    positive = (lambda v: signed32(v) > 0) if fix_signed else (lambda v: v > 0)
    if kind == 1 and cc == 0 and positive(primary):
        return "primary90", primary, kind, cc
    if positive(fallback):
        return "fallbackA4", fallback, kind, cc
    return None


@dataclass(frozen=True)
class TerminalFacts:
    """Explicit runtime facts at the LATE A4 branch, not inferred from IDs.

    earlier_key includes related-object, remembered-vector, actor/component
    and special-object paths. component_key is the 6A60FD call's result.
    None means unknown; neither a missing UID nor an absent sidecar proves it.
    """
    earlier_key: int | None = None
    source_component_pair_valid: bool | None = None
    component_valid: bool | None = None
    component_key: int | None = None
    meter_entry_allowed: bool | None = None
    classification_uses_key: bool | None = None


def terminal_a4_key(fallback: int, facts: TerminalFacts) -> int | None:
    """Conservative certificate for just the terminal branch, never production."""
    if (facts.earlier_key is None or facts.earlier_key > 0
            or facts.source_component_pair_valid is not True
            or facts.meter_entry_allowed is not True
            or facts.classification_uses_key is not True):
        return None
    if not (facts.component_valid is False
            or facts.component_valid is True and facts.component_key == 0):
        return None
    return fallback if signed32(fallback) > 0 else None


def static_audit():
    from tools.review_client_damage_types_v44 import BASE, CodeReview, StaticView
    view = StaticView()  # Checks original client AND all decoded segment hashes.
    instructions = CodeReview(view).decode(view.function(0x6A52E0))
    by_rva = {r.address - BASE: r for r in instructions}
    anchors = {
        0x6A537A: "cmp qword ptr [r15 + 0x30], 0",
        0x6A5385: "cmp byte ptr [r15 + 0x76], 0",
        0x6A538C: "mov ecx, dword ptr [r15 + 0x94]",
        0x6A5646: "mov esi, dword ptr [r15 + 0x90]",
        0x6A5671: "test esi, esi",
        0x6A5673: "jg 0x1406a569c",
        0x6A5693: "call qword ptr [rax + 0x30]",
        0x6A5717: "mov esi, dword ptr [r11 + rcx*8 + 0x10]",
        0x6A5B1A: "lea ecx, [rbx - 0x6f]",
        0x6A5B1D: "cmp ecx, 3",
        0x6A5F02: "test rcx, rcx",
        0x6A5F05: "je 0x1406a618a",
        0x6A6015: "jg 0x1406a5f78",
        0x6A60BB: "je 0x1406a615f",
        0x6A60CC: "je 0x1406a615f",
        0x6A60FD: "call qword ptr [rax + 0x10]",
        0x6A6100: "test eax, eax",
        0x6A6102: "je 0x1406a615f",
        0x6A6164: "mov edx, dword ptr [rax + 0xa4]",
        0x6A6174: "test edx, edx",
        0x6A6176: "jg 0x1406a5f89",
        0x6A5F89: "mov ecx, dword ptr [rax + 0x94]",
        0x6A5F8F: "cmp ecx, 0x88",
        0x6A5F9D: "sub ecx, 0xf",
        0x6A5FA6: "sub ecx, 1",
        0x6A5FAF: "sub ecx, 0x1a",
        0x6A5FB8: "cmp ecx, 2",
        0x6A62E5: "sub ecx, 0xb1",
        0x6A62F1: "sub ecx, 0x94",
        0x6A62FD: "sub ecx, 0x12b",
        0x6A6309: "cmp ecx, 0xe",
    }
    verified = []
    for at, expected in anchors.items():
        ins = by_rva[at]
        actual = f"{ins.mnemonic} {ins.op_str}"
        if actual != expected:
            raise ValueError(f"client instruction changed at {at:x}: {actual}")
        verified.append({"rva": hex(at), "bytes": ins.bytes.hex(), "instruction": actual})
    jumps = struct.unpack("<II", view.read(0x6A6B84, 8))
    table = view.read(0x6A6B8C, 203)
    classes = {42 + i for i, index in enumerate(table) if jumps[index] == 0x6A53DB}
    classes.update((325, 624, 638))
    if classes != ENTRY_CLASSES:
        raise ValueError("meter classification jump table changed")
    return {
        "client_sha256": view.manifest["client_sha256"],
        "verified_anchors": verified,
        "entry": {"amount_must_be_positive": True, "flag76_nonzero_or_result94_in": sorted(classes)},
        "label_classification_special_paths": sorted(SPECIAL_LABEL_CLASSES),
        "order": ["result90", "valid related object virtual30 if signed key<=0",
                  "remembered vector for sentinel -1/0", "actor current component",
                  "result94 111..114 component/element/actor match", "special object paths",
                  "validated source component pair, earlier key<=0, current component missing/invalid/zero",
                  "terminal signed-positive A4", "result94 label classification"],
        "wire_alone_proves_terminal_reachability": False,
        "note": "A4 is conditional and late; a valid nonzero current component key bypasses it. Missing runtime evidence is unknown, not false.",
    }


def row_identity(row):
    return (row["record_sha256"], row["family"], row["record_offset"],
            row["source_actor_id"], row["target_actor_id"], row["result_group_id"],
            row["damage_object_id"])


def physical_identity(record):
    return (record.wire_record_sha256, record.family, record.record_offset,
            record.source_actor_id, record.target_actor_id,
            record.damage_result_group_id, record.damage_object_id)


def scan_physical(capture: Path, wanted: set, deadline: float):
    from tools.analyze_npcap_trace import read_trace
    from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame, split_v576_payload
    found, counts = {}, Counter()
    # Stateless UDP walk only. No V576DiagnosticReplaySource, TCP buffers,
    # child processes, output files, or changes to decoder methods/caches.
    for frame_index, (timestamp, adapter, original_length, raw) in enumerate(read_trace(capture), 1):
        if time.monotonic() > deadline:
            raise TimeoutError("bounded physical audit deadline exceeded")
        counts["frames"] += 1
        packet = decode_captured_frame(timestamp, raw, original_length)
        if packet is None or packet.transport != "UDP":
            continue
        for message_index, wire in enumerate(split_v576_payload(packet.payload)):
            inventory = inspect_udp_damage_records(wire, packet.inferred_direction())
            if not inventory.complete:
                continue
            for record in inventory.damage_records:
                identity = physical_identity(record)
                if identity not in wanted or not record.admissible_fields:
                    continue
                counts["matching_physical_occurrences"] += 1
                if identity in found:
                    if found[identity][0].record_bytes != record.record_bytes:
                        raise ValueError("physical identity collision")
                    continue
                found[identity] = (record, {"frame_index": frame_index,
                    "message_index": message_index, "timestamp": timestamp,
                    "adapter": adapter, "wire_sha256": inventory.wire_sha256,
                    "wire_hex": wire.hex()})
    return found, dict(counts)


def audit(capture=CAPTURE, sidecar=SIDECAR, *, timeout_seconds=180, include_static=True):
    from dragon_nest_builder.data_loader import DATA_DIR
    from dragon_nest_builder.live_dps.sources import (
        _LIVE_DAMAGE_SKILL_ALIASES, _load_damage_skill_ids, _load_skill_catalog,
    )
    from dragon_nest_builder.live_dps.damage_attribution import is_deterministic_attribution, should_replace_attribution
    from tools.audit_native_damage_v46 import parse_record

    started = time.monotonic()
    before = production_signature()
    if digest(capture) != CAPTURE_SHA:
        raise ValueError("capture hash mismatch")
    sidecar_sha = digest(sidecar)
    if sidecar == SIDECAR and sidecar_sha != SIDECAR_SHA:
        raise ValueError("default sidecar hash changed; re-audit saved row baseline")
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    captures = [c for c in document["captures"] if c["capture_sha256"] == CAPTURE_SHA]
    if len(captures) != 1:
        raise ValueError("expected exactly one matching sidecar capture")
    rows = captures[0]["rows"]
    catalog = _load_skill_catalog(str(DATA_DIR.resolve()))
    damage_ids = _load_damage_skill_ids(str(DATA_DIR.resolve()))
    damage_ids = damage_ids.union(raw for raw, key in _LIVE_DAMAGE_SKILL_ALIASES.items() if key in damage_ids)

    def mapped(selected):
        if selected is None:
            return 0
        raw = STATIC_INTERNAL_ATTACK_PARENT.get(selected[1], selected[1])
        key = _LIVE_DAMAGE_SKILL_ALIASES.get(raw, raw)
        return key if key in catalog and key in damage_ids else 0

    physical, scan_counts = scan_physical(capture, {row_identity(r) for r in rows}, started + timeout_seconds)
    missing = [row_identity(r) for r in rows if row_identity(r) not in physical]
    if missing:
        raise ValueError(f"unmatched sidecar rows: {len(missing)}; first: {missing[:2]}")
    stats, by_variant, changes, examples = Counter(), defaultdict(Counter), defaultdict(Counter), {}
    for row in rows:
        record, origin = physical[row_identity(row)]
        raw = record.record_bytes
        prefix = parse_record(raw)
        cc_at = 104 if record.family == "direct41" else 100
        if prefix.field_offsets["common_15"] + 4 != cc_at:
            raise ValueError("typed family disagrees with closed common prefix")
        for field, value in (("source_kind", prefix.source_kind),
                             ("common_cc", raw[100]),
                             ("primary_raw", int.from_bytes(raw[110:114] if record.family == "direct41" else raw[106:110], "little")),
                             ("fallback_raw", int.from_bytes(raw[122:126] if record.family == "direct41" else raw[118:122], "little"))):
            if row["native"][field] != value:
                raise ValueError(f"saved sidecar field disagrees with actual bytes: {field}")
        baseline = _selected_native_key(record)
        if baseline != selected_candidate(record, fix_offset=False, fix_signed=False):
            raise ValueError("production selector has changed; re-audit baseline")
        baseline_key = mapped(baseline)
        assigned, method = int(row["skill_id"]), row["method"]
        stats["saved_rows"] += 1
        stats[f"family:{record.family}"] += 1
        stats[f"gates:{record.family}|kind={prefix.source_kind}|uid={prefix.uid_branch}|high={int(prefix.source_id >= 100000)}|cc={raw[cc_at]}"] += 1
        stats[f"baseline_method:{method}"] += 1
        stats["old_sidecar_false_independent_native_rows"] += int(bool(row["independent_deterministic"]) and method in NATIVE_METHODS)
        if record.family == "direct41" and raw[100] != raw[104]:
            stats["direct41_cc_byte_differs"] += 1
        if record.family == "direct41" and (raw[100] == 0) != (raw[104] == 0):
            stats["direct41_primary_gate_differs"] += 1
        primary_at = 106 if record.family == "direct37" else 110
        primary = int.from_bytes(raw[primary_at:primary_at + 4], "little")
        stats["primary_FFFFFFFF"] += int(primary == 0xFFFFFFFF)
        stats["primary_other_negative"] += int(signed32(primary) < 0 and primary != 0xFFFFFFFF)
        for name, offset, signed in (("offset_only", True, False), ("signed_only", False, True), ("offset_and_signed", True, True)):
            candidate = selected_candidate(record, fix_offset=offset, fix_signed=signed)
            key = mapped(candidate)
            c = by_variant[name]
            c["selected_tuple_changed"] += int(candidate != baseline)
            c["resolved_key_changed"] += int(key != baseline_key)
            c["newly_resolvable_saved_rows"] += int(key > 0 and baseline_key == 0)
            if key != baseline_key:
                c["changed_already_same_assignment"] += int(key > 0 and key == assigned)
                c["changed_conflicts_assigned"] += int(key > 0 and assigned > 0 and key != assigned)
                c["changed_unlabelled"] += int(key > 0 and assigned <= 0)
                independent = is_deterministic_attribution(method) and method not in NATIVE_METHODS
                c["changed_independent_match"] += int(independent and key == assigned and key > 0)
                c["changed_independent_conflict"] += int(independent and key != assigned and key > 0)
                c["changed_same_field_packet_skill"] += int(method == "packet_skill_id")
                route = "packet_native_attack_key" if record.source_actor_id >= 100000 else "packet_entity_native_attack_key"
                replaceable = should_replace_attribution(method, route)
                c["priority_allows_replacement_upper_bound"] += int(replaceable)
                c["priority_allows_conflict_upper_bound"] += int(replaceable and key > 0 and assigned > 0 and key != assigned)
                c["exact_packet_skill_player_caller_skip"] += int(route == "packet_native_attack_key" and method == "packet_skill_id")
                c["entity_priority_replacement_upper_bound"] += int(route == "packet_entity_native_attack_key" and replaceable)
                changes[name][f"{record.family}|{primary}|{baseline_key}->{key}|assigned={assigned}|{method}"] += 1
                example_name = f"{name}:{'conflict' if key != assigned else 'same'}:{record.family}:{method}"
                if example_name not in examples:
                    examples[example_name] = {"row": row, "origin": origin,
                        "record_hex": raw.hex(), "baseline": baseline,
                        "candidate": candidate, "mapped_candidate": key}
            if assigned == 3019:
                c["3019_saved_rows"] += 1
                c["3019_resolved_key_changed"] += int(key != baseline_key)
                c["3019_candidate_matches"] += int(key == 3019)
    after = production_signature()
    if before != after:
        raise ValueError("production source tree changed during audit")
    return {
        "schema": "native-key-gates-v60", "capture": str(capture),
        "capture_sha256": CAPTURE_SHA, "sidecar": str(sidecar), "sidecar_sha256": sidecar_sha,
        "count_unit": "saved emission rows; physical identity join does not deduplicate emissions or certify new hits",
        "production_signature_before": before, "production_signature_after": after,
        "production_changed": False, "attribution_replay_processes": 0,
        "scan": scan_counts, "unique_physical_identities": len(physical), "unmatched_saved_rows": len(missing),
        "stats": dict(stats), "variants": {k: dict(v) for k, v in by_variant.items()},
        "changed_groups": {k: dict(v) for k, v in changes.items()}, "examples": examples,
        "static": static_audit() if include_static else None,
        "safe_integration_scope": "common_cc raw104 for direct41, raw100 for direct37; signed-to-A4 excluded",
        "actual_new_certified_hits": 0, "actual_new_3019_hits": 0,
        "limitation": "Existing sidecar labels are comparisons, not fresh production output; priority alone does not prove caller, owner generation, PvE or job guards. No terminal A4 runtime certificate is recovered.",
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--examples", action="store_true", help="Include complete real packet fixtures on stdout")
    parser.add_argument("--timeout-seconds", type=int, default=180, choices=range(1, 301), metavar="1..300")
    args = parser.parse_args()
    report = static_audit() if args.static_only else audit(timeout_seconds=args.timeout_seconds)
    if not args.examples:
        report.pop("examples", None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
