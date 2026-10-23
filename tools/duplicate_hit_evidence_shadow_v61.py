"""Isolated 110114 duplicate-Hit evidence experiment; never writes production.

--replay: ONE normal diagnostic prefix (frames 1..358534), JSON bundle on stdout.
--test: tiny frozen fixture, no capture replay, JSON test results on stdout.
No ReplaySource.poll(), duplicate feed_events(), aggregator, EOF backfill, or
global monkeypatch. The only duplicate decoder-ledger query is attribute_direct37.
_evidence_only=True is checked against ordinary forward lookup on a COPY.
The emitted replacement is a proposal; it does not alter the decoder event list.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses as dc
from decimal import Decimal
import hashlib
import inspect
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "outputs/duplicate_hit_evidence_v61_20260909"
REPORT = ROOT / "outputs/late_registration_v60_20260909/REPORT.json"
CAPTURE = Path("C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/0821黑典女王.dpscap")
CAPTURE_SHA = "40be2e0bc2cf58cb3ba3dec707cfc119fc18bef29ebb2967b8779f20402c5d17"
OWNER, OBJECT, AMOUNT = 1314843433, 110114, 62504
KEY = (OWNER, OBJECT)
ORIGINAL, REGISTRATION, DUPLICATE = 358513, 358521, 358534
HEADER = struct.Struct("<dBII")

from dragon_nest_builder.live_dps import sources as src
from dragon_nest_builder.live_dps import protocol_v576 as proto
from dragon_nest_builder.live_dps.damage_object_origins import (
    DamageObjectOriginLedger, DamageObjectOrigin, DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
)
from dragon_nest_builder.live_dps.damage_attribution import attribution_rule
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger, v576_wire_event_id
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plain(value):
    if callable(value):
        return {"callable": value.__module__ + "." + value.__qualname__}
    if dc.is_dataclass(value):
        return plain(dc.asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return [plain(v) for v in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, bytes):
        return value.hex()
    return value


def state_sha(ledger):
    return sha(json.dumps(plain(ledger.__dict__), sort_keys=True).encode())


def flow(packet):
    return [packet.transport, packet.source_ip, packet.source_port,
            packet.destination_ip, packet.destination_port,
            packet.tcp_connection_generation]


def unpack_packet(row):
    p = proto.decode_captured_frame(float(row["timestamp_epoch_seconds"]),
                                   bytes.fromhex(row["raw_frame_hex"]),
                                   row["original_length"])
    require(p is not None, "fixture frame must decode")
    require(p.payload.hex() == row["wire_hex"], "fixture raw frame / wire mismatch")
    return p


def physical(event):
    return (event.wire_record_offset, event.wire_source_actor_id,
            event.wire_target_actor_id, event.amount, event.critical,
            event.wire_result_group_id, event.wire_damage_object_id)


def record(packet, offset=96):
    inv = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
    require(inv.complete, "complete independent typed inventory required")
    rows = [r for r in inv.damage_records if r.record_offset == offset]
    require(len(rows) == 1, "unique typed record at exact offset required")
    return rows[0]


def record_key(packet):
    r = record(packet)
    return dict(record_start=r.record_start, record_offset=r.record_offset,
                record_end=r.record_end, declared_length=r.declared_length,
                family=r.family, xor_phase=r.xor_phase,
                physical_key=list(r.physical_key), raw_skill_slots=plain(r.raw_skill_slots),
                plaintext_record_sha256=r.wire_record_sha256,
                plaintext_record_hex=r.record_bytes.hex())


def packet_key(row, packet):
    return dict(capture_sha256=CAPTURE_SHA, frame_1based=row["frame"],
                trace_file_offset=row["trace_file_offset"],
                timestamp_double_le_hex=row["timestamp_double_le_hex"],
                timestamp_exact_decimal=row["timestamp_exact_decimal"],
                adapter=row["adapter"], raw_frame_sha256=row["frame_sha256"],
                captured_length=row["captured_length"],
                original_length=row["original_length"],
                connection_key_with_generation=flow(packet),
                direction=packet.inferred_direction(), message_index=row["message_index"],
                wire_event_id=v576_wire_event_id(packet.payload),
                wire_sha256=sha(packet.payload))


def ledger_context(ledger):
    return dict(evidence_epoch=ledger.evidence_epoch,
                origin=plain(ledger._origins.get(KEY)),
                generation_counter=ledger._generation_counters.get(KEY, 0),
                invalidation=plain(ledger._unknown_registration_invalidations.get(KEY)),
                revoked=[plain((k, v)) for k, v in ledger._revoked_origin_generations.items()
                         if k[:2] == KEY],
                packet_history=plain(ledger._packet_origin_history.get(KEY, [])),
                prior_parent_identity=plain(ledger._last_parent_state_identities.get(KEY)),
                history_gap_until=ledger._parent_state_history_gap_until)


def lookup(ledger, packet, event, serial, evidence_only=True):
    return ledger.attribute_direct37(
        source_actor_id=event.wire_source_actor_id, timestamp=packet.timestamp_seconds,
        layout=event.wire_damage_layout, payload=packet.payload,
        direction=packet.inferred_direction(), damage=event.amount,
        record_offset=event.wire_record_offset, packet_serial=serial,
        _evidence_only=evidence_only)  # _post_admitted_late stays FALSE.


def suggest(ledger, fixture, *, original_packet=None, duplicate_packet=None, event=None):
    """Conservative experiment, not the Main integration module.

    Birth/tombstone checks run at the DUPLICATE boundary. No lookup is backdated.
    Earlier unobserved generations remain a capture-limit caveat, not wire proof.
    """
    original_packet = original_packet or unpack_packet(fixture["packets"]["original"])
    duplicate_packet = duplicate_packet or unpack_packet(fixture["packets"]["duplicate"])
    event = event or CombatEvent(**fixture["original_event"])
    admission, history = fixture["admission"], fixture["history"]
    reject = lambda reason: {"eligible": False, "reason": reason}
    if (not admission["original_accepted"] or admission["row_matches"] != 1
            or not admission["original_row_still_present"]):
        return reject("original_not_uniquely_admitted")
    if not admission["duplicate_rejected"]:
        return reject("duplicate_not_rejected_by_event_ledger")
    if (original_packet.transport != "UDP" or flow(original_packet) != flow(duplicate_packet)
            or original_packet.inferred_direction() != "server_to_client"
            or duplicate_packet.inferred_direction() != "server_to_client"):
        return reject("connection_or_direction_mismatch")
    if original_packet.payload != duplicate_packet.payload:
        return reject("not_byte_identical_message")
    elapsed = duplicate_packet.timestamp_seconds-original_packet.timestamp_seconds
    if not 0 <= elapsed <= 3.0:
        return reject("outside_nonrefreshing_retry_window")
    if not history["continuous_prefix"] or history["pending_evicted"]:
        return reject("history_unknown_or_evicted")
    if ledger.evidence_epoch != history["at_original"]["evidence_epoch"]:
        return reject("epoch_changed")
    old = history["at_original"]
    if (old["generation_counter"] or old["origin"] or old["invalidation"]
            or old["revoked"] or old["prior_parent_identity"]
            or history["target_seen_before_original"]):
        return reject("prior_generation_or_tombstone")
    rule = attribution_rule(event.attribution_method)
    if (event.healing or event.amount <= 0 or event.has_real_hit_evidence is not True
            or rule.deterministic or rule.locked or event.wire_entity_id
            or event.source_id != f"0x{OWNER:08X}"
            or event.target_id.partition("@")[0] != f"0x{event.wire_target_actor_id:08X}"):
        return reject("ineligible_or_stronger_original")
    try:
        r = record(duplicate_packet, event.wire_record_offset)
    except AssertionError:
        return reject("incomplete_or_ambiguous_record")
    if (not r.admissible_fields or r.family != "direct37"
            or r.physical_key != physical(event)
            or event.wire_critical != r.critical
            or record_key(original_packet) != fixture["original_record_key"]):
        return reject("record_key_mismatch")
    if any(v not in (0, 0xffffffff) for _, v in r.raw_skill_slots):
        return reject("native_truth_present")
    origin = ledger._origins.get(KEY)
    if origin is None:
        return reject("no_current_origin")
    if (origin.generation != 1 or ledger._generation_counters.get(KEY) != 1
            or origin.owner_actor_id != OWNER or origin.damage_object_id != OBJECT):
        return reject("generation_or_owner_reuse")
    if KEY in ledger._unknown_registration_invalidations:
        return reject("unknown_registration_tombstone")
    if (*KEY, origin.generation) in ledger._revoked_origin_generations:
        return reject("revoked_generation")
    if ledger._parent_state_history_gap_until > 0:
        return reject("ledger_history_gap")
    if (origin.parent_damage_object_id or origin.parent_proof_dependencies
            or origin.requires_root_action_proof or origin.parent_qualified_subtype
            or origin.action_qualified_subtype):
        return reject("parent_or_action_chain_outside_scope")
    if (origin.registered_at < original_packet.timestamp_seconds
            or origin.registered_packet_serial <= admission["original_decoder_serial"]
            or (origin.registered_at, origin.registered_packet_serial,
                origin.registered_record_offset, origin.projectile_template_id)
            != tuple(fixture["birth_witness"])):
        return reject("birth_witness_mismatch")
    snapshot = copy.deepcopy(ledger.__dict__)
    proof = lookup(ledger, duplicate_packet, event, admission["proof_packet_serial"])
    require(ledger.__dict__ == snapshot, "evidence-only query mutated actual origin ledger")
    if (proof is None or not proof.deterministically_validated
            or not (proof.client_act_validated or proof.packet_validated)):
        return reject("forward_attribute_direct37_rejected")
    if fixture["job_conflict"] or str(proof.skill_id) not in fixture["skill_names"]:
        return reject("catalog_or_job_conflict")
    # This audit intentionally proves metadata enrichment, not skill relabelling.
    if event.skill_id != str(proof.skill_id):
        return reject("different_skill_outside_scope")
    changes = dict(skill_id=str(proof.skill_id),
                   skill_name=fixture["skill_names"][str(proof.skill_id)],
                   attribution_method="damage_object_projectile_link",
                   attribution_confidence=proof.confidence)
    suggested = dc.replace(event, **changes)
    return dict(eligible=True, reason="exact_duplicate_forward_existing_object",
                proof=plain(proof), deterministically_validated=proof.deterministically_validated,
                original_packet_key=fixture["original_packet_key"],
                original_record_key=fixture["original_record_key"],
                duplicate_packet_key=fixture["duplicate_packet_key"],
                duplicate_record_key=record_key(duplicate_packet),
                admitted_event_index_0based=admission["event_index"],
                original_event=plain(event), suggested_event=plain(suggested),
                changed_fields={k: [getattr(event, k), v] for k, v in changes.items()
                                if getattr(event, k) != v},
                apply_semantics="compare exact original event at exact packet+record key, replace once; never append",
                original_hit_timestamp_lookup_bypassed=False,
                proof_evaluated_at_duplicate=True, new_rows=0, new_damage=0,
                new_skill_labels=0, proposal_only=True)


def freeze_ledger(ledger):
    context = ledger_context(ledger)
    require(not context["packet_history"], "fixture minimization requires no target snapshots")
    origin = ledger._origins[KEY]
    require(not origin.parent_proof_dependencies and not origin.kali_11076_parent_witness,
            "minimal fixture cannot omit parent dependencies")
    return dict(target=context,
                latest_observed_timestamp=ledger._latest_observed_timestamp,
                history_packet_serial=ledger._history_packet_serial,
                minimization="real replay target projection; no synthetic registration/action, no omitted target dependencies")


def thaw_ledger(frozen):
    ledger = DamageObjectOriginLedger()
    target = frozen["target"]
    ledger.evidence_epoch = target["evidence_epoch"]
    o = dict(target["origin"])
    o["parent_proof_dependencies"] = frozenset(tuple(x) for x in o["parent_proof_dependencies"])
    ledger._origins[KEY] = DamageObjectOrigin(**o)
    ledger._generation_counters[KEY] = target["generation_counter"]
    if target["invalidation"]:
        ledger._unknown_registration_invalidations[KEY] = tuple(target["invalidation"])
    for k, v in target["revoked"]:
        ledger._revoked_origin_generations[tuple(k)] = tuple(v)
    if target["prior_parent_identity"]:
        ledger._last_parent_state_identities[KEY] = tuple(target["prior_parent_identity"])
    ledger._parent_state_history_gap_until = target["history_gap_until"]
    ledger._latest_observed_timestamp = frozen["latest_observed_timestamp"]
    ledger._history_packet_serial = frozen["history_packet_serial"]
    return ledger


NEGATIVES = {
    "not_admitted": "original_not_uniquely_admitted",
    "ambiguous_row": "original_not_uniquely_admitted",
    "row_replaced": "original_not_uniquely_admitted",
    "accepted_new_message": "duplicate_not_rejected_by_event_ledger",
    "other_connection": "connection_or_direction_mismatch",
    "connection_generation": "connection_or_direction_mismatch",
    "one_changed_byte": "not_byte_identical_message",
    "retry_too_late": "outside_nonrefreshing_retry_window",
    "retry_before_original": "outside_nonrefreshing_retry_window",
    "history_unknown": "history_unknown_or_evicted",
    "pending_evicted": "history_unknown_or_evicted",
    "reset_then_same_generation": "epoch_changed",
    "prior_generation": "prior_generation_or_tombstone",
    "prior_tombstone": "prior_generation_or_tombstone",
    "strong_packet_truth": "ineligible_or_stronger_original",
    "not_real_hit": "ineligible_or_stronger_original",
    "record_offset": "incomplete_or_ambiguous_record",
    "record_hash": "record_key_mismatch",
    "wire_critical_mismatch": "record_key_mismatch",
    "no_origin": "no_current_origin",
    "reused_generation": "generation_or_owner_reuse",
    "unknown_replacement": "unknown_registration_tombstone",
    "revoked_only": "revoked_generation",
    "ledger_history_gap": "ledger_history_gap",
    "parent_required": "parent_or_action_chain_outside_scope",
    "root_action_required": "parent_or_action_chain_outside_scope",
    "unwitnessed_birth": "birth_witness_mismatch",
    "expired": "forward_attribute_direct37_rejected",
    "born_after_duplicate": "forward_attribute_direct37_rejected",
    "same_packet_before_birth_offset": "forward_attribute_direct37_rejected",
    "job_conflict": "catalog_or_job_conflict",
    "skill_change": "different_skill_outside_scope",
}


def fixture_tests(fixture):
    passed = []
    op = unpack_packet(fixture["packets"]["original"])
    dp = unpack_packet(fixture["packets"]["duplicate"])
    event = CombatEvent(**fixture["original_event"])
    ledger = thaw_ledger(fixture["ledger_before_duplicate"])
    for role, row in fixture["packets"].items():
        p = unpack_packet(row)
        require(sha(bytes.fromhex(row["raw_frame_hex"])) == row["frame_sha256"], role+" raw hash")
        require(struct.pack("<d", p.timestamp_seconds).hex() == row["timestamp_double_le_hex"],
                role+" exact timestamp")
    require(op.payload == dp.payload, "exact original and duplicate bytes")
    passed.append("three_raw_packet_fixtures")
    inv = inspect_udp_damage_records(op.payload, op.inferred_direction())
    require(len(inv.damage_records) == 1 and inv.damage_records[0].physical_key == physical(event),
            "one physical damage record in the entire exact message")
    passed.append("unique_physical_damage_row_in_entire_packet")
    registration = unpack_packet(fixture["packets"]["registration"])
    require(proto.decode_v576_damage_object_registration(
        registration.payload, registration.inferred_direction()) is None, "not local-registration branch")
    require(not proto.decode_v576_server_damage_object_registrations(
        registration.payload, registration.inferred_direction()), "empty default template-list contract")
    regs = proto.decode_v576_server_damage_object_registrations(
        registration.payload, registration.inferred_direction(),
        DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS, include_unmapped=True)
    require(len(regs) == 1 and (regs[0].source_actor_id, regs[0].damage_object_id,
            regs[0].projectile_template_id, regs[0].record_offset) == (OWNER, OBJECT, 63, 18),
            "exact production server-registration arguments")
    passed.append("production_registration_arguments_not_empty_defaults")
    before = copy.deepcopy(ledger.__dict__)
    positive = suggest(ledger, fixture)
    require(positive["eligible"] and ledger.__dict__ == before, "positive immutable proof")
    if "expected_actual_proof" in fixture:
        require(positive["proof"] == fixture["expected_actual_proof"], "minimized fixture / actual ledger proof parity")
    for _ in range(3):
        require(suggest(ledger, fixture) == positive and ledger.__dict__ == before,
                "evidence retries must not refresh lifetime or hit count")
    passed.append("positive_read_only_repeatable")
    ordinary = lookup(copy.deepcopy(ledger), dp, event, fixture["admission"]["proof_packet_serial"], False)
    require(plain(ordinary) == positive["proof"], "normal forward query parity")
    passed.append("ordinary_forward_parity_on_copy")
    independent = copy.deepcopy(ledger)
    independent._origins[KEY].action_backed = False
    independent._origins[KEY].response_backed = False
    independent._origins[KEY].action_epoch_id = 0
    no_timing = lookup(independent, dp, event, fixture["admission"]["proof_packet_serial"])
    require(no_timing is not None and no_timing.skill_id == 3019
            and no_timing.client_act_validated and no_timing.deterministically_validated,
            "template63 proof must not depend on a recent action")
    passed.append("client_template_proof_without_recent_action_flags")
    require(lookup(ledger, op, event, fixture["admission"]["original_decoder_serial"]) is None,
            "normal birth gate must reject original timestamp")
    passed.append("original_timestamp_birth_gate")
    replaced = [event]
    replaced[0] = CombatEvent(**positive["suggested_event"])
    preserve = set(dc.asdict(event)) - set(positive["changed_fields"])
    require(len(replaced) == 1 and all(getattr(event, k) == getattr(replaced[0], k)
                                      for k in preserve), "replace metadata only")
    passed.append("one_row_metadata_replacement")

    for name, expected in fixture["negative_cases"].items():
        f, l = copy.deepcopy(fixture), thaw_ledger(fixture["ledger_before_duplicate"])
        e, d = event, dp
        a, h, o = f["admission"], f["history"], l._origins[KEY]
        if name == "not_admitted": a["original_accepted"] = False
        elif name == "ambiguous_row": a["row_matches"] = 2
        elif name == "row_replaced": a["original_row_still_present"] = False
        elif name == "accepted_new_message": a["duplicate_rejected"] = False
        elif name == "other_connection": d = dc.replace(d, destination_port=d.destination_port+1)
        elif name == "connection_generation": d = dc.replace(d, tcp_connection_generation=1+(d.tcp_connection_generation or 0))
        elif name == "one_changed_byte": d = dc.replace(d, payload=d.payload[:-1]+bytes([d.payload[-1]^1]))
        elif name == "retry_too_late": d = dc.replace(d, timestamp_seconds=op.timestamp_seconds+3.01)
        elif name == "retry_before_original": d = dc.replace(d, timestamp_seconds=op.timestamp_seconds-.001)
        elif name == "history_unknown": h["continuous_prefix"] = False
        elif name == "pending_evicted": h["pending_evicted"] = True
        elif name == "reset_then_same_generation":
            l.reset()
            l._origins[KEY] = o
            l._generation_counters[KEY] = 1
        elif name == "prior_generation": h["at_original"]["generation_counter"] = 1
        elif name == "prior_tombstone": h["at_original"]["invalidation"] = [op.timestamp_seconds-1, 1, 18]
        elif name == "strong_packet_truth": e = dc.replace(e, attribution_method="packet_skill_id")
        elif name == "not_real_hit": e = dc.replace(e, has_real_hit_evidence=False)
        elif name == "record_offset": e = dc.replace(e, wire_record_offset=e.wire_record_offset+1)
        elif name == "record_hash": f["original_record_key"]["plaintext_record_sha256"] = "0"*64
        elif name == "wire_critical_mismatch": e = dc.replace(e, wire_critical=False)
        elif name == "no_origin": l._origins.clear()
        elif name == "reused_generation":
            o.generation = 2
            l._generation_counters[KEY] = 2
        elif name == "unknown_replacement":
            l.observe_registration(source_actor_id=OWNER, damage_object_id=OBJECT,
                projectile_template_id=0x7ffffffe, effect_resource_id=0, child_sequence=0,
                timestamp=dp.timestamp_seconds-.001, packet_serial=a["proof_packet_serial"]-1,
                record_offset=18, state_observation=True)
        elif name == "revoked_only":
            l._revoked_origin_generations[(*KEY, 1)] = (dp.timestamp_seconds-.001, a["proof_packet_serial"]-1, 18)
        elif name == "ledger_history_gap": l._parent_state_history_gap_until = dp.timestamp_seconds+1
        elif name == "parent_required": o.parent_qualified_subtype = True
        elif name == "root_action_required": o.requires_root_action_proof = True
        elif name == "unwitnessed_birth": o.registered_record_offset += 1
        elif name == "expired": o.expires_at = dp.timestamp_seconds-.000001
        elif name == "born_after_duplicate":
            o.registered_at = dp.timestamp_seconds+.001
            f["birth_witness"][0] = o.registered_at
        elif name == "same_packet_before_birth_offset":
            o.registered_packet_serial = a["proof_packet_serial"]
            o.registered_record_offset = event.wire_record_offset+1
            f["birth_witness"][1:3] = [o.registered_packet_serial, o.registered_record_offset]
        elif name == "job_conflict": f["job_conflict"] = True
        elif name == "skill_change": e = dc.replace(e, skill_id="3402")
        else: raise AssertionError("unknown negative "+name)
        before = copy.deepcopy(l.__dict__)
        result = suggest(l, f, duplicate_packet=d, event=e)
        require(not result["eligible"] and result["reason"] == expected,
                f"{name}: expected {expected}, got {result}")
        require(l.__dict__ == before, name+" evidence mutated ledger")
        passed.append(name)

    # Directly test the production boundary gates, not only outer conservatism.
    for name, mutate in (
        ("native_tombstone_gate", "unknown"),
        ("native_revoked_generation_gate", "revoked"),
    ):
        l = thaw_ledger(fixture["ledger_before_duplicate"])
        boundary = (dp.timestamp_seconds-.001, fixture["admission"]["proof_packet_serial"]-1, 18)
        if mutate == "unknown": l._unknown_registration_invalidations[KEY] = boundary
        else: l._revoked_origin_generations[(*KEY, 1)] = boundary
        before = copy.deepcopy(l.__dict__)
        require(lookup(l, dp, event, fixture["admission"]["proof_packet_serial"]) is None,
                name+" must reject")
        require(l.__dict__ == before, name+" must not mutate")
        passed.append(name)
    return dict(passed=len(passed), failed=0, cases=passed, replay_processes=0)


def source_manifest():
    files = [Path(__file__)]
    files.extend(sorted((ROOT / "dragon_nest_builder/live_dps").rglob("*.py")))
    # Pin data files actually used by the decoder without loading them for tests.
    files.extend(sorted((ROOT / "dragon_nest_builder/data").glob("*.csv")))
    return {str(p.relative_to(ROOT)): digest(p) for p in files}


def read_evidence(capture):
    previous = json.loads(REPORT.read_text(encoding="utf-8-sig"))
    packets = {}
    with capture.open("rb") as f:
        for role, frame in (("original", ORIGINAL), ("registration", REGISTRATION), ("duplicate", DUPLICATE)):
            row = copy.deepcopy(next(p for p in previous["packets"] if p["frame"] == frame))
            f.seek(row["trace_file_offset"])
            header = f.read(HEADER.size)
            timestamp, adapter, original, length = HEADER.unpack(header)
            raw = f.read(length)
            require(sha(raw) == row["frame_sha256"] and header[:8].hex() == row["timestamp_double_le_hex"],
                    role+" raw capture mismatch with v60 report")
            require((adapter, original, length) == (row["adapter"], row["original_length"], row["captured_length"]),
                    role+" header mismatch")
            row["raw_frame_hex"] = raw.hex()
            packets[role] = row
    return packets


def replay(capture, max_seconds):
    started = time.monotonic()
    require(digest(capture) == CAPTURE_SHA, "0821 capture hash pin failed")
    require("_evidence_only" in inspect.signature(DamageObjectOriginLedger.attribute_direct37).parameters,
            "Main read-only attribute_direct37 interface required")
    inputs_before = source_manifest()
    packets = read_evidence(capture)
    source = src.V576DiagnosticReplaySource(capture, ROOT / "dragon_nest_builder/data", {},
                                           action_parser_mode="structured")
    decoder = source._decoder
    transport, identity_transport = proto.V576TransportReassembler(), proto.V576TransportReassembler()
    admission_ledger = V576EventLedger(mode="event_id")
    events, original_rows, target_transitions = [], [], []
    prior_context = ledger_context(decoder.damage_object_origins)
    seen_before_original = False
    original_history = None
    stats = dict(frames=0, logical_packets=0, feed_events_calls=0,
                 duplicate_feed_events_calls=0, identity_packets=0, session_evidence_packets=0)
    original_serial = None
    birth_witness = None
    registration_proof = None
    last_progress = started
    done = False
    with capture.open("rb") as f:
        require(f.read(6) == b"DNDP1\0", "bad trace magic")
        for frame_index in range(1, DUPLICATE+1):
            require(time.monotonic()-started < max_seconds, "self-exit replay deadline")
            file_offset = f.tell()
            header = f.read(HEADER.size)
            require(len(header) == HEADER.size, "truncated prefix header")
            timestamp, adapter, original_length, captured_length = HEADER.unpack(header)
            require(captured_length <= 16*1024*1024, "abnormal frame length")
            raw = f.read(captured_length)
            require(len(raw) == captured_length, "truncated prefix frame")
            stats["frames"] = frame_index
            if time.monotonic()-last_progress >= 20:
                print(f"prefix {frame_index}/{DUPLICATE}; {time.monotonic()-started:.1f}s", file=sys.stderr, flush=True)
                last_progress = time.monotonic()
            if adapter == src.IDENTITY_METADATA_ADAPTER_INDEX:
                decoder.known_actor_names.update(src.decode_identity_trace_bindings(raw))
                continue
            packet = proto.decode_captured_frame(timestamp, raw, original_length)
            if packet is None:
                continue
            if adapter == src.SESSION_EVIDENCE_ADAPTER_INDEX:
                events.extend(src._verified_session_evidence_events(decoder, packet))
                stats["session_evidence_packets"] += 1
                continue
            if adapter == src.IDENTITY_TRACE_ADAPTER_INDEX:
                for logical in identity_transport.feed(packet):
                    decoder._observe_tcp_player_spawn_identity(logical)
                    stats["identity_packets"] += 1
                continue
            for message_index, logical in enumerate(transport.feed(packet)):
                stats["logical_packets"] += 1
                ledger = decoder.damage_object_origins
                if frame_index in (ORIGINAL, REGISTRATION, DUPLICATE):
                    role = {ORIGINAL: "original", REGISTRATION: "registration", DUPLICATE: "duplicate"}[frame_index]
                    row = packets[role]
                    require(file_offset == row["trace_file_offset"] and message_index == 0
                            and sha(raw) == row["frame_sha256"] and logical.payload.hex() == row["wire_hex"],
                            "normal transport must reach exact capture witness")
                if frame_index == ORIGINAL:
                    original_history = ledger_context(ledger)
                before_stats = plain(admission_ledger.stats)
                accepted = admission_ledger.accept(logical)
                if frame_index == DUPLICATE:
                    require(not accepted and len(original_rows) == 1, "actual original admission / duplicate rejection")
                    row_index, original_event = original_rows[0]
                    require(events[row_index] is original_event, "original row changed before duplicate")
                    require(birth_witness is not None, "real registration required")
                    fixture = dict(schema="duplicate-hit-evidence-v61-fixture",
                        capture_sha256=CAPTURE_SHA, packets=packets,
                        original_event=plain(original_event),
                        original_packet_key=packet_key(packets["original"], unpack_packet(packets["original"])),
                        duplicate_packet_key=packet_key(packets["duplicate"], logical),
                        original_record_key=record_key(logical),
                        history=dict(continuous_prefix=True, pending_evicted=False,
                                     at_original=original_history,
                                     target_seen_before_original=seen_before_original),
                        admission=dict(original_accepted=True, row_matches=len(original_rows),
                            original_row_still_present=True, duplicate_rejected=True,
                            event_index=row_index, original_decoder_serial=original_serial,
                            proof_packet_serial=decoder._damage_object_packet_serial+1),
                        birth_witness=birth_witness,
                        ledger_before_duplicate=freeze_ledger(ledger),
                        skill_names={str(k): v[0] for k, v in decoder.skill_catalog.items() if k == 3019},
                        job_conflict=decoder._action_conflicts_with_confirmed_job(OWNER, 3019),
                        negative_cases=NEGATIVES)
                    # Always retain the real normal-prefix witness even if a
                    # later audit assertion fails. This checkpoint is stdout-
                    # independent and permits --test without another replay.
                    print("V61_FIXTURE=" + json.dumps(fixture, ensure_ascii=True),
                          file=sys.stderr, flush=True)
                    events_before = tuple(events)
                    before_state, serial_before = state_sha(ledger), decoder._damage_object_packet_serial
                    suggestion = suggest(ledger, fixture)
                    require(suggestion["eligible"], f"110114 proposal rejected: {suggestion}")
                    fixture["expected_actual_proof"] = suggestion["proof"]
                    require(tuple(events) == events_before and all(a is b for a, b in zip(events, events_before)),
                            "normal event list was modified")
                    require(before_state == state_sha(ledger) and serial_before == decoder._damage_object_packet_serial,
                            "duplicate must not mutate decoder ledger or serial")
                    counts = dict(admitted_events=len(events),
                        combat_rows=sum(isinstance(e, CombatEvent) for e in events),
                        nonhealing_damage=sum(e.amount for e in events if isinstance(e, CombatEvent) and not e.healing),
                        real_hit_rows=sum(isinstance(e, CombatEvent) and not e.healing and e.has_real_hit_evidence is True for e in events))
                    shadow = list(events)
                    shadow[row_index] = CombatEvent(**suggestion["suggested_event"])
                    require(len(shadow) == len(events)
                            and sum(e.amount for e in shadow if isinstance(e, CombatEvent) and not e.healing) == counts["nonhealing_damage"],
                            "metadata replacement changed row count or damage")
                    output = dict(schema="duplicate-hit-evidence-v61-report",
                        capture=str(capture), capture_sha256=CAPTURE_SHA,
                        previous_report_sha256=digest(REPORT), fixture=fixture,
                        replay_statistics=stats, normal_prefix_counts=counts,
                        shadow_counts=dict(counts), original_match_count=1,
                        duplicate_event_ledger_before=before_stats,
                        duplicate_event_ledger_after=plain(admission_ledger.stats),
                        original_decoder_serial=original_serial,
                        decoder_serial_before_and_after_duplicate=[serial_before, decoder._damage_object_packet_serial],
                        actual_origin_ledger_sha256_before_and_after=[before_state, state_sha(ledger)],
                        registration_evidence=registration_proof,
                        target_state_transitions=target_transitions,
                        replacement_proposals=[suggestion],
                        validation=None,
                        excluded="No duplicate feed, ReplaySource.poll, EOF flush/backfill, aggregator, or production integration.",
                        metric_delta=dict(new_rows=0, new_damage=0, new_skill_labels=0,
                            proposed_evidence_upgrades=1, applied_production_upgrades=0,
                            measured_842_gap_reduction=0),
                        lifecycle_limit="Capture-local first observed generation, not a wire generation token. Unknown/evicted history, reset, reuse, tombstone, expiry, or stronger original evidence must refuse enrichment.")
                    done = True
                    break
                if accepted:
                    start = len(events)
                    new = decoder.feed_events(logical)
                    stats["feed_events_calls"] += 1
                    events.extend(new)
                    if frame_index == ORIGINAL:
                        original_serial = decoder._damage_object_packet_serial
                        r = record(logical)
                        original_rows = [(start+i, e) for i, e in enumerate(new)
                                         if isinstance(e, CombatEvent) and physical(e) == r.physical_key]
                        require(len(original_rows) == 1, "normal decoder must admit exact original once")
                    if frame_index == REGISTRATION:
                        regs = [r for r in proto.decode_v576_server_damage_object_registrations(
                            logical.payload, logical.inferred_direction(), include_unmapped=True)
                            if r.source_actor_id == OWNER and r.damage_object_id == OBJECT]
                        require(len(regs) == 1, "one exact real registration")
                        origin = ledger._origins[KEY]
                        birth_witness = [origin.registered_at, origin.registered_packet_serial,
                                         origin.registered_record_offset, origin.projectile_template_id]
                        registration_proof = dict(packet_key=packet_key(packets["registration"], logical),
                                                  decoded_registration=plain(regs[0]), origin=plain(origin))
                context = ledger_context(ledger)
                # Audit-only full-prefix memory: unlike bounded production maps,
                # a previously seen target remains seen even after pruning/reset.
                if frame_index < ORIGINAL and any((context["origin"], context["generation_counter"],
                                                   context["invalidation"], context["revoked"])):
                    seen_before_original = True
                if context != prior_context and frame_index >= ORIGINAL:
                    target_transitions.append(dict(frame=frame_index, accepted=accepted,
                                                   decoder_serial=decoder._damage_object_packet_serial, context=context))
                prior_context = context
            if done:
                break
    require(done, "did not reach duplicate")
    source.close()
    inputs_after = source_manifest()
    output["input_manifest"] = inputs_before
    output["input_files_changed_during_run"] = [p for p, h in inputs_before.items() if inputs_after.get(p) != h]
    output["elapsed_seconds"] = round(time.monotonic()-started, 3)
    output["self_exit"] = dict(stop_frame=DUPLICATE, deadline_seconds=max_seconds, spawned_replay_children=0)
    try:
        output["validation"] = fixture_tests(fixture)
    except Exception as exc:
        # Preserve the real prefix fixture if an independent negative fails;
        # fix/rerun --test only, never spend a second full replay for test edits.
        output["validation"] = {"failed": 1, "error": repr(exc)}
    return output


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--replay", action="store_true")
    group.add_argument("--test", action="store_true")
    parser.add_argument("--capture", type=Path, default=CAPTURE)
    parser.add_argument("--fixture", type=Path, default=OUT / "fixture_110114.json")
    parser.add_argument("--max-seconds", type=int, default=600)
    args = parser.parse_args()
    if args.test:
        output = fixture_tests(json.loads(args.fixture.read_text(encoding="utf-8-sig")))
    else:
        require(1 <= args.max_seconds <= 900, "bounded replay deadline 1..900 seconds")
        # Same process watchdog; no background replay/helper process can survive.
        timer = threading.Timer(args.max_seconds, lambda: os._exit(124))
        timer.daemon = True
        timer.start()
        try:
            output = replay(args.capture, args.max_seconds)
        finally:
            timer.cancel()
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
