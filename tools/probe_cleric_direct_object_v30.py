"""Bounded v25-index reuse: physical TLV/namespace evidence, never attribution.

Run with Python 3.12 -B. Only 16 indexed frames are protocol-decoded. A header
walk locates them; a byte-only digest verifies the original capture. No replay,
resource scan, cast matching, production mutation, or object-ID truncation.
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
OUT = ROOT / "outputs/cleric_direct_object_v30_20260908"
INDEX = ROOT / "outputs/v25_unregistered_direct_20260908/sunday_occurrences.json"
INVENTORY = INDEX.with_name("sunday.json")
OWNER = 0x4F9E5CDC
OBJECTS = (102000, 102032, 104369)
KEY = bytes.fromhex("c8bfcda4943f2642fd8a723474")
HEADER = struct.Struct("<dBII")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def u32(body, at):
    if at < 0 or at + 4 > len(body):
        raise ValueError("u32 outside complete record")
    return int.from_bytes(body[at:at + 4], "little")


def xor_body(wire, phase):
    return wire[:9] + bytes(v ^ KEY[(i - phase) % 13]
                           for i, v in enumerate(wire[9:], 9))


def inspect(wire):
    """Unique full standard TLV root; unknown records stay opaque leaves.

    No searching for nested markers and no phase filtering by desired object,
    owner, opcode or skill. This diagnostic only supports the observed root14
    envelopes; rejected envelopes cannot supply registrations or hits.
    """
    if not 21 <= len(wire) <= 4096:
        return {"complete": False, "reason": "size_outside_bounded_scope"}
    if int.from_bytes(wire[7:9], "little") + 3 != len(wire):
        return {"complete": False, "reason": "outer_length_mismatch"}
    candidates = []
    for phase in range(13):
        body = xor_body(wire, phase)
        cursor, records = 14, []
        while cursor + 7 <= len(body) and len(records) < 64:
            m = cursor + 4
            n = int.from_bytes(body[m + 1:m + 3], "little")
            end = m + 3 + n
            if end > len(body):
                break
            records.append(dict(start=cursor, marker=m, end=end, opcode=body[m],
                                length=n, actor_prefix=u32(body, cursor),
                                plain_hex=body[cursor:end].hex()))
            cursor = end
        if cursor == len(body) and records:
            candidates.append((phase, body, records))
    if len(candidates) != 1:
        return {"complete": False, "reason": "nonunique_or_incomplete_tlv_root",
                "candidate_phases": [c[0] for c in candidates]}
    phase, body, records = candidates[0]
    return dict(complete=True, phase=phase, root_start=14, root_end=len(body),
                observer=u32(body, 10), plain_hex=body.hex(), records=records)


def registrations(parsed):
    """Observed 09 prefixes only. No template-to-skill or generation claims."""
    if not parsed["complete"]:
        return []
    body = bytes.fromhex(parsed["plain_hex"])
    result = []
    for r in parsed["records"]:
        m = r["marker"]
        if r["opcode"] != 9 or r["length"] not in (93, 98, 110):
            continue
        owner, obj, template = u32(body, m - 4), u32(body, m + 8), u32(body, m + 12)
        if (not 0 < owner < 0xFFFFFFFF or owner != u32(body, m + 44)
                or body[m + 3:m + 8] != bytes(5)
                or not 0 < obj <= 0xFFFFFF or not 0 < template <= 0x7FFFFFFF):
            continue
        parent = None
        if r["length"] == 98:
            candidate, branch, suffix = u32(body, m + 49), u32(body, m + 53), u32(body, m + 69)
            if (body[m + 48] == 0 and 0 < candidate <= 0xFFFFFF and candidate != obj
                    and branch & 0xFFFF == 1 and body[m + 57:m + 65] == b"\xff" * 8
                    and 0 < suffix <= 0xFFFF):
                parent = dict(object=candidate, offset=m + 49, owner=owner,
                              status="wire_parent_field_only_no_parent_generation_or_static_edge")
        result.append(dict(owner=owner, object=obj, template=template, marker=m,
                           object_offset=m + 8, repeated_owner_offset=m + 44,
                           namespace="owner_scoped_runtime_damage_object_u32",
                           parent_field=parent, skill=None))
    return result


def direct_hits(parsed):
    if not parsed["complete"]:
        return []
    body = bytes.fromhex(parsed["plain_hex"])
    return [dict(owner=u32(body, r["marker"] + 8), object=u32(body, r["marker"] + 13),
                 target=r["actor_prefix"], marker=r["marker"],
                 object_offset=r["marker"] + 13, result_group=u32(body, r["marker"] + 4),
                 amount=int.from_bytes(body[r["marker"] + 19:r["marker"] + 27], "little"),
                 namespace="owner_scoped_runtime_damage_object_u32", skill=None)
            for r in parsed["records"] if r["opcode"] == 8 and r["length"] == 115]


def classify(parsed, owner, obj):
    """Classify all full-u32 token occurrences by their real declared leaf."""
    if not parsed["complete"]:
        return []
    body = bytes.fromhex(parsed["plain_hex"])
    token = obj.to_bytes(4, "little")
    regs, hits = registrations(parsed), direct_hits(parsed)
    result, at = [], body.find(token, 9)
    while at >= 0:
        r = next((r for r in parsed["records"] if r["start"] <= at and at + 4 <= r["end"]), None)
        status, actual_owner = "opaque_or_nonrecord_token", None
        reg = next((x for x in regs if x["object_offset"] == at), None)
        hit = next((x for x in hits if x["object_offset"] == at), None)
        if reg:
            actual_owner = reg["owner"]
            status = "same_owner_registration_prefix" if actual_owner == owner else "other_owner_registration"
        elif hit:
            actual_owner = hit["owner"]
            status = "same_owner_direct_hit" if actual_owner == owner else "other_owner_direct_hit"
        elif r and r["opcode"] == 8 and r["length"] == 115 and at == r["marker"] + 19:
            actual_owner = u32(body, r["marker"] + 8)
            status = "damage_amount_not_object"
        elif any(x["parent_field"] and x["parent_field"]["offset"] == at for x in regs):
            status = "parent_field_reference_only"
        result.append(dict(offset=at, classification=status, actual_owner=actual_owner,
                           path=[dict(kind="complete_message", start=0, end=len(body)),
                                 dict(kind="tlv_root", start=14, end=len(body)),
                                 dict(kind="declared_record", **r) if r else dict(kind="untyped"),
                                 dict(kind="u32_field", start=at, end=at + 4)],
                           registration=reg, hit=hit))
        at = body.find(token, at + 1)
    return result


def namespace_join(reg, hit):
    """Equality check only; a True result is NOT lifecycle/skill proof."""
    return (reg["namespace"] == hit["namespace"]
            and reg["owner"] == hit["owner"] and reg["object"] == hit["object"])


def collect():
    from dragon_nest_builder.live_dps import protocol_v576 as p

    inputs = [INDEX, INVENTORY, ROOT / "tools/audit_unregistered_named_hit_v25.py",
              ROOT / "tools/audit_unregistered_object_occurrences_v25.py",
              ROOT / "dragon_nest_builder/live_dps/protocol_v576.py",
              ROOT / "dragon_nest_builder/live_dps/udp_damage_records.py"]
    before = {str(f.relative_to(ROOT)): digest(f) for f in inputs}
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    capture = Path(index["capture"])
    assert index["capture"] == inventory["capture"]
    assert digest(capture) == index["capture_sha256"] == inventory["capture_sha256"]
    selected = [q for q in index["queries"] if q["owner"] == OWNER and q["object"] in OBJECTS]
    assert {q["object"] for q in selected} == set(OBJECTS)
    wanted = {}
    for q in selected:
        assert q["count"] == len(q["samples"]) < 80, "truncated occurrence index"
        for s in q["samples"]:
            wanted.setdefault(s["frame"], []).append((q, s))
    assert len(wanted) == 16
    frames, counts = [], Counter()
    with capture.open("rb") as stream:
        assert stream.read(6) == b"DNDP1\0"
        for frame_no in range(1, max(wanted) + 1):
            offset = stream.tell()
            header = stream.read(HEADER.size)
            if len(header) != HEADER.size:
                raise ValueError("truncated capture header")
            ts, adapter, original, size = HEADER.unpack(header)
            assert 0 < size <= 1048576
            counts["headers_read"] += 1
            if frame_no not in wanted:
                stream.seek(size, 1)
                continue
            raw = stream.read(size)
            assert len(raw) == size
            pkt = p.decode_captured_frame(ts, raw, original)
            assert pkt and pkt.transport == "UDP"
            counts["frames_protocol_decoded"] += 1
            messages = p.split_v576_payload(pkt.payload)
            for q, sample in wanted[frame_no]:
                wire = messages[sample["message"]]
                assert wire.hex() == sample["wire"] and ts == sample["time"]
                assert pkt.inferred_direction() == sample["direction"]
                parsed = inspect(wire)
                assert parsed["complete"] and parsed["phase"] == sample["phase"]
                assert parsed["plain_hex"] == sample["plain"]
                occurrences = classify(parsed, q["owner"], q["object"])
                assert [x["offset"] for x in occurrences] == sample["offsets"]
                # Current stateless decoder is observation-only; no event feed.
                current = list(p.decode_v576_server_damage_object_registrations(
                    wire, pkt.inferred_direction(), (), include_unmapped=True))
                client = p.decode_v576_damage_object_registration(wire, pkt.inferred_direction())
                if client:
                    current.append(client)
                frames.append(dict(frame=frame_no, file_offset=offset, timestamp=ts, adapter=adapter,
                    original_length=original, captured_length=size, capture_header_hex=header.hex(),
                    frame_hex=raw.hex(), frame_sha256=hashlib.sha256(raw).hexdigest(),
                    udp_payload_hex=pkt.payload.hex(), message=sample["message"],
                    wire_hex=wire.hex(), wire_sha256=hashlib.sha256(wire).hexdigest(),
                    direction=pkt.inferred_direction(), source_ip=pkt.source_ip,
                    destination_ip=pkt.destination_ip, source_port=pkt.source_port,
                    destination_port=pkt.destination_port, anchor_owner=q["owner"],
                    anchor_object=q["object"], delta_to_anchor=sample["delta"],
                    parsed=parsed, occurrences=occurrences,
                    current_registration_observations=[asdict(r) for r in current]))
    anchors = []
    for q in sorted(selected, key=lambda q: q["object"]):
        rows = [r for r in frames if r["anchor_object"] == q["object"]]
        first = min(rows, key=lambda r: (r["frame"], r["message"]))
        assert first["timestamp"] == q["time"]
        example = next(x for row in inventory["rows"] for x in row["examples"]
                       if x["raw_hit"]["source_actor_id"] == OWNER
                       and x["raw_hit"]["damage_object_id"] == q["object"]
                       and x["time"] == q["time"])
        assert example["event"]["wire_entity_id"] == 0 and example["raw_hit"]["skill_id"] is None
        assert example["wire"] == first["wire_hex"]
        hit = next(h for h in direct_hits(first["parsed"]) if h["owner"] == OWNER and h["object"] == q["object"])
        assert hit["result_group"] == example["raw_hit"]["damage_result_group_id"]
        assert hit["amount"] == example["raw_hit"]["damage"]
        observations = Counter(x["classification"] for r in rows for x in r["occurrences"])
        assert observations["same_owner_registration_prefix"] == 0
        assert first["parsed"]["records"][0]["opcode"] == 8
        anchors.append(dict(owner=OWNER, object=q["object"],
            sampling_label_only=q["diagnostic_skill"], verified_skill=None,
            inventory_packet_serial_not_raw_frame=example["packet"], first_raw_frame=first["frame"],
            first_file_offset=first["file_offset"], first_timestamp=first["timestamp"], hit=hit,
            index_count=q["count"], retained_samples=len(q["samples"]),
            occurrence_frames=[r["frame"] for r in rows], observations=dict(observations),
            root_parent_chain=dict(first_root=first["parsed"], same_owner_registration=None,
                                   runtime_parent=None, static_template=None, skill=None),
            conclusion="unresolved_no_same_owner_registration_in_reused_full_udp_u32_index"))
    after = {str(f.relative_to(ROOT)): digest(f) for f in inputs}
    if before != after:
        raise RuntimeError("Input changed during probe; evidence not published")
    return dict(schema="cleric-direct-object-v30-diagnostic-only", capture=str(capture),
        capture_size=capture.stat().st_size, capture_sha256=index["capture_sha256"],
        capture_hash_verified=True, inputs_sha256=before, inputs_unchanged=True,
        scope=dict(owner=OWNER, objects=OBJECTS, anchor_job="圣骑士",
            label_policy="Three different historical skill buckets; skill identities remain unproved.",
            namespace="capture + owner + complete runtime object u32; generation unresolved",
            first_occurrence_coverage="Reused existing full-capture UDP index; all samples below cap80.",
            exclusions=["summons", "FFFFFFFF relic templates", "new captures", "heavy replay",
                        "other workers", "resources", "recent casts", "skill names as truth"]),
        work=dict(counts, fresh_full_token_scans=0, replay_workers=0, resource_files_opened=0,
                  capture_byte_hash_passes=1),
        anchors=anchors, frames=frames,
        result=dict(anchors_investigated=3, new_same_owner_registration_chains=0,
                    deterministic_skill_confirmations=0, mainline_gain_measured=False,
                    mainline_gain=None, production_files_changed=[]),
        limitations=["No claim that the three old labels are three verified skills.",
            "Index coverage assumes normal decoder UDP splitting and contiguous little-endian u32 IDs.",
            "Fragmented/compressed/transformed IDs and lost packets cannot be excluded by this index.",
            "Other-owner 09 controls are observations, not new cleric chains or mainline gains.",
            "No runtime generation, template or skill can be recovered for these three anchors.",
            "No Crusader/Inquisitor survey: reused index has no such anchors; no expansion scan."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT / "evidence.json")
    args = parser.parse_args()
    target = args.output.resolve()
    if target.parent != OUT.resolve() or target.exists():
        raise ValueError("Only a new evidence file in the exclusive output directory is permitted")
    result = collect()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(output=str(target), work=result["work"], result=result["result"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
