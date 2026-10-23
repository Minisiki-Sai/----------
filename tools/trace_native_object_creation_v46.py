"""Two native kind-9 paths plus constructor-assigned object index evidence.

Static bytes only. No native function execution, ID-counter simulation,
production change, replay, resource/skill inference or object promotion.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT/'outputs/projectile_gap_v46_20260909'


@dataclass(frozen=True)
class CompactPrefix:
    object_index: int
    vector_raw: tuple
    action_index_u32: int
    signal_key_u32: int
    has_extra_vector: int
    extra_vector_raw: str | None
    consumed: int
    suffix_raw: str


def compact_prefix(record):
    if len(record)<7 or record[4]!=9 or int.from_bytes(record[5:7],'little')+7!=len(record):
        raise ValueError('explicit complete kind9 record required')
    at=7
    def take(n):
        nonlocal at
        if at+n>len(record):
            raise ValueError('incomplete compact common prefix')
        value=record[at:at+n];at+=n
        return value
    obj=int.from_bytes(take(4),'little')
    vectors=tuple(take(n).hex() for n in (9,6,6,6))
    action=int.from_bytes(take(4),'little')
    signal=int.from_bytes(take(4),'little')
    flag=take(1)[0]
    extra=take(6).hex() if flag else None
    return CompactPrefix(obj,vectors,action,signal,flag,extra,at-7,record[at:].hex())


def evidence():
    from tools.review_client_damage_types_v44 import StaticView,CodeReview,BASE
    view=StaticView();code=CodeReview(view)
    nodes={}
    for start in (0x98f7d0,0x993d80,0xc69eb0,0x764e40,0x75ced0,0xa8d190):
        function=view.function(start)
        if function is None or function[0]!=start or function[1]-start>16384:
            raise ValueError('unexpected bounded function interval')
        raw=view.read(start,function[1]-start)
        # This function ends with two explicitly referenced jump tables.
        # They are data, not instructions; do not disassemble through them.
        code_end=0x991d5c if start==0x98f7d0 else function[1]
        rows=[code.row(i) for i in code.dis.disasm(view.read(start,code_end-start),0x140000000+start)]
        if sum(len(bytes.fromhex(i['bytes'])) for i in rows)!=code_end-start:
            raise ValueError('incomplete static decode')
        nodes[start]=dict(runtime_function=function,sha256=sha256(raw).hexdigest(),instructions=rows,
                          code_end=code_end,undisassembled_table_bytes=function[1]-code_end)
    def row(at):
        return next(r for n in nodes.values() for r in n['instructions'] if r['rva']==at)
    expected={
        0x990f37:('call','0x140993d80'),
        0x990f67:('call','0x140a90370'),
        0x993dd5:('lea','rdx, [rsp + 0x60]'),
        0x993dde:('call','0x14022f260'),
        0x993dec:('mov','r9d, 9'),
        0x993e09:('mov','r9d, 7'),
        0x993e26:('mov','r9d, 7'),
        0x993e43:('mov','r9d, 7'),
        0x993e60:('lea','rdx, [rsp + 0x44]'),
        0x993e74:('lea','rdx, [rsp + 0x48]'),
        0x993e9b:('cmp','byte ptr [rsp + 0x40], 0'),
        0x993ea8:('mov','r9d, 7'),
        0x993ed4:('mov','edx, dword ptr [rsp + 0x44]'),
        0x993ed8:('call','0x14004ef30'),
        0x993ee6:('mov','r9, qword ptr [rax + 0x80]'),
        0x993f14:('movsx','eax, word ptr [rdx + 0xa]'),
        0x993f18:('cmp','eax, r10d'),
        0x993f74:('cmp','word ptr [rdx + 8], 0xf'),
        0x993f7f:('mov','rdx, qword ptr [rdx + 0x18]'),
        0x994052:('call','0x140a8dea0'),
        0x994063:('mov','edx, dword ptr [rsp + 0x60]'),
        0x99406a:('call','0x140c74540'),
        0xc69ecf:('movzx','r12d, r8b'),
        0xc6a487:('test','r12b, r12b'),
        0xc6a48a:('je','0x140c6a63b'),
        0xc6a508:('mov','dword ptr [r15 + 0x1730], edi'),
        0xc6a5c7:('mov','dword ptr [rax + 0x20], edi'),
        0xc6a605:('movups','xmmword ptr [rcx + 0x28], xmm0'),
        0xa8d1ea:('movzx','r8d, dil'),
        0xa8d1f5:('call','0x140c69eb0'),
    }
    checks=[]
    for at,pair in expected.items():
        r=row(at)
        if (r['mnemonic'],r['operands'])!=pair:
            raise ValueError(f'changed native anchor {at:x}')
        checks.append(r)
    counter_sites=(0x764ecb,0xc6a490,0xc6a612,0xc6a630,0x75d853,0x75d861,0x75da9b,0x75daa9)
    for at in counter_sites:
        if row(at).get('rip_relative_targets') != [0x1191f28]:
            raise ValueError('counter reference mismatch')
    if not row(0x764ecb)['operands'].endswith(', 0x186a0'):
        raise ValueError('counter initialization changed')
    if row(0xc6a541).get('rip_relative_targets')!=[0x1191f30]:
        raise ValueError('constructor and explicit registration maps differ')
    indexes=view.read(0x991db0,100)
    targets=struct.unpack('<21I',view.read(0x991d5c,84))
    dispatch={kind:targets[indexes[kind-1]] for kind in (9,56)}
    if dispatch!={9:0x990f2d,56:0x990f41}:
        raise ValueError('subclass dispatch changed')
    return dict(client_sha256=sha256(view.raw).hexdigest(),inputs=view.inputs,nodes=nodes,
                anchors=checks,counter_sites=[row(a) for a in counter_sites],
                counter_rva=0x1191f28,initial_counter_value=100000,
                object_map_rva=0x1191f30,dispatch=dispatch,
                caveats=['Constructor counter proves another native registration route, not a captured skill mapping.',
                 'Counter runtime contents are not read or zero-filled; initialization context is not fully classified.',
                 'No claim that every weapon-map object is a Projectile or one unique skill.',
                 'Static dispatch is class-dependent; do not decode every kind9 as one universal format.'])


def fixture_audit():
    from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
    path=ROOT/'tests/data/compact_entity_selectors_raw.json'
    raw=path.read_bytes();cases=json.loads(raw)['cases'];rows=[]
    for case in cases:
        reg=case['registration']
        wire=bytes.fromhex(reg['wire'])
        inv=inspect_udp_damage_records(wire,'server_to_client')
        if not inv.complete:
            raise ValueError('saved positive is not completely framed')
        candidates=[r for r in inv.typed_records if r.record_offset==reg['marker']]
        if len(candidates)!=1:
            raise ValueError('saved positive record ambiguity')
        parsed=compact_prefix(candidates[0].record_bytes)
        matches=(parsed.object_index==reg['object'] and parsed.action_index_u32==reg['action']
                 and parsed.signal_key_u32==reg['signal'])
        if not matches:
            raise ValueError('compact prefix differs from saved structural evidence')
        rows.append(dict(capture=case['capture'],template=case['template'],frame=reg['frame'],
                         wire_sha256=sha256(wire).hexdigest(),parsed=asdict(parsed),matches=matches))
    return dict(input=str(path),sha256=sha256(raw).hexdigest(),rows=rows,
                new_confirmed_hits=0,production_changed=False)


def main():
    result=dict(schema='native-object-creation-v46',native=evidence(),fixtures=fixture_audit(),
                script_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    for path,digest in result['native']['inputs'].items():
        if sha256(Path(path).read_bytes()).hexdigest()!=digest:
            raise ValueError('input changed during audit')
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'object_creation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(nodes=len(result['native']['nodes']),anchors=len(result['native']['anchors']),
                          compact_records=len(result['fixtures']['rows']),new_confirmed_hits=0),ensure_ascii=False))


if __name__=='__main__':
    main()
