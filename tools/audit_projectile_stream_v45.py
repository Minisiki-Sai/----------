"""Offline, non-production mirror of the statically reviewed stream prefix.

Only an explicit caller-supplied stream boundary is accepted. No offset search,
new XOR key, packet-family promotion, skill inference or native execution.
Field names express the observed reader/lookup branches, not guessed gameplay
semantics. This prefix alone is never an attribution certificate.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "outputs/projectile_evidence_v45_20260909"
FIXTURE = ROOT / "tests/data/damage_object_captured_ffff_parent_chain.json"


def native_evidence():
    from tools.review_client_damage_types_v44 import StaticView, CodeReview, capstone, BASE
    view = StaticView()
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    expectations = {
        0x22f260: ("jmp", "0x14022f170"),
        0x22f19e: ("add", "rdx, qword ptr [rdi + 8]"),
        0x22f1aa: ("add", "dword ptr [rdi + 0x14], ebx"),
        0x22f230: ("mov", "qword ptr [rcx + 8], rdx"),
        0x22f234: ("mov", "dword ptr [rcx + 0x10], r8d"),
        0x22f2a8: ("cmp", "r15d, 9"),
        0x22f2de: ("mov", "r10d, 6"),
        0x22f2e6: ("mov", "r10d, 9"),
        0xa9046a: ("lea", "r8d, [r9 + 3]"),
        0xa90479: ("call", "0x14022f270"),
        0xa90484: ("mov", "r9d, 7"),
        0xa90499: ("call", "0x14022f270"),
        0xa904a4: ("mov", "r9d, 7"),
        0xa904b9: ("call", "0x14022f270"),
        0xa904c4: ("mov", "r9d, 7"),
        0xa904d9: ("call", "0x14022f270"),
        0xa90525: ("cmp", "ecx, 1"),
        0xa90546: ("mov", "r8d, 8"),
        0xa90557: ("mov", "r8d, 4"),
        0xa907b4: ("call", "0x140c6eda0"),
        0xa90e74: ("call", "0x140a8d190"),
        0xa8d1af: ("mov", "rbp, rdx"),
        0xa8d4fd: ("movups", "xmm0, xmmword ptr [rbp]"),
        0xa8d501: ("movups", "xmmword ptr [rbx + 0x1740], xmm0"),
        0xa90fb8: ("mov", "edx, dword ptr [rbp + 0x10]"),
        0xa90fbe: ("call", "0x140c74540"),
        0xc74557: ("mov", "dword ptr [rdi + 0x1730], ebx"),
        0xc74567: ("jmp", "0x140c6fec0"),
        0xc6fef9: ("mov", "ebx, dword ptr [rdi + 0x1730]"),
    }
    checks = []
    for rva, expected in expectations.items():
        ins = next(decoder.disasm(view.read(rva, 15), BASE + rva))
        if (ins.mnemonic, ins.op_str) != expected:
            raise ValueError(f"native evidence changed at {rva:#x}")
        checks.append(CodeReview.row(ins))
    # Register, erase and lookup demonstrably use the same native object map.
    map_sites = (0xc6ff1a, 0xc73ba1, 0xc6edbc)
    maps = []
    for rva in map_sites:
        ins = next(decoder.disasm(view.read(rva, 15), BASE + rva))
        maps.append(CodeReview.row(ins))
    map_targets = [m["rip_relative_targets"] for m in maps]
    if not all(m == [0x1191f30] for m in map_targets):
        raise ValueError("register/erase/lookup native index does not match")
    table = struct.unpack("<10I", view.read(0x22f57c, 40))
    if table != (0x22f2bc,0x22f2c6,0x22f2ce,0x22f2c6,0x22f2ce,
                 0x22f2c6,0x22f2d6,0x22f2de,0x22f2de,0x22f2e6):
        raise ValueError("native reader type table changed")
    return dict(client_sha256=sha256(view.raw).hexdigest(), inputs=view.inputs,
                checked_instructions=checks, native_object_map_sites=maps,
                reader_type_jump_table=list(table),
                limitations=["Static bytes, not a live execution trace.",
                             "Caller-provided actor context is not itself a decoded wire owner certificate."])


@dataclass(frozen=True)
class StreamPrefix:
    opaque_u32: int
    equipment_branch: int
    object_index: int
    config_row: int | None
    vector_raw: tuple[str, ...]
    reference_branch: int
    reference_id: int
    inventory_uid_branch: int | None
    inventory_uid: int | None
    indexed_item_reference: int | None
    action_resource: int
    child_sequence: int
    inline_definition_branch: int
    consumed: int


class TruncatedPrefix(ValueError):
    pass


def server_stream_bounds(plain: bytes, marker: int) -> tuple[int, int]:
    """The little-endian record size excludes the three-byte kind/size header."""
    if marker < 0 or marker + 3 > len(plain):
        raise TruncatedPrefix("server record header truncated")
    if plain[marker] != 9:
        raise ValueError("not a selected server kind-9 record")
    declared = int.from_bytes(plain[marker+1:marker+3], "little")
    start = marker + 3
    end = start + declared
    if end > len(plain):
        raise TruncatedPrefix("server record body truncated")
    return start, end


def parse_prefix(data: bytes, start: int, end: int) -> StreamPrefix:
    """Mirror a90370 through a90a59; retain the unparsed suffix explicitly.

    22f260 -> 22f170: byte-count reads. 22f270 types 9/7/1/2 consume
    9/6/2/1 bytes respectively. All four vector reads are mandatory.
    Source/owner ID is NOT present in this stream's prefix; the caller supplies
    a checked actor-handle pair to the Projectile constructor separately.
    """
    if not 0 <= start <= end <= len(data):
        raise ValueError("invalid explicit stream bounds")
    at = start

    def raw(count):
        nonlocal at
        if at + count > end:
            raise TruncatedPrefix(f"field at {at-start} needs {count} bytes")
        result = data[at:at+count]
        at += count
        return result

    def integer(count, signed=False):
        return int.from_bytes(raw(count), "little", signed=signed)

    opaque = integer(4)
    equipment_branch = integer(1)
    obj = integer(4)
    config = integer(4) if equipment_branch == 0 else None
    vectors = tuple(raw(n).hex() for n in (9, 6, 6, 6))
    reference_branch = integer(1, True)
    reference = integer(4)
    uid_branch = uid = indexed_item = None
    if reference_branch == 1:
        uid_branch = integer(1)
        if uid_branch:
            uid = integer(8)
        else:
            indexed_item = integer(4)
    action = integer(2, True)
    child = integer(1, True)
    inline = integer(1, True)
    return StreamPrefix(opaque, equipment_branch, obj, config, vectors,
                        reference_branch, reference, uid_branch, uid,
                        indexed_item, action, child, inline, at-start)


def fixture_audit():
    # Existing production decoder proposes only already-audited boundaries.
    from dragon_nest_builder.live_dps import protocol_v576 as p
    evidence = native_evidence()
    raw = FIXTURE.read_bytes()
    cases = json.loads(raw)["cases"]
    results = []
    unique = set()
    for case in cases:
        for packet in case.get("packets", ()):
            if "wire_hex" not in packet:
                continue
            wire = bytes.fromhex(packet["wire_hex"])
            wire_hash = sha256(wire).hexdigest()
            if wire_hash != packet["wire_sha256"]:
                raise ValueError("fixture wire hash mismatch")
            if wire_hash in unique:
                continue
            unique.add(wire_hash)
            direction = packet.get("direction")
            observations = []
            client = p.decode_v576_damage_object_registration(wire, direction)
            if client:
                # Every wrapped form preserves the owner-relative stream.
                observations.append((client, client.record_offset + 5, "client"))
            for server in p.decode_v576_server_damage_object_registrations(
                    wire, direction, include_unmapped=True):
                # Do not relabel separately audited parent/compact schemas.
                if "096f-152" in server.layout:
                    continue
                observations.append((server, server.record_offset + 3, "server"))
            for observed, start, family in observations:
                plain = p._decode_v576_body(wire, observed.xor_phase)
                end = len(plain)
                if family == "server":
                    start, end = server_stream_bounds(plain, observed.record_offset)
                item = dict(case=case["name"], packet_role=packet.get("role"),
                            frame=packet.get("frame"), capture=case["capture"],
                            capture_sha256=case.get("capture_sha256"),
                            wire_sha256=wire_hash, family=family,
                            stream_start=start, stream_end=end,
                            expected_owner=observed.source_actor_id,
                            expected_object=observed.damage_object_id,
                            expected_template=observed.projectile_template_id,
                            production_layout=observed.layout)
                try:
                    parsed = parse_prefix(plain, start, end)
                    item["parsed"] = asdict(parsed)
                    item["object_matches"] = parsed.object_index == observed.damage_object_id
                    item["config_matches"] = parsed.config_row == observed.projectile_template_id
                    item["reference_equals_caller_owner"] = parsed.reference_id == observed.source_actor_id
                    item["unparsed_suffix_bytes"] = end - start - parsed.consumed
                    item["prefix_match"] = item["object_matches"] and item["config_matches"]
                except (TruncatedPrefix, ValueError) as exc:
                    item["error"] = str(exc)
                    item["prefix_match"] = False
                results.append(item)
    report = dict(fixture=str(FIXTURE), fixture_sha256=sha256(raw).hexdigest(),
                  script_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  unique_udp_messages=len(unique), comparisons=len(results),
                  matching_prefixes=sum(r["prefix_match"] for r in results),
                  new_confirmed_hits=0, production_changes=False, results=results,
                  native_evidence=evidence,
                  limits=["A matching prefix does not certify a new packet family or skill.",
                          "Owner comes from caller context, not a guessed repeated field.",
                          "Unknown suffix and action-table lookup remain unresolved.",
                          "Existing fixture boundaries are selection bias, not corpus coverage."])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stream_fixture_audit.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps({k: v for k,v in report.items() if k not in ("results", "limits", "native_evidence")}, ensure_ascii=False))
    if not results or not all(r["prefix_match"] for r in results):
        raise ValueError("selected known prefix regression; inspect the saved mismatches")


if __name__ == "__main__":
    fixture_audit()
