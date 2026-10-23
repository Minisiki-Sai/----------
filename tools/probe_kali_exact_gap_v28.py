"""Bounded, single-stream Kali raw-evidence probe; stdout only, no replay.

Use -B. No subprocesses, worker pools or output writes. Historical accepted
verification uses cached offsets. Current verification feeds only the 14 saved
fixture frames through production in-process, never opens a capture, and does
not measure accepted-report net gain. All inferred names are excluded from proofs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CAPTURE = Path(r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics\0817黑典噬魂.dpscap")
OUT = ROOT / "outputs/kali_exact_gap_v28_20260908"
OWNER = 1296954537
WINDOWS = ((159.0, 174.0), (1628.0, 1641.0))
TEMPLATES = {5508, 5520, 11075, 11076}
KNOWN = frozenset(range(5000, 5501))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def u32(body, at):
    return int.from_bytes(body[at:at + 4], "little")


def records(body):
    if len(body) < 21 or int.from_bytes(body[7:9], "little") + 3 != len(body):
        return []
    result = []
    cursor = 14
    while cursor < len(body):
        if cursor + 7 > len(body) or len(result) >= 64:
            return []
        m = cursor + 4
        n = int.from_bytes(body[m + 1:m + 3], "little")
        end = m + 3 + n
        if not 0 < u32(body, cursor) < 0xFFFFFFFF or n == 0 or end > len(body):
            return []
        result.append((m, body[m], n))
        cursor = end
    return result


def collect():
    from dragon_nest_builder.live_dps import protocol_v576 as p
    from dragon_nest_builder.live_dps.damage_object_v25_shaman_illusion import decode_signal_evidence

    inputs = [ROOT / "dragon_nest_builder/live_dps/protocol_v576.py",
              ROOT / "dragon_nest_builder/live_dps/damage_object_v25_shaman_illusion.py"]
    before = {str(x.relative_to(ROOT)): sha(x) for x in inputs}
    rows = []
    counts = Counter()
    tracked = {101868, 101916}  # Full IDs from cached 09 6f raw anchor; never truncated.
    with CAPTURE.open("rb") as f:
        assert f.read(6) == b"DNDP1\0"
        origin = None
        for frame_no in range(1, 300001):
            at = f.tell()
            head = f.read(17)
            if not head:
                break
            assert len(head) == 17
            t, adapter, original, size = struct.unpack("<dBII", head)
            assert size <= 1048576
            if origin is None:
                origin = t
            seconds = t - origin
            counts["headers_read"] += 1
            if seconds > WINDOWS[-1][1]:
                break
            if not any(a <= seconds <= b for a, b in WINDOWS):
                f.seek(size, 1)
                continue
            frame = f.read(size)
            assert len(frame) == size
            counts["frames_decoded_in_windows"] += 1
            pkt = p.decode_captured_frame(t, frame, original)
            if not pkt or pkt.transport.upper() != "UDP":
                continue
            direction = pkt.inferred_direction()
            for message, wire in enumerate(p.split_v576_payload(pkt.payload)):
                counts["window_udp_messages"] += 1
                if len(wire) > 16384:
                    raise RuntimeError("unexpected oversized message; stop rather than widen scan")
                plains = []
                raw_regs = []
                for phase in range(13):
                    b = p._decode_v576_body_uncached(wire, phase)
                    if OWNER.to_bytes(4, "little") not in b:
                        continue
                    rr = records(b)
                    for m, op, n in rr:
                        if op == 9 and n >= 93 and u32(b, m - 4) == OWNER:
                            obj, template = u32(b, m + 8), u32(b, m + 12)
                            if template in TEMPLATES or obj in tracked:
                                raw_regs.append(dict(m=m, size=n, owner=OWNER,
                                    repeated_owner=u32(b, m + 44), object=obj, template=template,
                                    phase=phase, parent_candidate=u32(b, m + 49),
                                    tail_hex=b[m + 44:m + 89].hex()))
                                tracked.add(obj)
                    plains.append(dict(phase=phase, decoded_hex=b.hex(), records=rr))
                current = list(p.decode_v576_server_damage_object_registrations(
                    wire, direction, (), include_unmapped=True))
                client = p.decode_v576_damage_object_registration(wire, direction)
                if client:
                    current.append(client)
                current = [r for r in current if r.source_actor_id == OWNER
                           and (r.projectile_template_id in TEMPLATES or r.damage_object_id in tracked)]
                tracked.update(r.damage_object_id for r in current)
                actions = [a for a in p.decode_v576_structured_actions(wire, direction, KNOWN, {OWNER})
                           if a.source_actor_id == OWNER]
                hits = [h for h in p.decode_v576_embedded_direct_damage_events(wire, None, KNOWN)
                        if h.source_actor_id == OWNER and h.damage_object_id in tracked]
                roots, signal_regs = decode_signal_evidence(wire, direction)
                if not (raw_regs or current or actions or hits):
                    continue
                rows.append(dict(frame=frame_no, file_offset=at, timestamp=t,
                    seconds=round(seconds, 6), adapter=adapter, original_length=original,
                    frame_sha256=hashlib.sha256(frame).hexdigest(), frame_hex=frame.hex(),
                    message=message, wire_hex=wire.hex(), direction=direction, plains=plains,
                    raw_registrations=raw_regs, current_registrations=[asdict(r) for r in current],
                    actions=[asdict(a) for a in actions], hits=[asdict(h) for h in hits],
                    existing_signal_roots=[asdict(r) for r in roots],
                    existing_signal_registrations=[asdict(r) for r in signal_regs]))
                if len(rows) > 512:
                    raise RuntimeError("evidence limit reached; stop rather than allocate unbounded output")
    after = {str(x.relative_to(ROOT)): sha(x) for x in inputs}
    return dict(capture=str(CAPTURE), capture_sha256_from_cached_audit=
                "59cb400b6443513d128eb6a755f191f0816ea030f1892fa83574dba7192d01e6",
                owner=OWNER, origin=origin, windows=WINDOWS, counts=dict(counts),
                production_hashes_before=before, production_hashes_after=after,
                production_unchanged_during_probe=before == after, rows=rows)


def candidate_child(payload, direction):
    """Proposal only: exact observed 152-byte relay envelope, no global gate.

    M+69 is coordinates here! The extended scalar is M+82. Neither scalar
    nor the bytes in between are treated as a root/cast-instance identifier.
    """
    from dragon_nest_builder.live_dps import protocol_v576 as p
    if (direction != "client_to_server" or len(payload) != 152
            or payload[5:9] != bytes.fromhex("ce6c9500")):
        return None
    candidates = []
    for phase in range(13):
        b = p._decode_v576_body_uncached(payload, phase)
        if records(b) != [(18, 0x46, 13), (38, 9, 111)]:
            continue
        m = 38
        owner, obj, parent = u32(b, m - 4), u32(b, m + 8), u32(b, m + 49)
        branch, scalar = u32(b, m + 53), u32(b, m + 82)
        if (not 0 < owner < 0xFFFFFFFF or owner != u32(b, m + 44)
                or owner != u32(b, 10) or owner != u32(b, 14)
                or b[m + 3:m + 8] != bytes(5)
                or u32(b, m + 12) != 11076
                or not 0 < obj <= 0xFFFFFF or not 0 < parent <= 0xFFFFFF
                or parent == obj or b[m + 48] != 0
                or branch != (66 << 16 | 3)
                or b[m + 57:m + 65] != b"\xff" * 8
                or u32(b, m + 74) != 0xFFFFFFFF
                or not 0 < scalar <= 0xFFFF):
            continue
        candidates.append(p.DecodedV576DamageObjectRegistration(
            source_actor_id=owner, damage_object_id=obj, projectile_template_id=11076,
            effect_resource_id=scalar, child_sequence=66, xor_phase=phase,
            layout="proposal-kali-parent-096f-152-v28", record_offset=m,
            parent_qualified_subtype=True, parent_damage_object_id=parent,
            requires_root_action_proof=True, registration_key=b[m - 4:].hex()))
    return candidates[0] if len(candidates) == 1 and candidates[0].xor_phase == 0 else None


def anchored_actions(evidence):
    """Seek only cached exact offsets; no second capture stream scan."""
    from dragon_nest_builder.live_dps import protocol_v576 as p
    cached = json.loads((ROOT / "outputs/damage_attribution/audit_remaining_lineage_five_jobs_raw_anchors.json").read_text(encoding="utf-8"))
    cache = next(x for x in cached if x["tag"] == "souleater")
    parent = next(x for x in evidence["rows"] if x["frame"] == 190510)
    # The existing cached complete action window starts child_time-4 and
    # contains ALL of this stricter two-second root attachment interval.
    child = next(x for x in evidence["rows"] if x["frame"] == 191012)
    lo, hi = parent["timestamp"] - 2.0, child["timestamp"]
    assert lo >= child["timestamp"] - 4.0
    selected = {x["file_offset"]: x for x in cache["actions"]
                if lo <= x["timestamp"] <= hi and x["message"]["source_actor_id"] == OWNER}
    result = []
    with CAPTURE.open("rb") as f:
        for at, cached_row in sorted(selected.items()):
            f.seek(at)
            t, adapter, original, size = struct.unpack("<dBII", f.read(17))
            frame = f.read(size)
            assert t == cached_row["timestamp"] and len(frame) == size
            pkt = p.decode_captured_frame(t, frame, original)
            assert pkt is not None
            wire = pkt.payload
            if len(wire) == 45 and wire[5:9] == bytes.fromhex("87062a00"):
                b = p._decode_v576_body_uncached(wire, 6)
                owner, skill = u32(b, 10), u32(b, 14)
                assert owner == OWNER and pkt.inferred_direction() == "client_to_server"
                phase = 6
            else:
                actions = p.decode_v576_structured_actions(wire, pkt.inferred_direction(), KNOWN, {OWNER})
                identities = {(a.source_actor_id, a.skill_id) for a in actions}
                assert len(identities) == 1
                owner, skill = identities.pop()
                phase = 3
                b = p._decode_v576_body_uncached(wire, phase)
            assert skill == cached_row["message"]["skill_id"]
            result.append(dict(frame=cached_row["frame_index"], file_offset=at,
                timestamp=t, owner=owner, skill=skill, direction=pkt.inferred_direction(),
                phase=phase, frame_hex=frame.hex(), frame_sha256=hashlib.sha256(frame).hexdigest(),
                wire_hex=wire.hex(), decoded_hex=b.hex()))
    return result, dict(start=lo, end=parent["timestamp"], seconds=2.0,
        cache_coverage_start=child["timestamp"] - 4.0,
        cache_source="outputs/damage_attribution/audit_remaining_lineage_five_jobs_raw_anchors.json",
        cache_sha256=sha(ROOT / "outputs/damage_attribution/audit_remaining_lineage_five_jobs_raw_anchors.json"),
        meaning="unique independently observed compatible root; no claim of a wire cast-instance nonce")


def static_evidence():
    from tools.build_dungeon_map_catalog import parse_dnt
    from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full
    resource = Path(r"E:\ext0811.rar\Resource00\Resource00\resource")
    hashes = {}
    def table(name, predicate):
        path = resource / "ext" / name
        hashes[str(path.relative_to(resource))] = sha(path)
        return [r for r in parse_dnt(path) if predicate(r)]
    actor = table("actortable.dnt", lambda r: r["RowId"] == 6)[0]
    projectiles = table("weapontable_projectile.dnt", lambda r: r["RowId"] in (11075, 11076))
    files = table("filetable.dnt", lambda r: r["RowId"] in (actor["_ActName"], 11284, 11285))
    skills = table("skilltable_character.dnt", lambda r: r["RowId"] in (5217, 5406))
    levels = table("skillleveltable_characterkali.dnt", lambda r: r.get("_SkillIndex") in (5217, 5406) and r.get("_SkillLevel") == 1)
    manifest = resource / "char/player/kali/kali.act"
    hashes[str(manifest.relative_to(resource))] = sha(manifest)
    prefix = []
    base = 0
    for filename in manifest.read_text(encoding="utf-8-sig").splitlines():
        path = resource / "char/player/kali" / filename.strip()
        if not filename.strip():
            continue
        count = struct.unpack_from("<I", path.read_bytes(), 36)[0]
        hashes[str(path.relative_to(resource))] = sha(path)
        prefix.append(dict(file=str(path.relative_to(resource)), base=base, count=count))
        if path.name.casefold() == "kali_skill_2st_souleater.act":
            actions = parse_full(path)["actions"]
            ordinal = 224 - base
            a = actions[ordinal]
            s = a["signals"][16]
            assert a["name"] == "Skill_BeastSpirit_EX" and s["type"] == 15 and s["u32"][0] == 11075
            root_signal = dict(action_ordinal=224, local_index=ordinal, action_offset=a["offset"],
                signal_index=16, signal_offset=s["offset"], type=15, template=11075,
                resource_hex=path.read_bytes()[s["offset"]:s["offset"] + 32].hex())
            break
        base += count
    parent_act = resource / "weapon/kali_beastspirit_ex_pve.act"
    hashes[str(parent_act.relative_to(resource))] = sha(parent_act)
    a = parse_full(parent_act)["actions"][3]
    child_signals = []
    for index in (66, 67):
        s = a["signals"][index]
        assert s["type"] == 15 and s["u32"][0] == 11076
        child_signals.append(dict(action_index=3, action_offset=a["offset"], signal_index=index,
            signal_offset=s["offset"], type=15, template=11076,
            resource_hex=parent_act.read_bytes()[s["offset"]:s["offset"] + 32].hex()))
    assert next(r for r in skills if r["RowId"] == 5406)["_BaseSkillID"] == 5217
    assert any(r["_SkillIndex"] == 5406 and r["_ProcessParam1"] == "Skill_BeastSpirit_EX" for r in levels)
    return dict(resource_root=str(resource), hashes=hashes, actor_row=actor, file_rows=files,
        projectile_rows=[{k:r[k] for k in ("RowId", "_ActName")} for r in projectiles],
        skill_rows=[{k:r[k] for k in ("RowId", "_BaseSkillID")} for r in skills],
        skilllevel_rows=[{k:r[k] for k in ("RowId", "_SkillIndex", "_SkillLevel", "_ProcessParam1")} for r in levels],
        actor_manifest_prefix=prefix, parent_registration_signal=root_signal,
        parent_to_child_signals=child_signals)


def candidate_parent_witness(ledger, reg, timestamp, *, frozen=None):
    """Required supplemental guard, scoped ONLY to this candidate family.

    A parser-only insertion is insufficient: the generic ledger need not
    reject every unknown-parent observation after a child was registered.
    Keep the exact parent generation and independent epoch in this witness.
    """
    key = (reg.source_actor_id, reg.parent_damage_object_id)
    parent = ledger._origins.get(key)
    if (parent is None or parent.projectile_template_id != 11075
            or parent.owner_actor_id != reg.source_actor_id
            or not parent.registered_at <= timestamp <= parent.expires_at
            or (parent.effect_resource_id, parent.child_sequence) != (224, 16)
            or parent.parent_backed or not parent.action_backed
            or key in ledger._unknown_registration_invalidations
            or (*key, parent.generation) in ledger._revoked_origin_generations):
        return None
    roots = [a for a in ledger._actions if a.owner_actor_id == reg.source_actor_id
             and a.raw_skill_id in (5217, 5406)
             and parent.registered_at - 2.0 <= a.observed_at <= parent.registered_at]
    if len(roots) != 1 or roots[0].action_epoch_id != parent.action_epoch_id:
        return None
    witness = (parent.generation, parent.action_epoch_id, parent.registered_at, roots[0].observed_at)
    if frozen is not None and witness != frozen:
        return None
    return witness


def verify():
    from dragon_nest_builder.live_dps import protocol_v576 as p
    from dragon_nest_builder.live_dps.damage_object_origins import DamageObjectOriginLedger
    proof_modules = {"dragon_nest_builder.live_dps." + x for x in
                     ("protocol_v576", "damage_object_origins", "models", "generated_attack_instance_specs_v576")}
    loaded_start = {name: dict(path=str(Path(module.__file__).resolve()), sha256=sha(Path(module.__file__)))
                    for name, module in list(sys.modules.items())
                    if name in proof_modules}
    evidence = json.loads((OUT / "window_raw_evidence.json").read_text(encoding="utf-8"))
    byframe = {r["frame"]: r for r in evidence["rows"]}
    parent, child = byframe[190510], byframe[191012]
    sibling = byframe[191013]
    actions, root_window = anchored_actions(evidence)
    compatible = [r for r in actions if r["skill"] in (5217, 5406)
                  and root_window["start"] <= r["timestamp"] <= root_window["end"]]
    assert len(compatible) == 1
    root = compatible[0]
    candidate = candidate_child(bytes.fromhex(child["wire_hex"]), child["direction"])
    assert candidate and candidate.parent_damage_object_id == 101868
    parent_reg = p.decode_v576_damage_object_registration(bytes.fromhex(parent["wire_hex"]), parent["direction"])
    assert parent_reg and (parent_reg.source_actor_id, parent_reg.projectile_template_id,
        parent_reg.effect_resource_id, parent_reg.child_sequence) == (OWNER, 11075, 224, 16)
    assert parent["timestamp"] - root["timestamp"] <= 2.0
    current_child = p.decode_v576_server_damage_object_registrations(bytes.fromhex(child["wire_hex"]),
        child["direction"], (), include_unmapped=True)
    assert not current_child and p.decode_v576_damage_object_registration(bytes.fromhex(child["wire_hex"]), child["direction"]) is None
    hits = [(r, h) for r in evidence["rows"] for h in r["hits"] if h["damage_object_id"] == 101916]
    key_fields = ("source_actor_id", "target_actor_id", "damage_object_id", "damage_result_group_id", "damage", "critical", "record_offset")
    for row, h in hits:
        parsed = p.decode_v576_embedded_direct_damage_events(bytes.fromhex(row["wire_hex"]), row["direction"], KNOWN)
        assert sum(all(getattr(x, k) == h[k] for k in key_fields) for x in parsed) == 1
    assert len({tuple(h[k] for k in key_fields[:-1]) for _, h in hits}) == len(hits)

    def observe(ledger, reg, row, **extra):
        return ledger.observe_registration(source_actor_id=reg.source_actor_id,
            damage_object_id=reg.damage_object_id, projectile_template_id=reg.projectile_template_id,
            effect_resource_id=reg.effect_resource_id, child_sequence=reg.child_sequence,
            timestamp=row["timestamp"], packet_serial=row["frame"], record_offset=reg.record_offset,
            parent_qualified_subtype=reg.parent_qualified_subtype,
            parent_damage_object_id=reg.parent_damage_object_id, registration_key=reg.registration_key,
            requires_root_action_proof=reg.requires_root_action_proof, **extra)

    def run(mode="positive", *, supplemental_guard=True):
        from dataclasses import replace
        ledger = DamageObjectOriginLedger()
        if mode != "no_root":
            ledger.observe_action(OWNER + (1 if mode == "wrong_root_owner" else 0),
                                  5203 if mode == "wrong_root_skill" else root["skill"], root["timestamp"])
        if mode == "two_distinct_compatible_roots":
            ledger.observe_action(OWNER, root["skill"], root["timestamp"] + .25)
        if mode != "no_parent":
            pr = replace(parent_reg, source_actor_id=OWNER + 1) if mode == "wrong_parent_owner" else parent_reg
            observe(ledger, pr, parent)
        cr = candidate
        if mode == "parent_high16_collision":
            cr = replace(cr, parent_damage_object_id=cr.parent_damage_object_id + 65536)
        if mode == "self_parent":
            cr = replace(cr, parent_damage_object_id=cr.damage_object_id)
        if mode == "child_high16_collision":
            cr = replace(cr, damage_object_id=cr.damage_object_id + 65536)
        witness = candidate_parent_witness(ledger, cr, child["timestamp"])
        if mode != "baseline" and (not supplemental_guard or witness is not None):
            observe(ledger, cr, child, state_observation=True)
        if mode == "invalidate_parent":
            changed = bytearray(p._decode_v576_body_uncached(bytes.fromhex(parent["wire_hex"]), 12))
            changed[23:27] = (999999).to_bytes(4, "little")
            unknown = p.decode_v576_damage_object_registration(p._decode_v576_body_uncached(bytes(changed), 12), "client_to_server")
            assert unknown and unknown.projectile_template_id == 999999
            observe(ledger, unknown,
                    {**child, "timestamp": child["timestamp"] + .01, "frame": child["frame"] + 1})
        if mode == "invalidate_child":
            ledger.invalidate_origin(source_actor_id=OWNER, damage_object_id=cr.damage_object_id,
                timestamp=child["timestamp"] + .01, packet_serial=child["frame"] + 1, record_offset=38)
        if mode == "parent_reuse":
            ledger.observe_action(OWNER, root["skill"], child["timestamp"] + .005)
            observe(ledger, replace(parent_reg, registration_key=parent_reg.registration_key + "00"),
                {**child, "timestamp": child["timestamp"] + .01, "frame": child["frame"] + 1})
        if mode == "echo_no_renewal":
            original = ledger._origins[(OWNER, cr.damage_object_id)]
            identity = (original.generation, original.registered_at, original.expires_at,
                        original.parent_generation, original.parent_root_action_epoch_id)
            observe(ledger, replace(cr, registration_key=cr.registration_key + "00"),
                {**child, "timestamp": child["timestamp"] + .4, "frame": child["frame"] + 1}, state_observation=True)
            current = ledger._origins[(OWNER, cr.damage_object_id)]
            assert identity == (current.generation, current.registered_at, current.expires_at,
                                current.parent_generation, current.parent_root_action_epoch_id)
        if mode == "reset":
            ledger.reset()
        result = []
        for row, h in hits:
            timestamp = parent["timestamp"] + 9 if mode in ("expired_parent", "echo_no_renewal") else row["timestamp"]
            a = ledger.attribute_direct37(source_actor_id=OWNER, timestamp=timestamp,
                layout=h["layout"], payload=bytes.fromhex(row["wire_hex"]), direction=row["direction"],
                damage=h["damage"], record_offset=h["record_offset"], packet_serial=row["frame"])
            guard = (not supplemental_guard or witness is not None and
                     candidate_parent_witness(ledger, cr, timestamp, frozen=witness) is not None)
            result.append(dict(frame=row["frame"], deterministic=bool(a and a.deterministically_validated and guard),
                               attribution=asdict(a) if a else None))
        return result

    baseline, positive = run("baseline"), run()
    assert all(not x["deterministic"] for x in baseline)
    assert positive and all(x["deterministic"] for x in positive)
    negatives = {}
    for mode in ("no_root", "wrong_root_owner", "wrong_root_skill", "no_parent", "wrong_parent_owner",
                 "parent_high16_collision", "self_parent", "invalidate_parent", "expired_parent", "reset",
                 "two_distinct_compatible_roots", "child_high16_collision", "invalidate_child", "parent_reuse",
                 "echo_no_renewal"):
        rows = run(mode)
        negatives[mode] = all(not x["deterministic"] for x in rows)
        assert negatives[mode], mode
    unguarded_invalidation = run("invalidate_parent", supplemental_guard=False)
    wire = bytes.fromhex(child["wire_hex"])
    b = bytearray(p._decode_v576_body_uncached(wire, 0))
    mutations = {
        "repeat_owner": (82, (OWNER + 1).to_bytes(4, "little")),
        "parent_high_byte": (87, (101868 + 0x1000000).to_bytes(4, "little")),
        "parent_zero": (87, bytes(4)), "self_parent": (87, (101916).to_bytes(4, "little")),
        "guard": (86, b"\x01"), "wrong_template": (50, (5520).to_bytes(4, "little")),
        "wrong_branch": (91, b"\x01\x00"), "wrong_signal": (93, b"\x41\x00"),
        "sentinel": (95, b"\x00"), "reference": (112, bytes(4)),
        "scalar_zero": (120, bytes(4)), "companion_size": (19, b"\x0c\x00"),
        "outer_length": (7, b"\x94\x00"), "unproved_scalar_at_69": (120, b"\x00\x00\x01\x00"),
    }
    format_negatives = {}
    for name, (at, value) in mutations.items():
        changed = bytearray(b)
        changed[at:at + len(value)] = value
        encoded = p._decode_v576_body_uncached(bytes(changed), 0)
        format_negatives[name] = candidate_child(encoded, "client_to_server") is None
        assert format_negatives[name], name
    format_negatives["truncated"] = candidate_child(wire[:-1], "client_to_server") is None
    format_negatives["appended"] = candidate_child(wire + b"\x00", "client_to_server") is None
    format_negatives["unknown_direction"] = candidate_child(wire, None) is None
    format_negatives["unobserved_server_direction"] = candidate_child(wire, "server_to_client") is None
    assert all(format_negatives.values())
    sibling_reg = p.decode_v576_damage_object_registration(bytes.fromhex(sibling["wire_hex"]), sibling["direction"])
    assert sibling_reg and sibling_reg.parent_damage_object_id == 101868 and sibling_reg.damage_object_id == 101917
    # Re-open saved physical offsets and verify bytes, without a stream replay.
    with CAPTURE.open("rb") as f:
        for row in evidence["rows"]:
            f.seek(row["file_offset"])
            t, adapter, original, size = struct.unpack("<dBII", f.read(17))
            frame = f.read(size)
            assert t == row["timestamp"] and hashlib.sha256(frame).hexdigest() == row["frame_sha256"]
            assert frame.hex() == row["frame_hex"]
    resource_evidence = static_evidence()
    accepted = json.loads((ROOT / "outputs/compact_entity_selectors_20260908/after_identity36/profession_direct_damage_report.json").read_text(encoding="utf-8"))
    signature = accepted["analysis_signature"]["files"]
    for name, item in loaded_start.items():
        assert sha(Path(item["path"])) == item["sha256"]
        rel = name.replace(".", "/") + ".py"
        item["accepted_hash"] = signature.get(rel)
        item["matches_accepted"] = item["accepted_hash"] == item["sha256"] if item["accepted_hash"] else None
        if "/rake_frozen_runtime/before/" in Path(item["path"]).as_posix():
            assert item["matches_accepted"], name
    return dict(candidate_registration=asdict(candidate), parent_registration=asdict(parent_reg),
        sibling_control=asdict(sibling_reg), actions=actions, root_attachment_window=root_window,
        unique_root_frame=root["frame"], static_evidence=resource_evidence,
        loaded_modules=loaded_start, loaded_files_unchanged=True,
        accepted_hit_parser_matches_saved_keys=True, raw_keys_unique_in_window=True,
        accepted_report_sha256=sha(ROOT / "outputs/compact_entity_selectors_20260908/after_identity36/profession_direct_damage_report.json"),
        accepted_analysis_signature=accepted["analysis_signature"]["sha256"],
        raw_hit_keys=[dict(frame=r["frame"], file_offset=r["file_offset"], timestamp=r["timestamp"],
            owner=h["source_actor_id"], target=h["target_actor_id"], object=h["damage_object_id"],
            result_group=h["damage_result_group_id"], amount=h["damage"], critical=h["critical"],
            marker=h["record_offset"], skill_slot=h["skill_id"], skill_slot_exact=h["skill_id_exact"])
            for r,h in hits], baseline_ledger=baseline, candidate_ledger=positive,
        lifecycle_negatives=negatives, format_negatives=format_negatives,
        parser_only_parent_invalidation_counterexample=unguarded_invalidation,
        validation_scope="raw parser + small origin-ledger harness only, no CombatEvent/aggregator/replay",
        accepted_report_net_gain=None, accepted_report_named_denominator=24551,
        raw_hits=len(hits), raw_damage=sum(h["damage"] for _,h in hits),
        notes=["59 saved physical frames revalidated at exact offsets; no second stream scan",
               "Most recently displayed skill names and v12 classifications never supply truth",
               "2s unique-root guard at birth and frozen full-parent witness checks at Hit are mandatory; parser-only insertion is insufficient",
               "No wider 096f/0973 gate; repeated state must not renew a generation"])


def verify_current():
    """Fixture-only production integration check; one small in-process stream."""
    from dragon_nest_builder.live_dps import protocol_v576 as p
    from dragon_nest_builder.live_dps.models import CombatEvent
    from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
    fixture = ROOT / "tests/data/kali_11076_parent_v28.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert len(data["packets"]) == 14
    decoder = _V576PacketEventDecoder(ROOT / "dragon_nest_builder/data", {})
    result = []
    for row in data["packets"]:
        frame = bytes.fromhex(row["frame_hex"])
        assert hashlib.sha256(frame).hexdigest() == row["frame_sha256"]
        packet = p.decode_captured_frame(row["timestamp"], frame, row.get("original_length", len(frame)))
        assert packet and packet.payload.hex() == row["wire_hex"]
        for event in decoder.feed_events(packet):
            if (isinstance(event, CombatEvent) and not event.healing
                    and event.wire_source_actor_id == data["owner"]
                    and event.wire_damage_object_id == 101916):
                result.append(dict(owner=event.wire_source_actor_id, target=event.wire_target_actor_id,
                    object=event.wire_damage_object_id, result_group=event.wire_result_group_id,
                    amount=event.amount, critical=event.critical, skill_id=event.skill_id,
                    method=event.attribution_method, real_hit=event.has_real_hit_evidence))
    fields = ("owner", "target", "object", "result_group", "amount", "critical")
    assert [tuple(row[f] for f in fields) for row in result] == [
        tuple(row[f] for f in fields) for row in data["expected_hits"]]
    assert all(r["skill_id"] == "5217" and r["method"] == "damage_object_projectile_link"
               and r["real_hit"] for r in result)
    origin = decoder.damage_object_origins._origins[data["owner"], 101916]
    assert origin.kali_11076_parent_witness is not None
    return dict(scope="14 fixture frames through current production; no capture replay",
                fixture_sha256=sha(fixture), raw_candidate_hits=len(result),
                raw_damage=sum(r["amount"] for r in result), result_keys_preserved=True,
                parent_witness=asdict(origin.kali_11076_parent_witness),
                accepted_report_net_gain=None, events=result)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["collect", "verify"])
    ap.add_argument("--runtime", choices=["accepted", "current"], default="accepted")
    args = ap.parse_args()
    if args.mode == "verify" and args.runtime == "accepted":
        frozen = ROOT / "outputs/compact_entity_selectors_20260908/rake_frozen_runtime/before"
        sys.path.insert(0, str(frozen))
    result = collect() if args.mode == "collect" else verify_current() if args.runtime == "current" else verify()
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
