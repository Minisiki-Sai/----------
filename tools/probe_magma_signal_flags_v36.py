"""Bounded, read-only ACT6 signal audit; stdlib, two fixed ACTs, no runtime.

All command output goes to stdout. Artifacts are installed separately with
apply_patch. Unknown flag values never grant reachability or disable a signal.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
OLD = ROOT / 'outputs/v26_magmawall_family_closure_20260908'
V28 = ROOT / 'outputs/magma_exact_route_v28_20260908/static_evidence.json'
OUT = ROOT / 'outputs/magma_signal_flags_v36_20260908'
BASE = 'Skill_MagmaWall'
NORMAL = 'Skill_MagmaWall_EX'
CLONE = 'Skill_MagmaWall_EX_Clone[1]'
ALCHEMIST = 'char/player/academic/academic_skill_1st_alchemist.act'
ADEPT = 'char/player/academic/academic_skill_2st_adept.act'
FILES = {
    ALCHEMIST: (1242168, '4f57dcfa0d8dde1f9390137ecfe97a6876203c4ce508f7d65703cb8d17ab4498'),
    ADEPT: (442615, '838a8fd6ed59bb762d0d0a37b52f92fb49173ed566c79c9a75d420ed3e26b120'),
}
ROOTS = {
    BASE: (ALCHEMIST, 320735, 333332),
    NORMAL: (ADEPT, 122354, 135641),
    CLONE: (ADEPT, 135641, 148191),
}
OLD_SIGNAL_KEYS = ('offset', 'type', 'start', 'end', 'order', 'u32', 'tables', 'strings')
OLD_ACTION_KEYS = ('offset', 'name', 'animation', 'frames', 'next_action', 'blend',
                   'next_frame', 'end_offset', 'span_sha256')


class AuditError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise AuditError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def load_json(path):
    require(path.stat().st_size <= 8_000_000, f'oversized index: {path}')
    data = path.read_bytes()
    return json.loads(data.decode('utf-8-sig')), sha(data)


class Reader:
    def __init__(self, data, base=0):
        self.data, self.pos, self.base = data, 0, base

    @property
    def offset(self):
        return self.base + self.pos

    def take(self, size):
        require(0 <= size <= len(self.data) - self.pos,
                f'truncated or invalid size {size} at {self.offset}')
        start = self.pos
        self.pos += size
        return self.data[start:self.pos]

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]

    def count(self):
        value = self.i32()
        require(0 <= value <= 65536, f'invalid count {value} at {self.offset - 4}')
        return value

    def string(self):
        raw = self.take(self.count()).rstrip(b'\0')
        for encoding in ('utf-8', 'gb18030', 'latin-1'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                pass


def parse_signal(r):
    start = r.pos
    s = dict(offset=r.offset, type=r.i32(), start=r.i32(), end=r.i32(), order=r.i32())
    s['payload_offset'] = r.offset
    s['u32'] = list(struct.unpack('<64I', r.take(256)))
    s['tables'], s['table_layout'] = {}, []
    for width, label in ((8, 'vec2'), (12, 'vec3'), (16, 'vec4')):
        off = r.offset
        count = r.count()
        indexes = [r.i32() for _ in range(count)]
        values = [list(struct.unpack('<' + 'f' * (width // 4), r.take(width))) for _ in range(count)]
        s['table_layout'].append(dict(label=label, offset=off, count=count, end_offset=r.offset))
        if count:
            s['tables'][label] = [[key, value] for key, value in zip(indexes, values)]
    s['strings_table_offset'] = r.offset
    count = r.count()
    indexes = [r.i32() for _ in range(count)]
    s['strings'], s['string_layout'] = [], []
    for index in indexes:
        off = r.offset
        value = r.string()
        s['strings'].append([index, value])
        s['string_layout'].append(dict(index=index, offset=off, end_offset=r.offset))
    s['tail_offset'] = r.offset
    s['tail_u8'] = r.take(1)[0]
    s['tail_semantics'] = 'UNKNOWN'
    s['end_offset'] = r.offset
    s['span_sha256'] = sha(r.data[start:r.pos])
    return s


def parse_action(r):
    start = r.pos
    a = dict(offset=r.offset, name=r.string(), animation=r.string(), frames=r.i32(),
             next_action=r.string(), blend=r.i32(), next_frame=r.i32())
    # Already investigated in v28; retain for byte accounting, not a new route.
    a['settings_offset'] = r.offset
    a['settings_raw_hex'] = r.take(14).hex()
    a['settings_semantics'] = 'opaque; prior non-discriminator, not a new finding'
    a['signal_count_offset'] = r.offset
    count = r.count()
    a['signals'] = [parse_signal(r) for _ in range(count)]
    a['end_offset'] = r.offset
    a['span_sha256'] = sha(r.data[start:r.pos])
    return a


def parse_act6(data):
    require(len(data) <= 2_000_000, 'ACT exceeds two-MB bound')
    r = Reader(data)
    header = r.take(32)
    require(header.startswith(b'Eternity Action File'), 'wrong signature')
    version = r.i32()
    require(version == 6, 'only ACT6 is audited')
    count = r.count()
    actions = [parse_action(r) for _ in range(count)]
    require(r.pos == len(data), 'trailing ACT bytes')
    return dict(version=version, header_raw_hex=header.hex(), actions=actions,
                end_offset=r.offset, eof=True)


def old_projection(a):
    result = {key: a[key] for key in OLD_ACTION_KEYS}
    result['signals'] = [{key: s[key] for key in OLD_SIGNAL_KEYS} for s in a['signals']]
    return result


def verify_against_old(a, old):
    expected = {key: old[key] for key in (*OLD_ACTION_KEYS, 'signals')}
    require(old_projection(a) == expected, f'old action drift: {old["name"]}')


def tail_assessment(actions):
    """An observation, not an engine predicate, even when a byte equals 0/1."""
    flags = {a['name']: sorted({s['tail_u8'] for s in a['signals'] if s['type'] == 48})
             for a in actions if a['name'] in ROOTS}
    return dict(generator_tail_values=flags,
                normal_clone_values_differ=flags.get(NORMAL) != flags.get(CLONE),
                tail_semantics='UNKNOWN', disabled_signal_offsets=[],
                clone_reachable=None, clone_unreachable_proven=False,
                family_32022_certified=False, confirmed_new_hits=0,
                status='HOLD_UNKNOWN_FLAG_AND_ENTRY_SEMANTICS')


def literal_positions(data, value):
    result, start = [], 0
    while (start := data.find(value, start)) >= 0:
        result.append(start)
        start += 1
    return result


def analyze():
    old_act, act_hash = load_json(OLD / 'act_closure.json')
    old_static, static_hash = load_json(V28)
    tables, tables_hash = load_json(OLD / 'table_closure.json')
    require(old_act['manifest_count'] == 7776 and not old_act['parsed_errors'], 'old global scan changed')
    expected_roots = {a['name']: a for a in old_static['exact_actions'] if a['name'] in ROOTS}
    roots, files, inbound, nonzero, key_candidates = [], [], [], [], []
    byte_count = 0
    for rel, (expected_size, expected_hash) in FILES.items():
        path = RESOURCE / rel
        require(path.stat().st_size == expected_size, f'resource size drift: {rel}')
        data = path.read_bytes()
        byte_count += len(data)
        require(sha(data) == expected_hash, f'resource hash drift: {rel}')
        manifest = next(row for row in old_act['referenced_manifest'] if row['path'] == rel)
        require(manifest['sha256'] == expected_hash, f'old manifest drift: {rel}')
        parsed = parse_act6(data)
        selected = [a for a in parsed['actions'] if a['name'] in ROOTS]
        for a in selected:
            require((rel, a['offset'], a['end_offset']) == ROOTS[a['name']], 'root boundary drift')
            verify_against_old(a, expected_roots[a['name']])
            roots.append(dict(path=rel, raw_hex=data[a['offset']:a['end_offset']].hex(), **a))
        flags = Counter()
        for a in parsed['actions']:
            if a['next_action'] == CLONE:
                inbound.append(dict(path=rel, source=a['name'], offset=a['offset'], kind='next_action'))
            for s in a['signals']:
                flags[str(s['tail_u8'])] += 1
                if s['tail_u8']:
                    nonzero.append(dict(path=rel, action=a['name'], offset=s['offset'],
                                        type=s['type'], tail_offset=s['tail_offset'], tail_u8=s['tail_u8']))
                for index, value in s['strings']:
                    if value == CLONE:
                        inbound.append(dict(path=rel, source=a['name'], offset=s['offset'],
                                            kind='string_candidate', type=s['type'], string_index=index))
                    if a['name'] in ROOTS and index == 2 and s['type'] == 29:
                        key_candidates.append(dict(path=rel, source=a['name'], offset=s['offset'],
                            type=29, string_index=index, value=value, tail_u8=s['tail_u8'],
                            previously_read=True, direction='outgoing', dispatch_semantics='UNKNOWN'))
        files.append(dict(path=rel, size=len(data), sha256=sha(data), version=6,
            actions=len(parsed['actions']), signals=sum(flags.values()), tail_distribution=dict(flags),
            eof=parsed['eof'], header_raw_hex=parsed['header_raw_hex'],
            clone_literal_offsets=literal_positions(data, CLONE.encode('ascii'))))
    require({a['name'] for a in roots} == set(ROOTS), 'missing or unexpected root')
    generators = []
    for a in roots:
        for s in a['signals']:
            if s['type'] == 48:
                generators.append(dict(action=a['name'], path=a['path'], offset=s['offset'],
                    template=s['u32'][0], start=s['start'], end=s['end'], order=s['order'],
                    tail_offset=s['tail_offset'], tail_u8=s['tail_u8'],
                    end_offset=s['end_offset'], span_sha256=s['span_sha256'],
                    within_declared_frames=0 <= s['start'] < s['end'] <= a['frames']))
    relevant_levels = [row for row in tables['levels'] if row['row'].get('_ProcessParam1') in ROOTS]
    sources = {}
    for rel in ('tools/build_attack_instance_catalog.py',
                'tools/audit_remaining_lineage_five_jobs_act_transitions.py',
                'tools/probe_magma_exact_route_v28.py',
                'outputs/v26_magmawall_family_closure_20260908/proposal.py'):
        data = (ROOT / rel).read_bytes()
        lines = data.decode('utf-8-sig').splitlines()
        terms = ('per-signal flag', 'version>=6:r.u8()', 'if version >= 6:',
                 'unbound_or_foreign_creator', 'ACT.bt:')
        matches = [dict(line=i, text=line.strip()) for i, line in enumerate(lines, 1)
                   if any(term in line for term in terms)]
        sources[rel] = dict(sha256=sha(data), relevant_lines=matches)
    return dict(schema='magma-signal-flags-v36', scope=dict(resource_files_read=2,
        resource_bytes_read=byte_count, resource_enumeration=False, capture_reads=0,
        production_imports=False, writes=False, note='Counters per analyze invocation; repeat verification rereads the same two files.'),
        inherited=dict(manifest_count=old_act['manifest_count'], parsed_count=old_act['parsed_count'],
            root_action_inbound=old_act['root_action_inbound'],
            clone_dnt_bindings=[row for row in relevant_levels if row['row']['_ProcessParam1'] == CLONE]),
        index_hashes=dict(act_closure=act_hash, static_v28=static_hash, table_closure=tables_hash),
        source_audit=sources, files=files, nonzero_tail_signals=nonzero, roots=roots,
        generators=generators, selected_file_clone_inbound=inbound,
        root_type29_keys=key_candidates, root_dnt_metadata=relevant_levels,
        result=tail_assessment(roots), limitations=[
            '0/1 or matching tails have no established enable/disable/condition meaning.',
            'No reference found is not a proof of global unreachability.',
            'All old root fields agree, including all numeric slots and indexed strings.',
            'Root settings are retained for byte accounting, not rediscovered as a selector.',
            'Unknown numeric dispatch and engine/evaluator semantics remain unaudited.',
            'No birth-specific creator link or direct-hit upgrade is supplied.'])


def verify_saved(evidence):
    """Reopen the two fixed resources and compare the entire reproducible audit."""
    require(evidence == analyze(), 'saved evidence differs from independent resource audit')
    return dict(status='PASS', scope='byte/field inventory only',
                clone_unreachable_proven=False, confirmed_new_hits=0,
                evidence_sha256=sha(json_bytes(evidence)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('audit', 'verify'))
    args = parser.parse_args()
    if args.mode == 'audit':
        result = analyze()
    else:
        evidence, _ = load_json(OUT / 'evidence.json')
        result = verify_saved(evidence)
    sys.stdout.buffer.write(json_bytes(result))


if __name__ == '__main__':
    main()
