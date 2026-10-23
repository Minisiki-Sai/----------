"""Read-only sequential replay audit. No scoring, cache writes or uploads.

Census covers supported decoded damage candidates, NOT every opaque UDP byte.
Unemitted candidates are retained as unresolved audit items, never new damage.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import gzip
import json
from pathlib import Path
import sys
import time


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class Totals:
    def __init__(self):
        self.count = self.amount = 0
        self.stream = hashlib.sha256()
        self.owner_skill_neutral = hashlib.sha256()
        self.players = defaultdict(lambda: {"hits": 0, "amount": 0, "name": "", "job": ""})
        self.classes = Counter()

    def add(self, events, event_type):
        rows = []
        for e in events:
            if not isinstance(e, event_type):
                continue
            category = ("healing" if e.healing else "incoming_hp_delta" if e.source_id == "__incoming_hp_delta__"
                        else "incoming_damage" if e.source_id == "__incoming_damage__" else "outgoing")
            self.classes[category + ":hits"] += 1
            self.classes[category + ":amount"] += e.amount
            self.count += 1
            self.amount += e.amount
            # Ignore added protocol metadata, but retain actual skill/owner,
            # target, timing, damage and hit qualification in the invariants.
            rows.append((e.timestamp_ms, e.source_id, e.target_id, e.amount,
                         bool(e.critical), bool(e.healing), e.skill_id, e.has_real_hit_evidence))
            if category == "outgoing":
                p = self.players[e.source_id]
                p["hits"] += 1
                p["amount"] += e.amount
                p["name"], p["job"] = e.source_name, e.job
        for row in sorted(rows, key=encode):
            self.stream.update(encode(row) + b"\n")
        neutral = [(r[0], r[2], r[3], r[4], r[5], r[7]) for r in rows]
        for row in sorted(neutral, key=encode):
            self.owner_skill_neutral.update(encode(row) + b"\n")

    def result(self):
        return {"count": self.count, "amount_including_incoming_and_healing": self.amount,
                "physical_skill_stream_sha256": self.stream.hexdigest(),
                "owner_skill_neutral_sha256": self.owner_skill_neutral.hexdigest(),
                "categories": dict(self.classes), "players": dict(self.players)}


def event_wire_key(e):
    return (e.wire_record_offset, e.wire_source_actor_id, e.wire_target_actor_id,
            e.amount, e.wire_critical, e.wire_result_group_id, e.wire_damage_object_id)


def decoded_wire_key(d):
    return (int(d.record_offset or 0), int(d.source_actor_id or 0), int(d.target_actor_id or 0),
            int(d.damage or 0), d.critical, int(d.damage_result_group_id or 0), int(d.damage_object_id or 0))


def run(snapshot, capture, output, event_details=False, wire_inventory=False):
    snapshot, capture, output = snapshot.resolve(), capture.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    def verify_snapshot():
        for rel, expected in manifest["files"].items():
            if digest_file(snapshot / rel) != expected:
                raise RuntimeError(f"Snapshot changed: {rel}")
    verify_snapshot()
    sys.path.insert(0, str(snapshot))
    from dragon_nest_builder.live_dps.models import CombatEvent
    from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
    from dragon_nest_builder.live_dps.aggregator import CombatAggregator
    from dragon_nest_builder.live_dps.event_ledger import V576EventLedger
    from dragon_nest_builder.live_dps.protocol_v576 import decode_v576_embedded_direct_damage_events
    if wire_inventory:
        from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
    try:
        from dragon_nest_builder.live_dps.protocol_v576 import decode_v576_embedded_direct41_damage_events
    except ImportError:
        decode_v576_embedded_direct41_damage_events = lambda *args: ()
    capture_hash = digest_file(capture)
    started = time.monotonic()
    detail_path = output.with_suffix(".events.jsonl.gz")
    detail_stream = None
    if event_details:
        output.parent.mkdir(parents=True, exist_ok=True)
        detail_stream = gzip.open(detail_path, "xb")
    def write_events(stage, events, message=0, packet=None):
        if detail_stream is None:
            return
        from dataclasses import asdict
        for event in events:
            if isinstance(event, CombatEvent):
                detail_stream.write(encode({"stage": stage, "message": message, "event": asdict(event),
                    "wire": packet.payload.hex() if packet is not None else ""}) + b"\n")
    source = V576DiagnosticReplaySource(capture, snapshot / "dragon_nest_builder/data", {})
    decoder = source._decoder
    original = decoder.feed_events
    raw, normalized, aggregate = Totals(), Totals(), Totals()
    counters, ownership, layouts = Counter(), Counter(), Counter()
    physical_inventory = Counter()
    opaque_records = Counter()
    inventory_stream_hash = hashlib.sha256()
    examples = defaultdict(list)
    last_progress = started
    has_provenance = hasattr(CombatEvent, "wire_source_actor_id")
    transport_counts = Counter()
    original_accept = V576EventLedger.accept
    def observe_accept(ledger, packet):
        accepted = original_accept(ledger, packet)
        transport_counts[packet.transport.upper() + (":accepted" if accepted else ":duplicate")] += 1
        return accepted
    V576EventLedger.accept = observe_accept
    owner_refinements = Counter()
    owner_changes = []
    original_finalize = getattr(decoder, "_finalize_wire_damage_owner", None)
    if original_finalize is not None:
        def observe_finalize(event, packet):
            updated = original_finalize(event, packet)
            if event.entity_owner_evidence_id != updated.entity_owner_evidence_id:
                owner_refinements["new_owner_certificate_hits"] += 1
                owner_refinements["new_owner_certificate_amount"] += event.amount
            if event.source_id != updated.source_id:
                from dataclasses import asdict
                owner_refinements["corrected_owner_hits"] += 1
                owner_refinements["corrected_owner_amount"] += event.amount
                owner_changes.append({"message": counters["messages"], "time": packet.timestamp_seconds,
                    "before": asdict(event), "after": asdict(updated), "wire": packet.payload.hex()})
            return updated
        decoder._finalize_wire_damage_owner = observe_finalize
    def feed(packet):
        nonlocal last_progress
        counters["messages"] += 1
        events = original(packet)
        raw.add(events, CombatEvent)
        write_events("raw_decoder", events, counters["messages"], packet)
        if packet.transport.upper() == "UDP":
            counters["udp_messages"] += 1
            if wire_inventory:
                direction = packet.inferred_direction()
                if direction != "server_to_client":
                    physical_inventory["excluded_direction:" + str(direction)] += 1
                else:
                    inventory = inspect_udp_damage_records(packet.payload, direction)
                    physical_inventory["messages"] += 1
                    if not inventory.complete:
                        physical_inventory["unverified_message:" + inventory.rejection_reason] += 1
                    else:
                        physical_inventory["complete_typed_messages"] += 1
                        accepted = Counter()
                        for event in events:
                            if not isinstance(event, CombatEvent) or event.healing or not event.wire_record_offset:
                                continue
                            key = event_wire_key(event)
                            family = "direct41" if "direct41" in event.wire_damage_layout else "direct37"
                            accepted[(family, *key[:5], (key[5] or 0) if family == "direct37" else None,
                                      (key[6] or 0) if family == "direct37" else None)] += 1
                        for record in inventory.typed_records:
                            if record.record_type != 8 or record.declared_length not in (0x73, 0x77):
                                opaque_records[f"{record.record_type:02x}/{record.declared_length:04x}"] += 1
                        for row in inventory.damage_records:
                            physical_inventory[row.family + ":records"] += 1
                            key = row.physical_key
                            match_key = (row.family, *key[:5], key[5] if row.family == "direct37" else None,
                                         key[6] if row.family == "direct37" else None)
                            if accepted[match_key]:
                                accepted[match_key] -= 1
                                disposition = "matched_emitted_physical_record"
                            else:
                                disposition = "not_matched_emission" if row.admissible_fields else "invalid_fields"
                            physical_inventory[disposition + ":records"] += 1
                            physical_inventory[disposition + ":amount"] += row.damage
                            inventory_stream_hash.update(encode((counters["messages"], row.family, key,
                                                                 disposition, row.rejection_reasons)) + b"\n")
                            if disposition != "matched_emitted_physical_record" and len(examples["physical_" + disposition]) < 30:
                                examples["physical_" + disposition].append({
                                    "message": counters["messages"], "key": key, "family": row.family,
                                    "wire_sha256": inventory.wire_sha256, "wire": packet.payload.hex(),
                                    "rejection_reasons": row.rejection_reasons,
                                    "source_template": getattr(decoder.monster_entities.get(row.source_actor_id), "template_id", None),
                                    "target_template": getattr(decoder.monster_entities.get(row.target_actor_id), "template_id", None),
                                })
            if has_provenance:
                emitted = Counter()
                for e in events:
                    if not isinstance(e, CombatEvent) or e.healing:
                        continue
                    layouts[e.wire_damage_layout or "no_wire_damage_record"] += 1
                    if e.wire_damage_layout:
                        emitted[event_wire_key(e)] += 1
                    if e.source_id.startswith("__"):
                        continue
                    try:
                        owner_id = int(e.source_id.split("@")[0], 16)
                    except ValueError:
                        owner_id = 0
                    kind = ("exact_entity_owner" if e.entity_owner_evidence_id
                            else "wire_player_source" if owner_id == e.wire_source_actor_id and owner_id > 65535
                            else "legacy_or_unproved_owner")
                    ownership[kind + ":hits"] += 1
                    ownership[kind + ":amount"] += e.amount
                # This is a production-supported candidate inventory, not an
                # independent proof of unknown packet formats or packet loss.
                candidates = decode_v576_embedded_direct_damage_events(
                    packet.payload, packet.inferred_direction(), decoder.damage_skill_ids) + decode_v576_embedded_direct41_damage_events(
                    packet.payload, packet.inferred_direction(), decoder.damage_skill_ids)
                for d in candidates:
                    counters["embedded_candidates"] += 1
                    key = decoded_wire_key(d)
                    if emitted[key]:
                        emitted[key] -= 1
                        reason = "matched_emitted_record"
                    elif d.target_actor_id == d.source_actor_id:
                        reason = "self_target_filtered_candidate"
                    elif d.target_actor_id not in decoder.monster_entities and d.target_actor_id not in decoder.confirmed_damage_target_ids:
                        reason = "target_unconfirmed_candidate"
                    else:
                        reason = "unmatched_supported_candidate"
                    counters[reason] += 1
                    if reason != "matched_emitted_record" and len(examples[reason]) < 20:
                        from dataclasses import asdict
                        primary = decoder.last_decoded_message
                        examples[reason].append({"message": counters["messages"], "time": packet.timestamp_seconds,
                            "key": key, "layout": d.layout, "wire_sha256": hashlib.sha256(packet.payload).hexdigest(),
                            "wire": packet.payload.hex(), "primary": asdict(primary) if primary else None,
                            "emitted": [asdict(e) for e in events if isinstance(e, CombatEvent)],
                            "legacy_summon_owner": decoder.summon_owners.get(d.source_actor_id),
                            "source_template": getattr(decoder.monster_entities.get(d.source_actor_id), "template_id", None),
                            "target_template": getattr(decoder.monster_entities.get(d.target_actor_id), "template_id", None)})
        if time.monotonic() - last_progress >= 20:
            print(f"{capture.name}: messages={counters['messages']}", flush=True)
            last_progress = time.monotonic()
        return events
    decoder.feed_events = feed
    try:
        events = source.poll()
        normalized.add(events, CombatEvent)
        write_events("normalized", events)
        agg = CombatAggregator()
        for e in events:
            agg.ingest(e)
        aggregate_events = agg.snapshot(include_events=True).combat_events
        aggregate.add(aggregate_events, CombatEvent)
        write_events("aggregate", aggregate_events)
    finally:
        decoder.feed_events = original
        V576EventLedger.accept = original_accept
        if original_finalize is not None:
            del decoder._finalize_wire_damage_owner
        source.close()
        if detail_stream is not None:
            detail_stream.close()
    verify_snapshot()
    if digest_file(capture) != capture_hash:
        raise RuntimeError("Capture changed during replay")
    loaded = {}
    for name, mod in tuple(sys.modules.items()):
        path = getattr(mod, "__file__", None)
        if name.startswith("dragon_nest_builder") and path:
            actual = Path(path).resolve()
            if not actual.is_relative_to(snapshot):
                raise RuntimeError(f"Mixed runtime: {name}: {actual}")
            loaded[name] = str(actual.relative_to(snapshot))
    result = {"schema": "udp-integrity-v27.1", "capture": str(capture), "capture_sha256": capture_hash,
              "runtime": str(snapshot), "manifest_sha256": digest_file(snapshot / "manifest.json"),
              "audit_tool_sha256": digest_file(__file__), "seconds": time.monotonic() - started,
              "raw_decoder": raw.result(), "normalized": normalized.result(), "aggregate": aggregate.result(),
              "census_enabled": has_provenance, "census_scope": "supported embedded candidates; not all opaque UDP",
              "counters": dict(counters), "transport_counts": dict(transport_counts),
              "owner_evidence": dict(ownership), "layouts": dict(layouts),
              "owner_refinements": dict(owner_refinements), "owner_changes": owner_changes,
              "independent_inventory": ({"counts": dict(physical_inventory), "opaque_records": dict(opaque_records),
                  "record_stream_sha256": inventory_stream_hash.hexdigest(),
                  "scope": "all accepted server UDP messages; only complete top-level 0873/0877 fields interpreted; unmatched is NOT proved missing damage"}
                  if wire_inventory else None),
              "event_details": ({"path": str(detail_path), "sha256": digest_file(detail_path)}
                                if event_details else None),
              "examples": dict(examples), "loaded_modules": loaded, "completed": True}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": str(output), "seconds": result["seconds"], "aggregate": result["aggregate"]["categories"],
                      "counters": dict(counters)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--event-details", action="store_true")
    parser.add_argument("--wire-inventory", action="store_true")
    args = parser.parse_args()
    run(args.snapshot, args.capture, args.output, args.event_details, args.wire_inventory)
