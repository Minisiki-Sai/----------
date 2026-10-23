"""Read-only, bounded 32005/138/101687 result-index audit; JSON to stdout.

No replay, resource discovery, attribution, deduplication, or output writes.
Only existing v33 wires and a seek-bounded birth-to-last-sample raw window.
Index matches are falsifiable diagnostics, NEVER sufficient attack evidence.
Run with Python -B to keep the entire audit read-only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (
    PhysicalHit, decrypt, read_compact_action, read_self_attack_slot, specs_from_catalog,
)
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full

INPUT = ROOT / "outputs/compact_rejection_v33_20260908"
SEED = ROOT / "outputs/compact_entity_selectors_20260908/engineer_probe/0821硬核04.json"
CATALOG = ROOT / "outputs/v26_summon_attack_chain/static_catalog.json"
ENTITY, OBJECT, TEMPLATE = 138, 101687, 32005


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(record, offset, width=4):
    require(0 <= offset and offset + width <= len(record), "out-of-record field")
    return int.from_bytes(record[offset:offset + width], "little")


def inspect_sample(sample):
    """Validate raw hash/plain/boundaries and exactly one source/object result."""
    wire = bytes.fromhex(sample["wire"])
    require(sha256(wire).hexdigest() == sample["wire_sha256"], "wire hash mismatch")
    inventory = inspect_udp_damage_records(wire, sample["direction"])
    require(inventory.complete, inventory.rejection_reason)
    body = decrypt(wire, inventory.xor_phase)
    require(body.hex() == sample["plain"], "plaintext mismatch")
    require(inventory.xor_phase == sample["phase"], "phase mismatch")
    boundaries = [[integer(body, r.record_start), r.record_offset,
                   r.record_type, r.declared_length] for r in inventory.typed_records]
    require(boundaries == sample["records"], "record boundaries mismatch")
    hits = [h for h in inventory.damage_records if h.family == "direct37"
            and h.source_actor_id == ENTITY and h.damage_object_id == OBJECT]
    require(len(hits) == 1, "not exactly one selected 08/73 result")
    h = hits[0]
    require(h.admissible_fields, "invalid physical fields")
    physical = PhysicalHit("v34-read-only", h.wire_sha256,
        (sample["frame"], sample["message"], h.record_offset), ENTITY, 0, TEMPLATE,
        h.target_actor_id, h.damage, OBJECT, h.damage_result_group_id, wire,
        sample["direction"])
    slot = read_self_attack_slot(physical)
    require(slot is not None, "self-slot framing/physical validation failed")
    record = h.record_bytes[4:]
    require(sample["positions"] == [h.record_offset + 13], "unexpected object occurrence")
    return dict(frame=sample["frame"], file_offset=sample["file_offset"],
        message=sample["message"], time=sample["time"], delta=sample["delta"],
        marker=h.record_offset, phase=inventory.xor_phase, wire_sha256=h.wire_sha256,
        record_sha256=sha256(record).hexdigest(), record_hex=record.hex(),
        source=ENTITY, target=h.target_actor_id, object=OBJECT,
        result_group=h.damage_result_group_id, amount=h.damage,
        m95_u8=record[95], m98_u16=integer(record, 98, 2),
        m100_u16=integer(record, 100, 2), m102_u32=slot.monster_skill_id,
        m110_u32=integer(record, 110), m114_context_only=slot.context_skill_id,
        can_attribute_damage=False)


def ordinal_sweep(rows, actions, specs):
    """Test literal LE u8/u16/u32 at EVERY payload offset, zero/one based.

    Controls are exact per-record m102 catalog entries, not the object's old
    label. Flat typed-signal ordering and local attack ordinals are explicitly
    invented hypotheses for falsification; this does not certify their schema.
    A common field is required across controls. Bit packing and conditional
    layouts are outside this finite search, as are unparsed resource semantics.
    """
    controls = [r for r in rows if (TEMPLATE, r["m102_u32"]) in specs]
    require(len({r["m102_u32"] for r in controls}) >= 2, "need two self-skill controls")
    flat = [(i, j) for i, a in enumerate(actions) for j, s in enumerate(a["signals"])
            if s["type"] in (4, 15)]
    allowed = {kind: [] for kind in ("action", "signal", "flat_typed_attack", "local_attack")}
    for r in controls:
        names = specs[TEMPLATE, r["m102_u32"]].actions
        indices = {i for i, a in enumerate(actions) if a["name"] in names}
        pairs = {(i, j) for i, j in flat if i in indices}
        allowed["action"].append(indices)
        allowed["signal"].append({j for i, j in pairs})
        allowed["flat_typed_attack"].append({k for k, pair in enumerate(flat) if pair in pairs})
        allowed["local_attack"].append({k for i in indices
            for k, _ in enumerate([j for ai, j in flat if ai == i])})
    result = {}
    for kind, domains in allowed.items():
        survivors, tested = [], 0
        for width in (1, 2, 4):
            for offset in range(3, 119 - width):
                for base in (0, 1):
                    tested += 1
                    values = [integer(bytes.fromhex(r["record_hex"]), offset, width)
                              for r in controls]
                    if all(value - base in domain for value, domain in zip(values, domains)):
                        survivors.append(dict(offset=offset, width=width, base=base,
                            raw_values=sorted(set(values)), can_attribute_damage=False))
        result[kind] = dict(tested=tested, survivor_count=len(survivors), survivors=survivors)
    return dict(control_frames=[r["frame"] for r in controls],
        policy="same literal field across controls; numeric compatibility never establishes semantics",
        hypotheses=result, can_attribute_damage=False)


def field_inventory(rows):
    """Lossless value sets including overlaps, with no semantic promotion."""
    records = [bytes.fromhex(r["record_hex"]) for r in rows]
    return [dict(offset=offset, width=width,
        values=sorted({integer(r, offset, width) for r in records}))
        for width in (1, 2, 4) for offset in range(3, 119 - width)]


def bounded_context(capture, seed, samples, action_names):
    """Seek to the existing independently indexed birth; hard cap 1 MB/20 s.

    Frame numbers refer to physical capture frames, never diagnostic serials.
    Unframed messages are counted, not searched for attractive byte signatures.
    """
    birth = seed["birth"]
    start, stop = birth["file_offset"], max(r["file_offset"] for r in samples)
    require(0 <= stop - start <= 1_000_000, "raw context exceeds byte budget")
    require(max(r["time"] for r in samples) - birth["t"] <= 20, "raw context exceeds time budget")
    expected = {(r["file_offset"], r["message"]): r for r in samples}
    found, selected, states, all_results = set(), [], [], []
    counts, digest = Counter(), sha256()
    first_wire = None
    with Path(capture).open("rb") as stream:
        require(stream.read(6) == b"DNDP1\0", "bad capture magic")
        stream.seek(start)
        frame = birth["frame"] - 1
        while stream.tell() <= stop:
            offset = stream.tell()
            header = stream.read(17)
            require(len(header) == 17, "truncated frame header")
            time, adapter, original, size = struct.unpack("<dBII", header)
            require(0 < size <= 65536 and stream.tell() + size - start <= 1_000_000,
                    "invalid or over-budget frame size")
            raw = stream.read(size)
            require(len(raw) == size, "truncated frame")
            digest.update(header + raw)
            frame += 1
            counts["physical_frames"] += 1
            packet = protocol.decode_captured_frame(time, raw, original)
            if packet is None or packet.transport != "UDP":
                continue
            for message, wire in enumerate(protocol.split_v576_payload(packet.payload)):
                if offset == start and message == birth["message"]:
                    first_wire = wire
                    require(wire.hex() == birth["wire"] and time == birth["t"], "birth seed mismatch")
                    spawn = protocol.decode_v576_monster_spawn(wire, None)
                    require(spawn is not None and spawn.actor_id == ENTITY
                            and spawn.template_id == TEMPLATE, "birth template mismatch")
                key = offset, message
                direction = packet.inferred_direction()
                if key in expected:
                    s = expected[key]
                    require((frame, time, direction, wire.hex()) ==
                            (s["frame"], s["time"], s["direction"], s["wire"]), "capture sample mismatch")
                    found.add(key)
                counts["udp_messages"] += 1
                inventory = inspect_udp_damage_records(wire, direction)
                if not inventory.complete:
                    counts["unparsed:" + inventory.rejection_reason] += 1
                    continue
                relevant = []
                for r in inventory.typed_records:
                    b = r.record_bytes[4:]
                    if integer(r.record_bytes, 0) != ENTITY or r.record_type not in (3, 9):
                        continue
                    row = dict(frame=frame, message=message, marker=r.record_offset,
                               op=r.record_type, length=r.declared_length, record_hex=b.hex())
                    if r.record_type == 3 and r.declared_length == 20:
                        obs = read_compact_action(wire, r.record_offset, ENTITY, action_names,
                                                  direction=direction)
                        require(obs is not None, "bounded compact action failed validation")
                        row.update(action=obs.action_code, variant=obs.variant, kind=obs.kind,
                                   action_name_hypothesis=obs.act_index_hypothesis,
                                   can_attribute_damage=False)
                    elif r.record_type == 9 and r.declared_length >= 4:
                        row.update(object=integer(b, 3))
                        if r.declared_length in (41, 50):
                            row.update(action=integer(b, 34), signal=integer(b, 38))
                    states.append(row)
                    relevant.append(row)
                for h in inventory.damage_records:
                    if h.family == "direct37" and h.source_actor_id == ENTITY:
                        b = h.record_bytes[4:]
                        row = dict(frame=frame, message=message, marker=h.record_offset,
                            object=h.damage_object_id, result_group=h.damage_result_group_id,
                            m98_u16=integer(b, 98, 2), m102_u32=integer(b, 102))
                        all_results.append(row)
                        relevant.append(row)
                if relevant:
                    selected.append(dict(frame=frame, file_offset=offset, message=message,
                        time=time, direction=direction, phase=inventory.xor_phase,
                        wire_sha256=sha256(wire).hexdigest(), wire=wire.hex(), records=relevant))
        end = stream.tell()
    require(found == set(expected) and first_wire is not None, "missing indexed raw samples")
    target_results = [r for r in all_results if r["object"] == OBJECT]
    require({(r["frame"], r["message"], r["marker"]) for r in target_results} ==
            {(r["frame"], r["message"], 18) for r in samples}, "v33/context sample set differs")
    return dict(start=start, end_exclusive=end, bytes_read=end - start,
        window_sha256=digest.hexdigest(), start_frame=birth["frame"], end_frame=frame,
        start_time=birth["t"], stop_time=max(r["time"] for r in samples), counts=dict(counts),
        birth_wire_sha256=sha256(first_wire).hexdigest(), verified_samples=len(found),
        states=states, duck_results=all_results, selected_messages=selected,
        target_object_09_count=sum(r["op"] == 9 and r.get("object") == OBJECT for r in states),
        boundary="top-level typed records only; no claim that opaque messages or later frames lack evidence")


def audit():
    occurrence_path, diagnostic_path = INPUT / "duck_default_object_occurrences.json", INPUT / "0821硬核04.json"
    paths = [occurrence_path, diagnostic_path, SEED, CATALOG, Path(__file__),
        ROOT / "tools/probe_duck_default_object_v33.py",
        ROOT / "tools/probe_compact_entity_attack_selectors.py",
        ROOT / "tools/audit_remaining_lineage_five_jobs_act_transitions.py",
        ROOT / "tools/build_attack_instance_catalog.py",
        ROOT / "dragon_nest_builder/live_dps/compact_entity_object_evidence.py",
        ROOT / "dragon_nest_builder/live_dps/summon_self_attack_evidence_v26.py",
        ROOT / "dragon_nest_builder/live_dps/udp_damage_records.py",
        ROOT / "dragon_nest_builder/live_dps/protocol_v576.py"]
    hashes = {str(p): sha256(p.read_bytes()).hexdigest() for p in paths}
    occurrence = json.loads(occurrence_path.read_text("utf-8"))
    diagnostic = json.loads(diagnostic_path.read_text("utf-8"))
    require((occurrence["entity"], occurrence["object"]) == (ENTITY, OBJECT), "wrong target")
    require(diagnostic["capture"] == occurrence["capture"], "capture identity mismatch")
    catalog = json.loads(CATALOG.read_text("utf-8"))
    specs = specs_from_catalog(catalog)
    template = next(r for r in catalog["templates"] if r["template"] == TEMPLATE)
    act_path = Path(catalog["resource_root"]) / template["act"]
    hashes[str(act_path)] = sha256(act_path.read_bytes()).hexdigest()
    require(hashes[str(act_path)] == catalog["hashes"][template["act"]], "ACT certificate hash mismatch")
    actions = parse_full(act_path)["actions"]
    names = tuple(a["name"] for a in actions)
    require(list(names) == [a["name"] for a in template["action_indices"]], "ACT catalog order mismatch")
    rows = [inspect_sample(r) for r in occurrence["matches"]]
    for row in rows:
        spec = specs.get((TEMPLATE, row["m102_u32"]))
        row["existing_self_slot_static_match"] = (dict(monster_skill=spec.monster_skill_id,
            player_skill=spec.player_skill_id, proof_id=spec.proof_id, actions=list(spec.actions))
            if spec else None)
        value = row["m98_u16"]
        row["m98_as_zero_based_action_hypothesis"] = names[value] if value < len(names) else None
    seed_data = json.loads(SEED.read_text("utf-8"))
    seeds = [e for r in seed_data["rows"] if r["template"] == TEMPLATE for e in r["examples"]
             if e["entity"] == ENTITY and e["birth"] and e["birth"]["t"] <= min(s["time"] for s in rows)]
    require(bool(seeds), "missing indexed birth seed")
    seed = min(seeds, key=lambda e: e["birth"]["file_offset"])
    context = bounded_context(occurrence["capture"], seed, occurrence["matches"], names)
    missing = next(r for r in diagnostic["rows"] if r["reason"] == "registration_missing")
    for e in missing["examples"]:
        require(e["proof"]["birth_fingerprint"] == context["birth_wire_sha256"], "diagnostic birth mismatch")
        require(any(r["wire_sha256"] == e["proof"]["hit_wire_sha256"] and
                    r["time"] == e["time"] and r["marker"] == e["order"][2] for r in rows),
                "diagnostic example not in selected samples")
    static_proofs = [r for r in catalog["accepted"] if r["template"] == TEMPLATE
                     and r["monster_skill"] in (260408, 260608, 30809)]
    sweep = ordinal_sweep(rows, actions, specs)
    for p, digest in hashes.items():
        require(sha256(Path(p).read_bytes()).hexdigest() == digest, "input changed during audit: " + p)
    return dict(schema="duck-result-action-v34-read-only", capture=occurrence["capture"],
        capture_sha256_from_v33_not_recomputed=diagnostic["capture_sha256"],
        source_hashes=hashes, entity=ENTITY, object=OBJECT, template=TEMPLATE,
        anchor=occurrence["anchor"], diagnostic_scope=diagnostic["scope"],
        diagnostic_counts={r["reason"]: r["hits"] for r in diagnostic["rows"]},
        summary=dict(raw_records=len(rows), distinct_plain_result_records=len({r["record_sha256"] for r in rows}),
            first_delta=min(r["delta"] for r in rows), self_slots=dict(Counter(r["m102_u32"] for r in rows)),
            unresolved_sample_frames=[r["frame"] for r in rows if r["m102_u32"] == 0xffffffff],
            confirmed_increment=0, can_close_unresolved_result_to_own_ACT=False),
        samples=rows, field_inventory=field_inventory(rows), ordinal_sweep=sweep,
        static=dict(path=str(act_path), sha256=hashes[str(act_path)], action_count=len(actions),
            exact_existing_proofs=static_proofs,
            actions=[dict(index=i, name=a["name"], offset=a["offset"],
                typed_signals=[dict(index=j, offset=s["offset"], type=s["type"],
                    order=s["order"], start=s["start"], end=s["end"],
                    projectile=s["u32"][0] if s["type"] == 15 else None,
                    word18=s["u32"][18], word21=s["u32"][21])
                    for j, s in enumerate(a["signals"]) if s["type"] in (4, 15)])
                for i, a in enumerate(actions)]), raw_context=context,
        boundaries=[
            "137 confirmed / 111 registration_missing are inherited pre-aggregation diagnostic counts, not coverage gains or counts of this object's samples.",
            "14 physical observations include a repeated plaintext result; no new deduplication policy is inferred.",
            "The 11 positive self-slot observations are existing per-record evidence, not 11 new hits or upgrades.",
            "One object contains 260408, 260608 and FFFFFFFF: never propagate an earlier skill across the object.",
            "m114 creator label, nearby 03 action 48, damage amounts and ACT numeric parameter matches are not join evidence.",
            "m98 is sequence-like here; its counter semantics, reset/wrap rules and action-instance join are unproven.",
            "The finite ordinal sweep rejects one shared literal field, not bit-packed, transformed, conditional or unknown encodings.",
            "Raw context excludes opaque/non-top-level messages and stops at the final existing sample; global absence is not claimed.",
            "Birth/owner/generation and PVE admission remain mainline trust boundaries; this probe emits no annotations or hits."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    result = audit()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result["summary"] if args.summary else result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
