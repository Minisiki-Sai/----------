"""Native kind-8 reader mirror, using bytes only; never emits combat events.

The caller must supply an independently framed, complete typed record. This
audit deliberately leaves runtime-dependent suffixes opaque. It does not
rename legacy result-group metadata, infer skills, resolve live handles, or
change existing totals. Client code and sample inputs are hashed.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
import ast
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/projectile_gap_v46_20260909'


@dataclass(frozen=True)
class NativeDamagePrefix:
    random_seed_u32: int
    source_kind: int
    source_id: int
    uid_branch: int
    indexed_object_id: int | None
    weapon_uid: int | None
    receiver_action_index: int
    damage_i64: int
    second_i64: int
    result_flags_i8: int
    # This is not a skill certificate. Offsets are relative to the kind byte.
    field_offsets: dict
    common_tail_raw: str
    consumed: int
    suffix_raw: str


def parse_record(record: bytes) -> NativeDamagePrefix:
    """Parse target-u32 + kind-u8 + length-u16 + body, no signature search.

    52a48a's common fields consume 95 bytes for indexed references, 99 for
    UID references. The rest depends on source object state and virtual reader
    calls. It is retained without assuming a skill slot or a universal layout.
    """
    if len(record) < 7 or record[4] != 8:
        raise ValueError('explicit complete kind-8 record required')
    declared = int.from_bytes(record[5:7], 'little')
    if len(record) != 7 + declared:
        raise ValueError('declared body and exact record boundary disagree')
    start, at = 7, 7
    offsets = {}

    def integer(name, size, signed=False):
        nonlocal at
        if at + size > len(record):
            raise ValueError(f'truncated native field: {name}')
        offsets[name] = at - 4
        value = int.from_bytes(record[at:at+size], 'little', signed=signed)
        at += size
        return value

    seed = integer('random_seed', 4)
    source_kind = integer('source_kind', 1, True)
    source = integer('source_id', 4)
    branch = integer('uid_branch', 1)
    uid = indexed = None
    if branch:
        uid = integer('weapon_uid', 8)
    else:
        indexed = integer('indexed_object_id', 4)
    action = integer('receiver_action_index', 2, True)
    damage = integer('damage', 8, True)
    second = integer('second_i64', 8, True)
    flags = integer('result_flags', 1, True)
    # Four compressed vectors (9 each); reader types 6,5,1,2 have sizes
    # 4,2,2,1. Do not guess the gameplay meanings of these common fields.
    common_sizes = (9, 9, 9, 9, 4, 1, 2, 2, 2, 1, 1, 2, 4, 4, 1, 1, 1)
    tail_start = at
    for number, size in enumerate(common_sizes):
        integer(f'common_{number}', size)
    return NativeDamagePrefix(seed, source_kind, source, branch, indexed, uid,
                              action, damage, second, flags, offsets,
                              record[tail_start:at].hex(), at-start,
                              record[at:].hex())


def native_evidence():
    from tools.review_client_damage_types_v44 import StaticView, CodeReview, BASE
    view, checks = StaticView(), []
    code = CodeReview(view)
    expected = {
        0x52a563: ('lea', 'rdx, [rbp - 0x60]'),
        0x52a56e: ('call', '0x14022f260'),
        0x52a5b2: ('cmp', 'byte ptr [rsp + 0x51], r13b'),
        0x52a5be: ('lea', 'r8d, [rdi + 9]'),
        0x52a5c9: ('mov', 'r8d, 4'),
        0x52a5e1: ('mov', 'r9d, 1'),
        0x52a5eb: ('lea', 'rdx, [rsp + 0x54]'),
        0x52a602: ('lea', 'rdx, [rbp + 0x70]'),
        0x52a618: ('lea', 'rdx, [rbp + 0xb8]'),
        0x52a63b: ('lea', 'rdx, [rbp + 0x78]'),
        0x52a891: ('mov', 'edx, dword ptr [rsp + 0x54]'),
        0x52a89a: ('mov', 'rax, qword ptr [rsi + 0x250]'),
        0x52a8ac: ('add', 'rcx, rsi'),
        0x52a8af: ('call', '0x14004ef30'),
        0x52a962: ('cmp', 'byte ptr [rsp + 0x51], r13b'),
        0x52a969: ('mov', 'rdx, qword ptr [rsp + 0x70]'),
        0x52a978: ('call', '0x140c6ed40'),
        0x52a97f: ('mov', 'edx, dword ptr [rsp + 0x58]'),
        0x52a98c: ('call', '0x140c6eda0'),
        0x52a99b: ('movsx', 'ecx, byte ptr [rsp + 0x52]'),
        0x52a9e1: ('mov', 'edx, dword ptr [rsp + 0x60]'),
        0x52aa37: ('call', '0x140524f80'),
        0x52b099: ('mov', 'edx, dword ptr [rbp - 0x60]'),
        0x52b09c: ('call', '0x14022d0c0'),
        0x22d0c9: ('mov', 'dword ptr [rcx], edx'),
        0x22d0e8: ('shr', 'r8d, 0x1e'),
        0x22d0ec: ('xor', 'r8d, dword ptr [rax - 8]'),
        0x22d0f0: ('imul', 'ecx, r8d, 0x6c078965'),
        0x22d0f7: ('add', 'ecx, r9d'),
        0x22d100: ('cmp', 'r9d, 0x270'),
    }
    for rva, pair in expected.items():
        ins = next(code.dis.disasm(view.read(rva, 15), BASE+rva))
        if (ins.mnemonic, ins.op_str) != pair:
            raise ValueError(f'native anchor changed at {rva:x}')
        checks.append(code.row(ins))
    selector = view.read(0x52e514, 111)[0]  # kind8 - 8
    target = struct.unpack_from('<I', view.read(0x52e47c, 4*(selector+1)), 4*selector)[0]
    if target != 0x52a48a:
        raise ValueError('kind-8 jump table changed')
    reader_types = struct.unpack('<10I', view.read(0x22f57c, 40))
    if reader_types != (0x22f2bc,0x22f2c6,0x22f2ce,0x22f2c6,0x22f2ce,
                        0x22f2c6,0x22f2d6,0x22f2de,0x22f2de,0x22f2e6):
        raise ValueError('native reader width table changed')
    windows = []
    for start, end in ((0x52a320,0x52a360), (0x52a48a,0x52a891),
                       (0x52a891,0x52a8fa), (0x52a962,0x52aa40),
                       (0x52b081,0x52b0a1), (0x22d0c0,0x22d120)):
        raw = view.read(start, end-start)
        windows.append(dict(start=start, end=end, sha256=sha256(raw).hexdigest(),
                            instructions=[code.row(i) for i in code.dis.disasm(raw, BASE+start)]))
    return dict(client_sha256=sha256(view.raw).hexdigest(), inputs=view.inputs,
                anchors=checks, kind8_dispatch=target, windows=windows,
                conclusion='The first stream u32 is used to seed a 624-word recurrence; not a skill ID.',
                limitation='Byte-only static model; no live execution, no native API or game process.')


def saved_samples():
    """Only small saved raw fixtures, no capture scan or label-based filtering."""
    paths = [ROOT/'outputs/gap_object_records_v36_20260908/0821黑典女王_雷神.json',
             ROOT/'tests/data/direct_result_fixed_field_anchors.json',
             ROOT/'tests/data/compact_entity_selectors_raw.json']
    hashes, samples = {}, []

    def visit(value, context, direction='server_to_client'):
        if isinstance(value, dict):
            direction = value.get('direction', direction)
            for key in ('wire', 'wire_hex', 'payload_hex'):
                if direction == 'server_to_client' and isinstance(value.get(key), str):
                    try:
                        raw = bytes.fromhex(value[key])
                    except ValueError:
                        continue
                    if len(raw) >= 21:
                        samples.append((context, raw))
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    visit(child, f'{context}/{key}', direction)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f'{context}/{index}', direction)

    for path in paths:
        raw = path.read_bytes()
        hashes[str(path)] = sha256(raw).hexdigest()
        visit(json.loads(raw), path.name)
    # Read a pinned literal; do NOT import/execute this legacy review script.
    path = ROOT/'outputs/v27_udp_integrity_review_20260908/review_udp_integrity.py'
    raw = path.read_bytes()
    hashes[str(path)] = sha256(raw).hexdigest()
    candidates = []
    for node in ast.parse(raw.decode('utf-8')).body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='REAL_0877' for t in node.targets)
                and isinstance(node.value, ast.Call) and ast.unparse(node.value.func)=='bytes.fromhex'
                and len(node.value.args)==1 and isinstance(node.value.args[0], ast.Constant)):
            candidates.append(bytes.fromhex(node.value.args[0].value))
    if len(candidates) != 1:
        raise ValueError('expected one explicit saved real-0877 literal')
    samples.append(('real_0877_saved_literal', candidates[0]))
    return hashes, samples


def run_audit():
    from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
    evidence = native_evidence()
    inputs, samples = saved_samples()
    rows, rejections, seen = [], [], set()
    total_before = total_after = 0
    for origin, wire in samples:
        digest = sha256(wire).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        inventory = inspect_udp_damage_records(wire, 'server_to_client')
        if not inventory.complete:
            rejections.append(dict(origin=origin, sha256=digest, reason=inventory.rejection_reason))
            continue
        for old in inventory.damage_records:
            parsed = parse_record(old.record_bytes)
            expected_branch = 0 if old.family=='direct37' else 1
            same = (parsed.source_id==old.source_actor_id and parsed.damage_i64==old.damage
                    and bool(parsed.result_flags_i8 & 1)==old.critical)
            if old.family == 'direct37':
                same = same and parsed.indexed_object_id==old.damage_object_id and parsed.uid_branch==0
            else:
                same = same and parsed.uid_branch!=0 and parsed.weapon_uid==int.from_bytes(old.opaque_object_context,'little')
            total_before += old.damage
            total_after += parsed.damage_i64
            rows.append(dict(origin=origin, wire_sha256=digest, record_offset=old.record_offset,
                record_sha256=old.wire_record_sha256, target_id=old.target_actor_id,
                old_family=old.family, legacy_group=old.damage_result_group_id,
                expected_uid_branch=expected_branch, native=asdict(parsed), physical_fields_unchanged=same,
                skill_certified=False))
    groups = defaultdict(list)
    for row in rows:
        groups[row['legacy_group']].append(row['native']['random_seed_u32'])
    collisions = {str(k): sorted(set(v)) for k,v in groups.items() if len(set(v))>1}
    result = dict(schema='native-kind8-v46', native_evidence=evidence, inputs=inputs,
                  sampled_unique_messages=len(seen), rows=rows, rejected_messages=rejections,
                  summary=dict(records=len(rows), by_family=dict(Counter(r['old_family'] for r in rows)),
                    physical_field_mismatches=sum(not r['physical_fields_unchanged'] for r in rows),
                    total_damage_before=total_before,total_damage_after=total_after,
                    legacy_group_collisions_with_distinct_native_seeds=len(collisions),
                    newly_certified_hits=0, production_changes=False),
                  seed_collision_examples=collisions,
                  limitations=['Purposive saved fixtures, not all packets/jobs or a new acceptance run.',
                    'Native suffix depends on source runtime state and virtual callbacks; left opaque.',
                    'Weapon UID is not a character ID or a truncated damage object index.',
                    'Receiver ACT field cannot certify the source skill.',
                    'Legacy group field retained unchanged for compatibility; never claimed unique.'])
    if any(not r['physical_fields_unchanged'] for r in rows):
        raise ValueError('native prefix mismatch against physical fixture fields')
    for path, expected in (evidence['inputs'] | inputs).items():
        if sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError(f'input changed while auditing: {path}')
    result['script_sha256'] = sha256(Path(__file__).read_bytes()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'native_damage.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    print(json.dumps(run_audit()['summary'], ensure_ascii=False, indent=2))
