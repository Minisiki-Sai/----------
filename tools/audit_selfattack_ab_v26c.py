"""Read-only v26c self-attack toggle audit. Default: no capture replay.

Only before._entity_self_attack_evidence and before._repair_proven_entity_target
are disabled. After wrappers observe original return values without replacing
them. Damage records are compared at decoder.feed_events, BEFORE global replay
normalization or aggregation. Capture mode uses the normal replay source in two
sequential passes, preserving its identity/session/transport handling.

Optional --output-dir writes exclusive per-input-hash JSONL/summary/completion
artifacts. Stdout contains summaries only, never full wire. No scoring, workers
or shared runtime edits. Without --output-dir, detailed evidence is NOT saved.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import weakref

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "outputs/damage_attribution_candidate_v26c_20260908"
FIXTURES = ROOT / "tests/data/summon_attack_v26_raw.json"
SNAPSHOT_HASHES = {
    "actor_state.py": "dffbc814a58269d60ab0208a8f163f44e2f064195dbe0557ec232c156bd72d08",
    "cast_timeline.py": "ca60ee1a75377d36abd2ca9e232ec543d8f838856a09ef9b4f29e4e015a22d5a",
    "core_decoder.py": "432021c4ef66e0152628a4a01955b6e0a3112c8f3a34b76701acd4d4ffa55ce5",
    "damage_attribution.py": "192d5879c414516b24c3ee8a9e4f6f7ce9b07cae314dca6e4f8f75f33f8230de",
    "damage_object_origins.py": "ab1930d571a6c1b77f8b8398fafbb3367261bf747da879c874b2f43cbee9a508",
    "damage_object_v25_shaman_illusion.py": "168de877d3e4a8130e8ba526a803ad57121e8afc24eba838fc8b23d0cf0c424b",
    "entity_owner_evidence.py": "03430605810b7dfde3acca6958215d94e66b4404a910bccbecd77183cdf13d83",
    "generated_attack_instance_specs_v576.py": "8eec0d884ddea8a022c2ab4ee2390be9e8d6808cce4c95b50d57eb4a6f61d65c",
    "generated_mirror_self_attack_specs_v26.py": "e575c2b0b57f07fbdd882c1730dafd8f980466b68301376855e8b8f746234c33",
    "generated_summon_self_attack_specs_v26.py": "a09014e12adf4df03476f3d45782768a526632a171ed8f1ed2aec4713c48d168",
    "models.py": "187fb18ab2182eb58d6a643f2a7fb2bfa39f9b6005b08ee7ce5fbe4d61229ce5",
    "protocol_v576.py": "796c27a3d945225e5ffae6cff42a3588675bbaaaab5fabedd01cae0d1abf4d1c",
    "replay_normalization.py": "1ad0abecb728b2f04cc8b53d7890673e49e1c2288deab8d819b8a1c10b224bca",
    "sources.py": "5383874aefa85d25499a2cb16bcb656d280be67796744104e95474da245d1884",
    "status_decoder.py": "f3bad6c3345dde5f3dc1a68ea709c55706a5488d14bfdfdb883ac0ca21b1b8f6",
    "summon_attack_runtime.py": "85ba74d22d5ebcb059fcaedbbc948d6a066e15f1cd2811f297d6bdc0f9489955",
    "summon_self_attack_evidence_v26.py": "3fbec5ca80b563a1e5478c8e1348a8645acf1cacebd7d7c6423276ea832cfe84",
    "timeline_evidence.py": "3a26caff74867abe369625956c8b2be8f279e879b7e353fb3b7911d15e1dd31a",
    "udp_direction.py": "57e919c3d188ecc7ec31c477501b57bbf0b3efaa70c350431a5c33f53fc94d12",
}
CATEGORIES = ("existing_hit_skill_or_owner_upgraded", "proven_duplicate_removed",
              "target_corrected", "newly_emitted", "unknown_change", "realflag_changed")
IDENTITY = ("timestamp_ms", "source_id", "target_id", "amount", "critical",
            "wire_result_group_id", "wire_damage_object_id")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    """Bound hashing memory to 1 MiB rather than materializing a capture."""
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class PersistentAudit:
    """One exclusive file set per full input SHA256; interrupted sets stay put.

    Only finish() writes the terminal summary and completion sidecar. A valid
    completion requires both checksummed files; a partial JSONL is NOT accepted.
    """
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.paths = {}
        self.handle = None
        self.pending_summary = None
        self.hasher = hashlib.sha256()
        self.lines = self.bytes = 0
        self.last_sync = time.monotonic()
        self.finished = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def _sync(self):
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.last_sync = time.monotonic()

    def _append(self, row):
        data = (canonical(row) + "\n").encode("utf8")
        if self.handle.write(data) != len(data):
            raise OSError("Short audit JSONL write")
        self.hasher.update(data)
        self.lines += 1
        self.bytes += len(data)
        self.handle.flush()
        if self.lines % 64 == 0 or time.monotonic() - self.last_sync >= 5:
            self._sync()

    def emit(self, row):
        if self.finished or self.pending_summary is not None:
            raise RuntimeError("No records allowed after terminal summary")
        if not self.paths:
            if row.get("record_type") != "header":
                raise ValueError("Audit must start with header")
            identity = row["capture_sha256"]
            if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
                raise ValueError("Invalid input SHA256")
            self.input_hash = identity
            self.paths = {"jsonl": self.directory / f"{identity}.jsonl",
                          "summary": self.directory / f"{identity}.summary.json",
                          "complete": self.directory / f"{identity}.complete.json"}
            self.directory.mkdir(parents=True, exist_ok=True)
            if any(os.path.lexists(p) for p in self.paths.values()):
                raise FileExistsError("Existing audit or partial audit for this input hash; use a NEW output directory")
            # This exclusive file also reserves the hash against concurrent runs.
            self.handle = self.paths["jsonl"].open("xb")
        elif row.get("record_type") == "header":
            raise ValueError("One input per PersistentAudit")
        if row.get("record_type") == "summary":
            if row.get("audit_complete") is not True or row.get("capture_sha256") != self.input_hash:
                raise ValueError("Invalid terminal summary")
            self.pending_summary = row
        else:
            self._append(row)

    @staticmethod
    def _exclusive_json(path, row):
        data = (canonical(row) + "\n").encode("utf8")
        with path.open("xb") as stream:
            if stream.write(data) != len(data):
                raise OSError("Short audit sidecar write")
            stream.flush()
            os.fsync(stream.fileno())
        return digest(data)

    def finish(self):
        if self.finished or self.pending_summary is None:
            raise RuntimeError("One verified terminal summary required before completion")
        self._append(self.pending_summary)
        self._sync()
        self.handle.close()
        self.handle = None
        summary_hash = self._exclusive_json(self.paths["summary"], self.pending_summary)
        self._exclusive_json(self.paths["complete"], {
            "schema": "v26c-selfattack-ab-completion.1", "audit_complete": True,
            "audit_passed": self.pending_summary.get("strict_existing_hit_conservation_passed",
                                                      self.pending_summary.get("passed", False)),
            "capture_sha256": self.input_hash, "jsonl": self.paths["jsonl"].name,
            "jsonl_sha256": self.hasher.hexdigest(), "jsonl_bytes": self.bytes, "jsonl_records": self.lines,
            "summary": self.paths["summary"].name, "summary_sha256": summary_hash})
        self.finished = True
        return {name: str(path) for name, path in self.paths.items()}


def _python_container_bytes(value, seen):
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_python_container_bytes(k, seen) + _python_container_bytes(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list)):
        size += sum(_python_container_bytes(v, seen) for v in value)
    return size


def trace_memory_estimate(traces):
    """Sample <=128 entries; never allocate a whole-cache object-id set.

    This is a Python container estimate, NOT RSS or a bound: sample variation,
    cross-entry shared objects, allocator arenas and normal replay are excluded.
    """
    count = len(traces)
    stride = max(1, (count + 127) // 128)
    samples, rows, hex_chars = [], 0, 0
    for index, (key, value) in enumerate(traces.items()):
        message, trace = value
        rows += len(trace["rows"])
        hex_chars += len(message["wire_hex"])
        if index % stride == 0:
            samples.append(_python_container_bytes((key, value), set()))
    average = sum(samples) / len(samples) if samples else 0
    return {"peak_messages": count, "peak_rows": rows, "retained_wire_hex_characters": hex_chars,
            "sample_entries": len(samples), "sample_entry_bytes_min": min(samples, default=0),
            "sample_entry_bytes_max": max(samples, default=0), "sample_entry_bytes_mean": average,
            "estimated_python_container_bytes": round(sys.getsizeof(traces) + count * average),
            "is_rss_or_total_process_peak": False,
            "estimator": "<=128 evenly spaced entries; shared cross-entry values overcounted; replay/allocator excluded"}


def packet_key(message):
    return tuple(message.get(k) for k in ("message_index", "timestamp_seconds", "source_ip",
        "destination_ip", "source_port", "destination_port", "wire_sha256"))


def actor(value):
    try:
        return int(str(value).split("@", 1)[0], 16)
    except ValueError:
        return None


def rule_data_hashes(data_root):
    """Track both rule bytes and inventory; two passes must use the same data."""
    data_root = Path(data_root)
    paths = [p for folder in ("live_dps_profiles", "modular")
             for p in (data_root / folder).rglob("*")
             if p.is_file() and p.suffix.casefold() in (".json", ".gz")]
    paths.extend(p for name in ("dragon_nest_data.json", "skill_adjustments.json")
                 if (p := data_root / name).is_file())
    return {str(p.resolve()): file_digest(p) for p in sorted(set(paths))}


def load_runtime(snapshot=SNAPSHOT):
    """Reject a contaminated baseline or a previously imported shared runtime."""
    snapshot = snapshot.resolve()
    if any(name.startswith("dragon_nest_builder.live_dps") for name in sys.modules):
        raise RuntimeError("Use a fresh process; live_dps was imported before snapshot installation")
    if {p.name for p in snapshot.glob("*.py")} != set(SNAPSHOT_HASHES):
        raise ValueError("v26c snapshot inventory changed")
    for name, expected in SNAPSHOT_HASHES.items():
        if digest((snapshot / name).read_bytes()) != expected:
            raise ValueError(f"Unreviewed snapshot bytes: {name}")
    sys.path.insert(0, str(ROOT))
    from tools.audit_damage_attribution_paired import BaselineFinder, snapshot_modules
    modules = snapshot_modules(snapshot)
    if set(modules.values()) != set(SNAPSHOT_HASHES):
        raise ValueError("Snapshot import mapping incomplete")
    sys.meta_path.insert(0, BaselineFinder(snapshot, modules))
    from dragon_nest_builder.live_dps import sources, protocol_v576
    from dragon_nest_builder.live_dps.models import CombatEvent
    cls = sources._V576PacketEventDecoder
    functions = {
        "core_decoder.py": cls.feed_events,
        "actor_state.py": cls._entity_self_attack_evidence,
        "status_decoder.py": cls._supplemental_embedded_direct_damage_events,
        "protocol_v576.py": protocol_v576.decode_v576_payload,
    }
    for name, fn in functions.items():
        if Path(fn.__code__.co_filename).resolve() != snapshot / name:
            raise ValueError(f"Non-frozen function loaded: {name}")
    if Path(cls._repair_proven_entity_target.__code__.co_filename).resolve() != snapshot / "actor_state.py":
        raise ValueError("Target repair is not frozen")
    paths = list((ROOT / "dragon_nest_builder/live_dps").rglob("*.py")) + list(snapshot.glob("*.py"))
    hashes = {str(path): digest(path.read_bytes()) for path in paths}
    return sources, protocol_v576, CombatEvent, {
        "audit_tool_sha256": digest(Path(__file__).read_bytes()),
        "snapshot": str(snapshot), "snapshot_sha256": SNAPSHOT_HASHES,
        "tracked_runtime_sha256": hashes,
        "rule_data_root": str(ROOT / "dragon_nest_builder/data"),
        "tracked_rule_data_sha256": rule_data_hashes(ROOT / "dragon_nest_builder/data"),
        "loaded_function_paths": {name: fn.__code__.co_filename for name, fn in functions.items()},
        "before_disabled": ["_entity_self_attack_evidence", "_repair_proven_entity_target"],
        "boundary": "decoder.feed_events; no aggregate/global replay normalization claims",
    }


def verify_runtime(metadata):
    if digest(Path(__file__).read_bytes()) != metadata["audit_tool_sha256"]:
        raise RuntimeError("Audit tool changed during execution")
    changed = [name for name, expected in metadata["tracked_runtime_sha256"].items()
               if digest(Path(name).read_bytes()) != expected]
    if changed:
        raise RuntimeError("Runtime files changed during audit: " + repr(changed))
    if (metadata.get("rule_data_root") and rule_data_hashes(metadata["rule_data_root"])
            != metadata["tracked_rule_data_sha256"]):
        raise RuntimeError("Rule data files or inventory changed during audit")


def _typed_records_for_phase(wire, phase):
    """Independent framing walk, NOT a production reader or marker search.

    The message starts records at byte 14. Each record is actor-u32, type-u8,
    payload-length-u16, payload. Every companion must end on the next boundary,
    and the last record must end at the exact outer message end.
    """
    key = bytes.fromhex("c8bfcda4943f2642fd8a723474")
    body = wire[:9] + bytes(v ^ key[(i - phase) % 13] for i, v in enumerate(wire[9:], 9))
    boundaries, cursor = [], 14
    while cursor < len(body):
        if cursor + 7 > len(body):
            return []
        marker = cursor + 4
        declared = int.from_bytes(body[marker + 1:marker + 3], "little")
        end = marker + 3 + declared
        if end > len(body):
            return []
        boundaries.append({"start": cursor, "marker": marker, "type": body[marker],
                           "declared": declared, "end": end})
        cursor = end
    records = []
    for index, boundary in enumerate(boundaries):
        if (boundary["type"], boundary["declared"]) != (8, 115):
            continue
        marker, end = boundary["marker"], boundary["end"]
        u32 = lambda at: int.from_bytes(body[at:at + 4], "little")
        source, target = u32(marker + 8), u32(marker - 4)
        amount = int.from_bytes(body[marker + 19:marker + 27], "little")
        if (not 0 < source < 65536 or target in (0, source, 0xffffffff)
                or not 0 < amount < 10**13 or body[marker + 12] != 0
                or body[marker + 106:marker + 110] != bytes(4)):
            continue
        # Negative-only ambiguity guard: never SELECT a nested signature as a
        # record. A plausible nested result/status makes this slot unprovable.
        nested = False
        for at in range(marker + 36, min(end, len(body) - 3)):
            if not 0 < u32(at - 4) < 0xffffffff:
                continue
            if body[at:at + 3] in (b"\x08\x73\0", b"\x08\x77\0"):
                shift = 4 if body[at + 1] == 0x77 else 0
                if (at + 27 + shift <= len(body) and 0 < u32(at + 8 + shift) < 0xffffffff
                        and 0 < int.from_bytes(body[at + 19 + shift:at + 27 + shift], "little") < 10**13):
                    nested = True
            if (body[at] == 0x13 and 0x47 <= body[at + 1] <= 0x4e and body[at + 2] == 0
                    and at + 16 <= len(body) and 0 < u32(at + 12) < 0xffffffff):
                nested = True
        if nested:
            continue
        records.append({"source": source, "target": target, "group": u32(marker + 4),
                        "object": u32(marker + 13), "amount": amount, "critical": bool(body[marker + 35] & 1),
                        "marker": marker, "phase": phase, "wire_sha256": digest(wire),
                        "record_start": marker - 4, "record_end": end,
                        "typed_record_index": index, "typed_boundaries": boundaries,
                        "outer_declared_length": int.from_bytes(wire[7:9], "little"),
                        "raw_record_sha256": digest(wire[marker - 4:end]),
                        "decoded_record_sha256": digest(body[marker - 4:end]),
                        "self_attack_slot_m102": u32(marker + 102), "context_slot_m114": u32(marker + 114)})
    return records


def wire_records(wire, direction):
    """Only full typed records in ONE validated XOR phase can prove a hit."""
    if (direction != "server_to_client" or len(wire) < 21
            or int.from_bytes(wire[7:9], "little") + 3 != len(wire)):
        return []
    phases = [records for phase in range(13) if (records := _typed_records_for_phase(wire, phase))]
    return phases[0] if len(phases) == 1 else []


def decoded_fields(decoded):
    return {name: getattr(decoded, name, None) for name in (
        "source_actor_id", "target_actor_id", "damage", "critical", "damage_result_group_id",
        "damage_object_id", "record_offset", "layout", "skill_id")}


def matches_decoded(record, decoded, *, target=True):
    pairs = (("source", "source_actor_id"), ("amount", "damage"), ("group", "damage_result_group_id"),
             ("object", "damage_object_id"), ("marker", "record_offset"), ("critical", "critical"))
    return all(record[a] == decoded[b] for a, b in pairs) and (
        not target or record["target"] == decoded["target_actor_id"])


class Probe:
    """Instrumentation is per decoder; neither facade nor class is patched."""
    def __init__(self, decoder, enabled, combat_event):
        self.decoder, self.enabled, self.combat_event = decoder, enabled, combat_event
        self.calls = []
        self._restore = {name: (name in vars(decoder), vars(decoder).get(name)) for name in
                         ("_entity_self_attack_evidence", "_repair_proven_entity_target")}
        original_self = decoder._entity_self_attack_evidence
        original_repair = decoder._repair_proven_entity_target

        def self_attack(packet, decoded):
            result = original_self(packet, decoded) if enabled else None
            if result is not None:
                from dragon_nest_builder.live_dps.summon_attack_runtime import SUMMON_SELF_ATTACK_SPECS
                binding = decoder.entity_owner_evidence.bindings.get(decoded.source_actor_id)
                spec = SUMMON_SELF_ATTACK_SPECS.get((result[2]["entity_template_id"], result[2]["entity_monster_skill_id"]))
                self.calls.append({"hook": "self_attack", "decoded": decoded_fields(decoded),
                                   "wire_sha256": digest(packet.payload),
                                   "result": list(result), "binding": asdict(binding) if binding else None,
                                   "static_spec": asdict(spec) if spec else None})
            return result

        def repair(packet, decoded):
            original = decoded_fields(decoded)
            result = original_repair(packet, decoded) if enabled else decoded
            if decoded_fields(result) != original:
                binding = decoder.entity_owner_evidence.bindings.get(decoded.source_actor_id)
                self.calls.append({"hook": "target_repair", "before": original,
                                   "wire_sha256": digest(packet.payload),
                                   "after": decoded_fields(result), "binding": asdict(binding) if binding else None})
            return result

        decoder._entity_self_attack_evidence = self_attack
        decoder._repair_proven_entity_target = repair

    def close(self):
        """Remove only our instance wrappers and break probe/decoder cycles."""
        if self.decoder is None:
            return
        for name, (existed, value) in self._restore.items():
            if existed:
                setattr(self.decoder, name, value)
            else:
                delattr(self.decoder, name)
        self._restore.clear()
        self.calls.clear()
        self.decoder = None

    def feed(self, packet, feed):
        self.calls = []
        # Passive alias observations only; never call an owner inference helper.
        aliases = {str(k): [int(v[0])] for k, v in self.decoder.summon_owners.items()}
        events = feed(packet)
        for entity, owner in self.decoder.summon_owners.items():
            aliases.setdefault(str(entity), []).append(int(owner[0]))
        rows = [asdict(e) for e in events if isinstance(e, self.combat_event) and not e.healing]
        emitted_sources = {actor(e["source_id"]) for e in rows}
        aliases = {k: sorted(set(v)) for k, v in aliases.items() if emitted_sources.intersection(v)}
        return events, {"rows": rows, "calls": list(self.calls), "aliases": aliases}


def proof_for(record, after):
    self_proofs = [c for c in after["calls"] if c["hook"] == "self_attack"
                   and c.get("wire_sha256") == record["wire_sha256"]
                   and matches_decoded(record, c["decoded"])]
    repairs = [c for c in after["calls"] if c["hook"] == "target_repair"
               and c.get("wire_sha256") == record["wire_sha256"]
               and matches_decoded(record, c["before"], target=False)
               and matches_decoded(record, c["after"])]
    return self_proofs, repairs


def bind_event(event, records, before, after):
    candidates = []
    for record in records:
        if (event["amount"], event["critical"], event["wire_result_group_id"], event["wire_damage_object_id"]) != (
                record["amount"], record["critical"], record["group"], record["object"]):
            continue
        proofs, repairs = proof_for(record, after)
        sources = {record["source"], *before["aliases"].get(str(record["source"]), []),
                   *after["aliases"].get(str(record["source"]), []), *[p["result"][0] for p in proofs]}
        targets = {record["target"], *[r["before"]["target_actor_id"] for r in repairs]}
        if actor(event["source_id"]) not in sources or actor(event["target_id"]) not in targets:
            continue
        if event.get("wire_entity_id", 0) not in (0, record["source"]):
            continue
        candidates.append(record)
    return candidates[0] if len(candidates) == 1 else None


def upgrade_proved(record, event, proofs):
    return any(event["source_id"] == f'0x{p["result"][0]:08X}'
               and str(event["skill_id"]) == str(p["result"][1])
               and event["attribution_method"] == "packet_entity_self_attack"
               and p["binding"] is not None
               and p.get("static_spec") is not None
               and p["static_spec"]["monster_skill_id"] == record["self_attack_slot_m102"]
               and all(event.get(k) == v for k, v in p["result"][2].items())
               for p in proofs)


def classify_message(message, before, after):
    out = {"message": message, "before": before, "after": after, **{k: [] for k in CATEGORIES}}
    raw = bytes.fromhex(message["wire_hex"])
    if digest(raw) != message["wire_sha256"]:
        out["unknown_change"].append({"reason": "fullwire_fingerprint_mismatch"})
        return out
    if Counter(map(canonical, before["rows"])) == Counter(map(canonical, after["rows"])):
        out["unchanged"] = True
        return out
    records = wire_records(raw, message["direction"])
    out["wire_records"] = records
    groups = defaultdict(lambda: {"before": [], "after": []})
    unmapped = {"before": [], "after": []}
    for side, trace in (("before", before), ("after", after)):
        for event in trace["rows"]:
            record = bind_event(event, records, before, after)
            if record is None:
                unmapped[side].append(event)
            else:
                groups[canonical(record)][side].append(event)
    # Unrelated unchanged events remain visible but cannot explain a changed hit.
    if Counter(map(canonical, unmapped["before"])) != Counter(map(canonical, unmapped["after"])):
        out["unknown_change"].append({"reason": "unmapped_or_ambiguous_physical_record", **unmapped})
    for key, pair in groups.items():
        record = json.loads(key)
        physical_key = {"capture": message["capture"], "message_index": message["message_index"],
                        "capture_sha256": message.get("capture_sha256"),
                        "timestamp_seconds": message["timestamp_seconds"],
                        "wire_event_id": message.get("wire_event_id"),
                        "raw_anchor": message.get("raw_anchor"), **record}
        base = {"physical_key": physical_key, **pair}
        old, new = pair["before"], pair["after"]
        if Counter(map(canonical, old)) == Counter(map(canonical, new)):
            continue
        proofs, repairs = proof_for(record, after)
        upgraded = len(new) == 1 and upgrade_proved(record, new[0], proofs)
        repair_only = len(new) == 1 and bool(repairs)
        if len(new) != 1 or not (upgraded or repair_only):
            out["unknown_change"].append({"reason": "no_unique_proven_selfattack_survivor", **base})
            continue
        final = new[0]
        if actor(final["target_id"]) != record["target"]:
            out["unknown_change"].append({"reason": "survivor_target_not_wire", **base})
            continue
        if not old:
            if upgraded:
                out["newly_emitted"].append({**base, "amount": final["amount"], "proofs": proofs})
            else:
                out["unknown_change"].append({"reason": "new_row_without_selfattack_proof", **base})
            continue
        immutable = ("timestamp_ms", "amount", "critical", "wire_result_group_id", "wire_damage_object_id")
        if any(any(e[k] != final[k] for k in immutable) for e in old):
            out["unknown_change"].append({"reason": "physical_amount_time_or_identity_changed", **base})
            continue
        wrong_targets = [e for e in old if e["target_id"] != final["target_id"]]
        if any(not any(actor(e["target_id"]) == r["before"]["target_actor_id"]
                       and actor(final["target_id"]) == r["after"]["target_actor_id"] for r in repairs)
               for e in wrong_targets):
            out["unknown_change"].append({"reason": "target_change_without_exact_repair_call", **base})
            continue
        duplicate = None
        if len(old) == 2:
            # Narrow duplicate acceptance: old wrong-target primary + a correct
            # wire-target hit, both bound to ONE raw marker, final correct hit alive.
            correct = [e for e in old if e["target_id"] == final["target_id"]]
            if len(correct) != 1 or len(wrong_targets) != 1 or not repairs:
                out["unknown_change"].append({"reason": "multiple_rows_not_proven_primary_supplemental_duplicate", **base})
                continue
            duplicate = {**base, "removed": wrong_targets[0], "correct_before": correct[0],
                         "survivor": final, "amount": record["amount"], "repairs": repairs}
            original = correct[0]
        elif len(old) == 1:
            original = old[0]
        else:
            out["unknown_change"].append({"reason": "unproved_multiplicity", **base})
            continue
        changed = {k: [original.get(k), final.get(k)] for k in original.keys() | final.keys()
                   if original.get(k) != final.get(k)}
        allowed = {"target_id", "target_name", "target_job", "target_max_hp"} if repairs else set()
        if upgraded:
            allowed.update({"source_id", "source_name", "job", "skill_id", "skill_name", "attribution_method",
                            "attribution_confidence", "has_real_hit_evidence", "raw_attribution_method",
                            "cast_instance_id", "wire_entity_id", "entity_template_id", "entity_owner_evidence_id",
                            "entity_monster_skill_id", "entity_attack_skill_id", "entity_attack_evidence_id"})
        if set(changed) - allowed:
            out["unknown_change"].append({"reason": "fields_changed_outside_proven_self_chain",
                                          "unexpected_fields": sorted(set(changed) - allowed), **base})
            continue
        if duplicate:
            out["proven_duplicate_removed"].append(duplicate)
        if upgraded and set(changed).intersection({"source_id", "skill_id", "attribution_method", "entity_attack_evidence_id"}):
            out["existing_hit_skill_or_owner_upgraded"].append({**base, "changes": changed, "proofs": proofs})
        if wrong_targets:
            out["target_corrected"].append({**base, "repairs": repairs})
        if original["has_real_hit_evidence"] != final["has_real_hit_evidence"]:
            out["realflag_changed"].append({"physical_key": physical_key,
                "before": original["has_real_hit_evidence"], "after": final["has_real_hit_evidence"],
                "before_method": original["attribution_method"], "after_method": final["attribution_method"]})
    out["raw_realflag_totals"] = {side: sum(e["has_real_hit_evidence"] is True for e in trace["rows"])
                                  for side, trace in (("before", before), ("after", after))}
    return out


def message_meta(packet, capture, index):
    return {"capture": str(capture), "message_index": index, "timestamp_seconds": packet.timestamp_seconds,
            "direction": packet.inferred_direction(), "transport": packet.transport,
            "source_ip": packet.source_ip, "destination_ip": packet.destination_ip,
            "source_port": packet.source_port, "destination_port": packet.destination_port,
            "wire_sha256": digest(packet.payload), "wire_hex": packet.payload.hex(),
            "wire_event_id": int.from_bytes(packet.payload[:5], "little")}


class Summary:
    def __init__(self):
        self.counts = Counter()
        self.totals = {side: {"rows": 0, "damage": 0, "realflags": 0} for side in ("before", "after")}
        self.hashes = {side: hashlib.sha256() for side in self.totals}
        self.removed_damage = self.new_damage = 0
        self.messages = self.changed = 0

    def add(self, result):
        self.messages += 1
        self.changed += not result.get("unchanged", False)
        for category in CATEGORIES:
            self.counts[category] += len(result[category])
        for side in self.totals:
            rows = result[side]["rows"]
            self.totals[side]["rows"] += len(rows)
            self.totals[side]["damage"] += sum(e["amount"] for e in rows)
            self.totals[side]["realflags"] += sum(e["has_real_hit_evidence"] is True for e in rows)
            self.hashes[side].update(canonical([result["message"]["message_index"], result["message"]["wire_sha256"],
                                               sorted([tuple(e[k] for k in IDENTITY) for e in rows], key=repr)]).encode())
        self.removed_damage += sum(r["amount"] for r in result["proven_duplicate_removed"])
        self.new_damage += sum(r["amount"] for r in result["newly_emitted"])

    def result(self):
        before, after = self.totals["before"], self.totals["after"]
        balanced = (before["rows"] - self.counts["proven_duplicate_removed"] + self.counts["newly_emitted"] == after["rows"]
                    and before["damage"] - self.removed_damage + self.new_damage == after["damage"])
        return {"counts": dict(self.counts), "totals": self.totals, "messages": self.messages,
                "changed_messages": self.changed, "proven_duplicate_damage_removed": self.removed_damage,
                "newly_emitted_damage": self.new_damage,
                "seven_field_stream_sha256": {k: h.hexdigest() for k, h in self.hashes.items()},
                "accounting_balanced": balanced,
                "strict_existing_hit_conservation_passed": balanced and not self.counts["unknown_change"]
                    and not self.counts["newly_emitted"]}


def fixture_run(runtime):
    sources, protocol, event_type, metadata = runtime
    fixture = json.loads(FIXTURES.read_text(encoding="utf8"))
    results = []
    for skill in (260401, 260707, 262466, 30615):
        case = next(c for c in fixture["cases"] if c["expected_monster_skill"] == skill)
        pair = [sources._V576PacketEventDecoder(ROOT / "dragon_nest_builder/data") for _ in range(2)]
        probes = [Probe(d, enabled, event_type) for d, enabled in zip(pair, (False, True))]
        for decoder in pair:
            decoder.monster_entities[case["hit"]["target_id"]] = next(t for t in decoder.monster_templates.values() if t.boss)
        birth, owner = case["ownership"]["birth"], case["ownership"]["owner_record"]
        sequence = [(birth["t"], birth["wire"]), (owner["time"], owner["wire"]), (owner["time"] + 1, case["hit"]["wire"])]
        summary, changes = Summary(), []
        for index, (timestamp, wire) in enumerate(sequence, 1):
            if index == 3:
                # Same synthetic admission context as the current production
                # four-case tests. This wrong old owner is NOT owner evidence.
                creation = 6319 if 33607 <= case["hit"]["template_id"] <= 33614 else case["expected_skill"]
                for decoder in pair:
                    decoder.summon_owners[case["hit"]["entity_id"]] = (0x50123456, creation)
            raw = bytes.fromhex(wire)
            packet = protocol.PacketRecord(timestamp, "UDP", "118.25.23.98", "10.1.39.221", 15161, 57027,
                                           raw, len(raw), len(raw), direction_hint="server_to_client")
            traces = [probe.feed(packet, decoder.feed_events)[1] for probe, decoder in zip(probes, pair)]
            message = message_meta(packet, case["name"], index)
            if index == 3:
                message["raw_anchor"] = case["raw_anchor"]
                message["raw_physical_hit_id"] = case["hit"]["physical_hit_id"]
            result = classify_message(message, *traces)
            summary.add(result)
            if not result.get("unchanged"):
                changes.append(result)
        after_hits = traces[1]["rows"]
        assert len(after_hits) == 1
        hit = after_hits[0]
        assert (hit["wire_entity_id"], actor(hit["target_id"]), hit["amount"], hit["wire_result_group_id"], hit["wire_damage_object_id"]) == (
            case["hit"]["entity_id"], case["hit"]["target_id"], case["hit"]["damage"], case["hit"]["result_group"], case["hit"]["object_id"])
        assert actor(hit["source_id"]) == case["life"]["owner_id"]
        assert hit["entity_monster_skill_id"] == skill and hit["entity_attack_skill_id"] == case["expected_skill"]
        assert hit["attribution_method"] == "packet_entity_self_attack"
        results.append({"fixture": case["name"], "raw_physical_hit_id": case["hit"]["physical_hit_id"],
                        "birth_wire_hex": birth["wire"], "owner_wire_hex": owner["wire"],
                        "synthetic_context": "known Boss + intentionally wrong existing admission owner 0x50123456, identical on both sides",
                        "summary": summary.result(), "changes": changes})
        for probe in probes:
            probe.close()
    verify_runtime(metadata)
    return {"schema": "v26c-selfattack-ab.1", "mode": "four-production-fixtures",
            "runtime": metadata, "fixture_sha256": digest(FIXTURES.read_bytes()), "captures_replayed": 0,
            "results": results,
            "passed": all(r["summary"]["strict_existing_hit_conservation_passed"] for r in results)}


def _capture_pass(runtime, capture, capture_hash, enabled, before_traces, summary, emit):
    """A separate normal replay lifetime; NEVER two decoders on one feed."""
    sources, _, event_type, _ = runtime
    replay = sources.V576DiagnosticReplaySource(capture, ROOT / "dragon_nest_builder/data", {})
    decoder = replay._decoder
    probe = Probe(decoder, enabled, event_type)
    references = {"replay": weakref.ref(replay), "decoder": weakref.ref(decoder), "probe": weakref.ref(probe)}
    original_feed = decoder.feed_events
    had_feed, previous_feed = "feed_events" in vars(decoder), vars(decoder).get("feed_events")
    index = 0
    last_log = time.monotonic()

    def feed(packet):
        nonlocal index, last_log
        index += 1
        events, trace = probe.feed(packet, original_feed)
        message = message_meta(packet, str(capture), index)
        message["capture_sha256"] = capture_hash
        key = packet_key(message)
        if not enabled:
            if trace["rows"]:
                before_traces[key] = (message, trace)
        else:
            before = before_traces.pop(key, (message, {"rows": [], "calls": [], "aliases": {}}))[1]
            if before["rows"] or trace["rows"]:
                result = classify_message(message, before, trace)
                summary.add(result)
                if not result.get("unchanged"):
                    emit({"record_type": "message_delta", **result})
        if time.monotonic() - last_log > 20:
            print(f"PROGRESS {'after' if enabled else 'before'} messages={index}", file=sys.stderr, flush=True)
            last_log = time.monotonic()
        return events

    decoder.feed_events = feed
    try:
        # Preserve all transport/identity/session/normalization work. poll's
        # returned full event list is discarded, exactly as before this change.
        replay.poll()
    finally:
        try:
            replay.close()
        finally:
            if had_feed:
                decoder.feed_events = previous_feed
            else:
                del decoder.feed_events
            probe.close()
    return {"side": "after" if enabled else "before", "messages_fed": index, "references": references}


def capture_run(runtime, capture, emit):
    metadata = runtime[3]
    capture = capture.resolve()
    capture_hash = file_digest(capture)
    emit({"record_type": "header", "capture": str(capture), "capture_sha256": capture_hash, "runtime": metadata})
    before_traces = {}
    summary = Summary()
    memory = {"hash_read_buffer_bytes": 1024 * 1024, "pass_lifetimes": [],
              "normal_replay_event_list_still_materialized": True}
    for enabled in (False, True):
        metrics = _capture_pass(runtime, capture, capture_hash, enabled, before_traces, summary, emit)
        references = metrics.pop("references")
        metrics["gc_collected"] = gc.collect()
        metrics["released_after_close_and_gc"] = {name: reference() is None for name, reference in references.items()}
        memory["pass_lifetimes"].append(metrics)
        if not all(metrics["released_after_close_and_gc"].values()):
            raise RuntimeError("Replay/decoder/probe retained after pass; refusing another replay or completion")
        if not enabled:
            memory["before_traces"] = trace_memory_estimate(before_traces)
        verify_runtime(metadata)
    for message, before in before_traces.values():
        result = classify_message(message, before, {"rows": [], "calls": [], "aliases": {}})
        summary.add(result)
        emit({"record_type": "message_delta", **result})
    before_traces.clear()
    if file_digest(capture) != capture_hash:
        raise RuntimeError("Capture changed during audit")
    verify_runtime(metadata)
    result = {**summary.result(), "capture_sha256": capture_hash, "memory_diagnostics": memory}
    emit({"record_type": "summary", "capture": str(capture), "capture_sha256": capture_hash,
          "audit_complete": True, **result})
    return result


def fixture_summary(report):
    return {"record_type": "summary", "mode": report["mode"], "capture": str(FIXTURES),
            "capture_sha256": report["fixture_sha256"], "audit_complete": True,
            "passed": report["passed"], "captures_replayed": 0,
            "snapshot": report["runtime"]["snapshot"],
            "audit_tool_sha256": report["runtime"]["audit_tool_sha256"],
            "results": [{"fixture": r["fixture"], **r["summary"]} for r in report["results"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixtures", action="store_true")
    mode.add_argument("--capture", type=Path, nargs="+")
    parser.add_argument("--allow-capture-replay", action="store_true", help="Explicit opt-in; NEVER used during fixture validation")
    parser.add_argument("--output-dir", type=Path, help="Exclusive per-input SHA256 JSONL, summary and completion files; no overwrites")
    args = parser.parse_args()
    if args.capture and not args.allow_capture_replay:
        parser.error("Capture replay requires --allow-capture-replay; use --fixtures for small checks")
    runtime = load_runtime(args.snapshot)
    if args.fixtures:
        result = fixture_run(runtime)
        compact = fixture_summary(result)
        paths = None
        if args.output_dir:
            with PersistentAudit(args.output_dir) as output:
                output.emit({"record_type": "header", "capture": str(FIXTURES),
                             "capture_sha256": result["fixture_sha256"], "runtime": result["runtime"],
                             "input_kind": "production_fixture_json_not_capture"})
                output.emit({"record_type": "fixture_report", **result})
                output.emit(compact)
                paths = output.finish()
        print(canonical({**compact, "evidence_persisted": paths is not None, "output_paths": paths}), flush=True)
        return 0 if result["passed"] else 2
    if not args.output_dir:
        print("WARNING: no --output-dir: detailed wire audit is NOT persisted; stdout is summaries only", file=sys.stderr, flush=True)
    passed = True
    for capture in args.capture:
        paths = None
        if args.output_dir:
            with PersistentAudit(args.output_dir) as output:
                result = capture_run(runtime, capture, output.emit)
                paths = output.finish()
        else:
            result = capture_run(runtime, capture, lambda row: None)
        print(canonical({"record_type": "summary", "capture": str(capture.resolve()), **result,
                         "evidence_persisted": paths is not None, "output_paths": paths}), flush=True)
        passed &= result["strict_existing_hit_conservation_passed"]
    return 0 if passed else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf8")
    raise SystemExit(main())
