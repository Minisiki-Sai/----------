"""Pure diagnostic registration decoders; NOT imported by the runtime.

Offsets include the UDP header. All six envelopes require exactly one valid
XOR phase out of all 13 phases, as well as the audited length/owner offset.
Decoding an object registration does NOT assign a skill. In particular the
extended field is returned as a candidate parent, never a nearest-template
or nearest-action relation. Run --fixtures to test retained real packets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body

# length: (owner offset, phase, shape)
LAYOUTS = {107: (10, 12, "resource"), 114: (17, 6, "resource"),
           117: (20, 9, "resource"), 112: (10, 11, "parent"),
           119: (17, 5, "parent"), 122: (20, 8, "parent")}


def u32(data: bytes, at: int) -> int:
    return int.from_bytes(data[at:at + 4], "little")


@dataclass(frozen=True, slots=True)
class CandidateRegistration:
    layout: str
    packet_length: int
    owner_offset: int
    xor_phase: int
    source_actor_id: int
    damage_object_id: int
    projectile_template_id: int
    effect_resource_id: int
    child_sequence: int
    parent_object_id_candidate: int | None
    parent_branch_word: int | None
    normalized_tail_hex: str
    repeated_resource_id: int | None = None
    resource_missing_sentinel: bool = False
    opaque_extension_hex: str = ""


def _fields(decoded: bytes, o: int, shape: str, known_templates) -> dict | None:
    owner, obj, template = u32(decoded, o), u32(decoded, o + 9), u32(decoded, o + 13)
    if not (0 < owner < 0xffffffff and owner == u32(decoded, o + 45)
            and decoded[o + 4:o + 9] == bytes(5)
            and 0 < obj <= 0xffffff and 0 < template <= 0x7fffffff):
        return None
    if known_templates is not None and template not in known_templates:
        return None
    parent = branch_word = None
    if shape == "resource":
        word = u32(decoded, o + 49)
        resource, sequence = word & 0xffff, word >> 16
        if not (resource and u32(decoded, o + 65) == resource
                and decoded[o + 53:o + 61] == b"\xff" * 8):
            return None
    else:
        parent = u32(decoded, o + 50)  # FULL little-endian runtime ID; not low16.
        branch_word = u32(decoded, o + 54)
        resource, sequence = u32(decoded, o + 70), branch_word >> 16
        if not (decoded[o + 49] == 0 and 0 < parent <= 0xffffff and parent != obj
                and branch_word & 0xffff == 1
                and decoded[o + 58:o + 66] == b"\xff" * 8
                and 0 < resource <= 0xffff):
            return None
        # Here resource is NOT the branch word's low16. It is a separate field.
    return dict(source_actor_id=owner, damage_object_id=obj,
                projectile_template_id=template, effect_resource_id=resource,
                child_sequence=sequence, parent_object_id_candidate=parent,
                parent_branch_word=branch_word,
                normalized_tail_hex=decoded[o:].hex(),
                repeated_resource_id=u32(decoded, o + 65) if shape == "resource" else None)


def decode_candidate_registration(payload: bytes, *, known_templates=None) -> CandidateRegistration | None:
    """Fail closed on length, guard failure, phase ambiguity or unaudited phase.

    Direction and encryption header strings are not skill evidence. The
    variable accelerator headers are allowed only with the complete guards.
    Passing a DNT template set validates existence, not skill ownership.
    """
    layout = LAYOUTS.get(len(payload))
    if layout is None:
        return None
    o, expected_phase, shape = layout
    accepted = []
    for phase in range(13):
        fields = _fields(_decode_v576_body(payload, phase), o, shape, known_templates)
        if fields is not None:
            accepted.append((phase, fields))
    if len(accepted) != 1 or accepted[0][0] != expected_phase:
        return None
    phase, fields = accepted[0]
    return CandidateRegistration(shape, len(payload), o, phase, **fields)


def decode_resource_registration(payload: bytes, *, known_templates=None) -> CandidateRegistration | None:
    row = decode_candidate_registration(payload, known_templates=known_templates)
    return row if row is not None and row.layout == "resource" else None


def decode_parent_registration(payload: bytes, *, known_templates=None) -> CandidateRegistration | None:
    row = decode_candidate_registration(payload, known_templates=known_templates)
    return row if row is not None and row.layout == "parent" else None


def decode_extended_resource_registration(payload: bytes, *, known_templates=None,
                                          allow_missing_resource=False) -> CandidateRegistration | None:
    """120-byte CONTROL extension, kept separate from the six-layout decoder.

    Nine opaque bytes + four FF bytes at O+61 insert 13 bytes before the
    original O+61 float/resource suffix. No semantic name is guessed for
    the nine opaque bytes. A 0xffff resource suffix is opt-in, not equality.
    This function decodes identity ONLY, never chooses an action or skill.
    """
    if len(payload) != 120 or payload[5:9] != bytes.fromhex("0c6d7500"):
        return None
    candidates=[];o=10
    for phase in range(13):
        b=_decode_v576_body(payload,phase)
        # Normalize only the observed insertion, then reuse ALL base guards.
        normalized=b[:o+61]+b[o+74:]
        repeated=u32(b,o+78);word=u32(b,o+49);resource=word&0xffff
        sentinel=bool(resource != 0xffff and repeated == 0xffff)
        if b[o+70:o+74] != b"\xff"*4:
            continue
        if sentinel:
            if not allow_missing_resource:
                continue
            patched=bytearray(normalized)
            patched[o+65:o+69]=resource.to_bytes(4,"little")
            normalized=bytes(patched)
        fields=_fields(normalized,o,"resource",known_templates)
        if fields is None:
            continue
        fields.update(normalized_tail_hex=b[o:].hex(),repeated_resource_id=repeated,
                      resource_missing_sentinel=sentinel,opaque_extension_hex=b[o+61:o+74].hex())
        candidates.append((phase,fields))
    if len(candidates)!=1 or candidates[0][0]!=12:
        return None
    phase,fields=candidates[0]
    return CandidateRegistration("resource_extra13",120,o,phase,**fields)


class ExactTailReplicaTracker:
    """Audit-only same-slot generations; copied wrappers never create a cast.

    Replicas require byte-identical decoded tails, same scene/owner/full ID,
    absolute <=250ms from FIRST packet (relay timestamps can move backwards).
    A delayed copy of a superseded generation must NOT revive that slot.
    TTL is a duplicate guard, not an attribution signal. A different tail
    supersedes the slot even if its template has no known skill.
    """
    def __init__(self):
        self.slots = {}
        self.counters = Counter()
        self.recent_tails = {}

    def observe(self, row: CandidateRegistration, *, scene: int, timestamp: float):
        key = (scene, row.source_actor_id, row.damage_object_id)
        token=(*key,row.normalized_tail_hex)
        prior=self.recent_tails.get(token)
        if prior and abs(timestamp-prior[0]) <= .25:
            return prior[1], True  # Do NOT overwrite current generation.
        self.counters[key] += 1
        self.slots[key] = (row.normalized_tail_hex, timestamp, self.counters[key])
        self.recent_tails[token]=(timestamp,self.counters[key])
        return self.slots[key][2], False


def validate_fixtures(folder: Path) -> dict:
    totals, shapes, negative = Counter(), Counter(), Counter()
    pairs, failures, examples = defaultdict(list), [], {}
    for path in sorted(folder.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for instance in doc.get("instances", []):
            payload = bytes.fromhex(instance["wire_payload_hex"])
            row = decode_candidate_registration(payload)
            totals["real_packets"] += 1
            if row is None:
                failures.append({"capture": path.stem, "p": instance["p"], "length": len(payload)})
                continue
            assert bytes.fromhex(row.normalized_tail_hex) == bytes.fromhex(instance["normalized_tail_hex"])
            assert row.source_actor_id == instance["owner"] and row.damage_object_id == instance["obj"]
            totals["decoded"] += 1
            shapes[str(len(payload))] += 1
            pairs[(path.stem, row.normalized_tail_hex)].append(row.packet_length)
            examples.setdefault(str(len(payload)), {"capture": path.stem, "p": instance["p"], **asdict(row)})
            if str(len(payload)) not in negative:
                # Target guards are mutated in plaintext, then XOR-encoded again.
                b = _decode_v576_body(payload, row.xor_phase)
                o = row.owner_offset
                sites = [("five_zero", o + 4), ("repeat_owner", o + 45),
                         ("eight_ff", o + (53 if row.layout == "resource" else 58))]
                sites += ([("repeat_resource", o + 65)] if row.layout == "resource" else
                          [("parent_tag", o + 49), ("branch_type", o + 54)])
                for label, at in sites:
                    mutated = bytearray(b); mutated[at] ^= 1
                    assert decode_candidate_registration(_decode_v576_body(bytes(mutated), row.xor_phase)) is None, label
                    negative[label] += 1
                assert decode_candidate_registration(payload + b"\0") is None
                assert decode_candidate_registration(payload[:-1]) is None
                assert decode_candidate_registration(payload, known_templates=frozenset()) is None
                # A wrong phase must not be accepted even if structurally valid.
                wrong = _decode_v576_body(b, (row.xor_phase + 1) % 13)
                assert decode_candidate_registration(wrong) is None
                track = ExactTailReplicaTracker()
                assert track.observe(row, scene=0, timestamp=1) == (1, False)
                assert track.observe(row, scene=0, timestamp=1.1) == (1, True)
                assert track.observe(row, scene=0, timestamp=.9999) == (1, True)
                assert track.observe(row, scene=0, timestamp=1.4) == (2, False)
                assert track.observe(row, scene=1, timestamp=1.5) == (1, False)
                negative[str(len(payload))] += 1
    return {"totals": totals, "lengths": shapes, "guard_and_generation_tests": negative,
            "failures": failures, "examples": examples,
            "note": "Retained instances include only first wrapper; replay validates actual replica bytes."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    result = validate_fixtures(args.fixtures)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "examples"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
