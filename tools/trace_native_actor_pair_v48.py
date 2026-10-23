"""Bounded offline Player pair consumer audit; no native execution/state replay.

The tiny arithmetic model is restricted to visible countdown/accumulator code.
It is not an engine, action namespace resolver, or skill certification rule.
Only actor_pair.json/md in the fixed sidecar output directory are generated.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.review_client_damage_types_v44 import StaticView, CodeReview, BASE, capstone

OUT=ROOT/'outputs/native_wire_v47_20260909'
V44_SHA='5cb4a393f7b7bab7001aaa36991fad25035d2a71968ae002253bdba0d279a5de'
SUFFIX_SHA='3a8299773010ce70da83a559981dc478edf709f26ba5e50c8e4dd9b1500bd153'
RANGES=(0xA5CDB0,0x657F80,0x657960,0x826B10,0x6575B0,0xC43470)
BOUNDED={0x81D930:27,0x8CB580:12}
REUSE=(0x52A320,0x8CB550,0xA56AB0,0x53DFD0,0x46620)


def digest(raw):return sha256(raw).hexdigest()


def f32(x):
    try:return struct.unpack('<f',struct.pack('<f',x))[0]
    except (OverflowError,struct.error) as exc:raise ValueError('outside finite float32 model') from exc


@dataclass(frozen=True)
class PairState:
    combo_value: int
    remaining_milliunits: int


def advance_pair(state,delta):
    """A5cf48..8c arithmetic, under finite nonnegative delta/normal SSE rounding.

    A5cdcd saves incoming XMM2. Its caller/time units are not modeled. Countdown
    scale is x1000; UI applies x0.001. Native overflow/NaN behavior is excluded.
    Callback happens BEFORE the pair is cleared; callback side effects omitted.
    """
    if (any(type(x) is not int or not -0x80000000<=x<=0x7fffffff
            for x in (state.combo_value,state.remaining_milliunits))
            or not math.isfinite(delta) or delta<0):
        raise ValueError('outside bounded int32/nonnegative-delta model')
    remaining=state.remaining_milliunits
    decrement=0
    if remaining>0:
        scaled=f32(f32(delta)*1000.0)
        if not math.isfinite(scaled) or scaled>=0x80000000:
            raise ValueError('CVTTSS2SI overflow excluded')
        decrement=math.trunc(scaled)
        remaining-=decrement
        if remaining < -0x80000000:raise ValueError('native subtraction overflow excluded')
    expired=remaining<0 or remaining==0 and state.combo_value>0
    return dict(state=PairState(0,0) if expired else PairState(state.combo_value,remaining),
                decrement=decrement,callback_called=expired,
                callback_value=state.combo_value if expired else None,skill_certified=False)


def ui_scaled_lifetime(pair_second):
    if type(pair_second) is not int or not -0x80000000<=pair_second<=0x7fffffff:
        raise ValueError('signed32 UI input required')
    return f32(f32(pair_second)*f32(0.001))


def damage_accumulator(old_total,damage,old_value,new_value,*,runtime_gate,already_counted=False):
    """Selected52aa paths ONLY, with identity/type/local-player gates supplied.

    This is a local UI combo aggregate, not a skill subtotal, object ancestry,
    or proof that any particular entity action produced a hit.
    old_value is the SOURCE virtual getter result; in the low-source linked
    route it is NOT the linked setter recipient's previously stored value.
    """
    if type(runtime_gate) is not bool or type(already_counted) is not bool:
        raise ValueError('explicit runtime branch facts required')
    if (any(type(x) is not int or not -0x8000000000000000<=x<=0x7fffffffffffffff
            for x in (old_total,damage))
            or any(type(x) is not int or not -0x80000000<=x<=0x7fffffff
                   for x in (old_value,new_value))):
        raise ValueError('bounded signed64 damage/signed32 pair inputs required')
    if not runtime_gate or already_counted or damage<=0 or new_value<=old_value:
        return old_total
    value=damage+(old_total if new_value>1 else 0)
    if not -0x8000000000000000<=value<=0x7fffffffffffffff:
        raise ValueError('native signed64 overflow excluded')
    return value


ANCHORS=(
    (0x53DFD0,'mov','eax, dword ptr [rcx + 0x1344]'),
    (0xA56AB0,'mov','dword ptr [rcx + 0x1344], edx'),
    (0xA56AB6,'mov','dword ptr [rcx + 0x1348], r8d'),
    (0x52ADBB,'mov','rcx, rbx'),
    (0x52ADBE,'call','qword ptr [rax + 0x1b0]'),
    (0x52ADC8,'cmp','ecx, eax'),
    (0x52ADCA,'jle','0x14052adf2'),
    (0x52ADCF,'jne','0x14052adf2'),
    (0x52AE15,'mov','rcx, r14'),
    (0x52AE18,'call','qword ptr [rax + 0x1a8]'),
    (0xA5CDCD,'movaps','xmm6, xmm2'),
    (0xA5CDD8,'xor','r14d, r14d'),
    (0xA5CF48,'mov','ecx, dword ptr [rdi + 0x1348]'),
    (0xA5CF50,'jle','0x140a5cf6b'),
    (0xA5CF55,'mulss','xmm0, dword ptr [rip + 0x55a187]'),
    (0xA5CF5D,'cvttss2si','eax, xmm0'),
    (0xA5CF61,'sub','ecx, eax'),
    (0xA5CF63,'mov','dword ptr [rdi + 0x1348], ecx'),
    (0xA5CF6B,'js','0x140a5cf7a'),
    (0xA5CF71,'cmp','dword ptr [rdi + 0x1344], r14d'),
    (0xA5CF80,'mov','edx, dword ptr [rdi + 0x1344]'),
    (0xA5CF86,'call','qword ptr [rax + 0x2d8]'),
    (0xA5CF8C,'mov','qword ptr [rdi + 0x1344], r14'),
    (0x81D930,'test','edx, edx'),
    (0x81D932,'jle','0x14081d94a'),
    (0x81D93E,'mov','rcx, qword ptr [rcx + 0x1008]'),
    (0x657F98,'mov','r14d, r8d'),
    (0x657F9B,'mov','esi, edx'),
    (0x65809A,'mov','r8d, r14d'),
    (0x65809D,'mov','edx, esi'),
    (0x65796D,'movsxd','rdx, edx'),
    (0x65797D,'call','qword ptr [rax + 0x130]'),
    (0x65799B,'mulss','xmm0, dword ptr [rip + 0x95f50d]'),
    (0x6579A3,'movss','dword ptr [rdi + 0x5d0], xmm0'),
    (0x6575C8,'lea','rax, [rip + 0x87d321]'),
    (0x6575D8,'mov','qword ptr [rbx], rax'),
    (0x826B25,'mov','rdx, qword ptr [rbx + 0x2660]'),
    (0x826B3E,'mov','qword ptr [rbx + 0x2660], 0'),
    (0xC4348C,'mov','rdi, rdx'),
    (0xC4355E,'mov','rdx, rdi'),
)
CALLS=((0x81D945,0x657F80,'jmp'),(0x658009,0x6575B0,'call'),
       (0x6580A2,0x657960,'call'),(0x8CB587,0x826B10,'jmp'),
       (0x826B39,0xC43470,'call'),(0xC43561,0x69E000,'call'))


FACTS=(
    ('P1','Player+1348 is a decrementing lifetime: incoming update XMM2 is preserved, multiplied by1000, truncated toward zero, and subtracted. Negative remainder or zero with positive+1344 calls virtual+2d8 then clears both dwords. This is not the behavior of an immutable ACT ordinal/skill ID.',
     [0xA5CDCD,0xA5CF48,0xA5CF55,0xA5CF5D,0xA5CF61,0xA5CF6B,0xA5CF71,0xA5CF80,0xA5CF86,0xA5CF8C]),
    ('P2','LocalPlayer setter8cb550 forwards+1344/+1348 through81d930 and657f80 into657960. First value goes to the inner object virtual+130; second is float-converted and scaled by0.001 into+5d0. Positive-first-value gate occurs at81d930. No ACT action-array lookup is on this reviewed chain.',
     [0x8CB55E,0x8CB565,0x8CB577,0x81D930,0x81D945,0x657F98,0x657F9B,0x65809A,0x65809D,0x6580A2,0x65796D,0x65797D,0x65799B,0x6579A3]),
    ('P3','657f80 allocates0x5e0 and calls6575b0 before passing the pair. Constructor writes vtableed48f0; its validated primary COL/type descriptor namesCDnComboDlg. RTTI and the actual setter/timing dataflow together support a combo-display count/lifetime interpretation, not a string-only skill claim.',
     [0x657FE0,0x658009,0x6575C8,0x6575D8,0x6580A2]),
    ('P4','Reviewed Player virtual+2d8 is a no-op; LocalPlayer+2d8 selects8cb580->826b10. If manager+25b8 and aggregate+2660 are nonzero, it sends manager+2660 toC43470 then clears it. A null display recipient returns without clearing a nonzero aggregate. C43470 forwards the quantity to69e000; formatting there is unreviewed.',
     [0xA5CF86,0x8CB580,0x8CB587,0x826B19,0x826B23,0x826B25,0x826B2F,0x826B39,0x826B3E,0xC4348C,0xC4355E,0xC43561]),
    ('P5','In the real52aa branch, incoming first value is compared against the+1b0 getter. Under the identity/local-player/positive-damage guards, a greater value adds damage to manager+2660 when value>1, otherwise starts the aggregate with current damage. It then sets the pair. Together with expiry this supports combo state, not an action sequence identifier.',
     [0x52AC4E,0x52AC54,0x52AC58,0x52AC5A,0x52AC5C,0x52AC63,0x52AC67,0x52AC6C,0x52AC73,0x52ACA2]),
    ('P6','The low-source linked-player route uses+1470/+1478, calls the linked object+1a8 at52ae18, and can update the local player aggregate. Entity ACT/action/signal identity is not supplied by the pair. The independent state19 path remains a distinct alternative; a20-byte suffix cannot choose the branch.',
     [0x52ACF4,0x52ACFB,0x52AD3B,0x52AD62,0x52AD83,0x52AD88,0x52AD91,0x52ADBE,0x52AE0C,0x52AE11,0x52AE15,0x52AE18,0x52AE53]),
    ('P7','Do not merge getter and setter recipients in the low-source route:52adbb passes original SOURCE RBX to virtual+1b0, whereas52ae15 passes linked R14 to virtual+1a8. Known Monster primary+1b0 points to57a40, not Player getter53dfd0;57a40 body is not newly reviewed. The greater-than comparison here is against source virtual result, NOT necessarily linked Player+1344. Same slot offset is not same implementation/state.',
     [0x52AA40,0x52ACF4,0x52ACFB,0x52ADB8,0x52ADBB,0x52ADBE,0x52ADC8,0x52ADCA,0x52ADCF,0x52AE09,0x52AE15,0x52AE18]),
)


def native(view,prior):
    code=CodeReview(view); nodes=[]; rows={}
    for start in RANGES:
        f=view.function(start)
        if not f or f[0]!=start or f[1]-start>8192:raise ValueError('unexpected exception range')
        raw=view.read(start,f[1]-start);ins=[code.row(i) for i in code.decode(f)]
        if sum(len(bytes.fromhex(i['bytes'])) for i in ins)!=len(raw):raise ValueError('incomplete selected decode')
        nodes.append(dict(start=start,runtime_function=f,size=len(raw),sha256=digest(raw),instructions=ins,
            provenance=view.provenance(start,len(raw)),
            exception_record_rva=view.exception['rva']+view.starts.index(start)*12))
        rows.update((i['rva'],i) for i in ins)
    for start,size in BOUNDED.items():
        if view.function(start) is not None:raise ValueError('unexpected thunk unwind record')
        raw=view.read(start,size);ins=[code.row(i) for i in code.dis.disasm(raw,BASE+start)]
        if sum(len(bytes.fromhex(i['bytes'])) for i in ins)!=size:raise ValueError('incomplete bounded sample')
        nodes.append(dict(start=start,runtime_function=None,size=size,sha256=digest(raw),raw=raw.hex(),
            instructions=ins,provenance=view.provenance(start,size),
            boundary='explicit direct/vtable-target sample, ending at return or tail jump; no invented function extent'))
        rows.update((i['rva'],i) for i in ins)
    if len(nodes)!=8 or sum(n['size'] for n in nodes)>16384:raise ValueError('bounded node budget')
    reused=[]
    for start in REUSE:
        node=next(x for x in prior['native']['nodes'] if x['start']==start)
        size=node.get('size',node.get('range_size'))
        if digest(view.read(start,size))!=node['sha256']:raise ValueError('prior suffix code bytes changed')
        rows.update((i['rva'],i) for i in node['instructions']);reused.append(node)
    anchors=[]
    for at,mnemonic,operands in ANCHORS:
        row=rows.get(at)
        if row is None or (row['mnemonic'],row['operands'])!=(mnemonic,operands):
            raise ValueError(f'anchor changed {at:#x}')
        anchors.append(row)
    for at,target,mnemonic in CALLS:
        row=rows[at]
        if row['mnemonic']!=mnemonic or row.get('direct_branch_target')!=target:raise ValueError('call chain changed')
        anchors.append(row)
    manager_sites=(0x52AC5C,0x52ADD1,0x8CB56B,0x8CB580)
    if any(rows[a].get('rip_relative_targets')!=[0x118DAA8] for a in manager_sites):
        raise ValueError('setter/accumulator/expiry manager global is not identical')
    constants=[]
    for at,expected in ((0xFB70E4,1000.0),(0xFB6EB0,f32(0.001))):
        raw=view.read(at,4)
        if struct.unpack('<f',raw)[0]!=expected:raise ValueError('time scaling constant changed')
        constants.append(dict(rva=at,raw=raw.hex(),float32=expected,provenance=view.provenance(at,4)))
    # A single exact constructor-linked type, not a new RTTI inventory.
    vt,col,td=0xED48F0,0xFF1E68,0x1171E18
    colraw=view.read(col,24);fields=struct.unpack('<6I',colraw)
    name=b'.?AVCDnComboDlg@@\0'
    if (struct.unpack('<Q',view.read(vt-8,8))[0]!=BASE+col
            or fields!=(1,0,0,td,0xFF1E90,col) or view.read(td+16,len(name))!=name):
        raise ValueError('ComboDlg constructor RTTI identity changed')
    rtti=dict(vtable=vt,locator=col,locator_raw=colraw.hex(),type_descriptor=td,
        name=name[:-1].decode(),name_rva=td+16,raw_name=name.hex(),
        locator_provenance=view.provenance(col,24),name_provenance=view.provenance(td+16,len(name)))
    tables=[]
    oldtypes=json.loads(view.load(ROOT/'outputs/client_protection_v44_20260908/damage_types.json'))
    for name,vt,wanted in (('CDnPlayerActor',0xF24310,0x46620),('CDnLocalPlayerActor',0xEFA568,0x8CB580)):
        t=next(x for x in oldtypes['focus_types'] if x['simple_name']==name)
        loc=next(l for l in t['locators'] if l['subobject_offset']==0 and any(v['rva']==vt for v in l['vtables']))
        if view.read(loc['rva'],24).hex()!=loc['raw']:raise ValueError('Player RTTI changed')
        raw=view.read(vt+0x2d8,8)
        if struct.unpack('<Q',raw)[0]!=BASE+wanted:raise ValueError('expiry virtual target changed')
        tables.append(dict(class_name=name,vtable=vt,slot=0x2d8,slot_index=91,target=wanted,
            raw=raw.hex(),locator=loc['rva'],provenance=view.provenance(vt+0x2d8,8)))
    getter_slots=[]
    for slot in prior['native']['class_vtable_slots']:
        if slot['slot_byte_offset']!=0x1b0:continue
        if view.read(slot['slot_rva'],8).hex()!=slot['raw']:
            raise ValueError('reused getter dispatch slot changed')
        expected=0x53DFD0 if slot['class_name'] in ('CDnPlayerActor','CDnLocalPlayerActor') else 0x57A40
        if slot['target']!=expected:raise ValueError('getter class dispatch changed')
        getter_slots.append(slot)
    if len(getter_slots)!=5:raise ValueError('getter dispatch proof inventory changed')
    facts=[dict(id=key,text=text,sites=sites,instructions=[rows[a] for a in sites]) for key,text,sites in FACTS]
    # Cheap raw-target prefilter; results are NOT a proof of all virtual calls.
    base,data=view.segments['.text'];targets=(0x53DFD0,0xA56AB0,0x8CB550,0x81D930)
    hits={hex(t):[] for t in targets}
    for match in re.finditer(b'[\xe8\xe9]',data):
        at=match.start()
        if at+5>len(data):continue
        target=base+at+5+struct.unpack_from('<i',data,at+1)[0]
        if target in targets:hits[hex(target)].append(base+at)
    if hits['0x53dfd0']:raise ValueError('new getter raw direct-call candidate; review scope')
    return dict(nodes=nodes,reused_suffix_nodes=reused,new_node_count=8,
        new_bytes=sum(n['size'] for n in nodes),anchors=anchors,constants=constants,
        rtti=rtti,expiry_virtual_slots=tables,facts=facts,direct_e8_e9_candidates=hits,
        shared_manager_global=dict(rva=0x118DAA8,instructions=[rows[a] for a in manager_sites]),
        getter_dispatch_slots=getter_slots,
        search_limit='E8/E9 target prefilter only. No matches for getter does not exclude virtual/inlined/indirect consumers.')


def saved_pair_examples(view,prior):
    # Recheck all saved low-source record byte hashes, not a capture-wide scan.
    from tools.trace_native_damage_suffix_v48 import compatible_plans
    examples=[]
    for row in prior['samples']['records']:
        if row['source_id']>=100000:continue
        raw=bytes.fromhex(row['record_hex'])
        if digest(raw)!=row['record_sha256']:raise ValueError('saved record hash changed')
        plans=compatible_plans(raw,'CDnActor')
        pairs={tuple(f['value'] for f in p['fields'][:2]) for p in plans}
        if len(pairs)!=1:raise ValueError('conditional pair byte readings disagree')
        pair=next(iter(pairs))
        examples.append(dict(origin=row['origin'],record_sha256=row['record_sha256'],
            source_id=row['source_id'],target_id=row['target_id'],pair=list(pair),
            conditional_plan_count=len(plans),runtime_route_certified=False,
            ui_scaled_duration=ui_scaled_lifetime(pair[1]),entity_action_certified=False))
    if len(examples)!=6 or not any(x['pair']==[6,1500] for x in examples):
        raise ValueError('saved low-source example set changed')
    return dict(examples=examples,
        boundary='Reuses hash-pinned suffix report record bytes; (6,1500) is present in the saved compact fixture. This does not identify it with any separately mentioned new infection packet. Actual runtime branch/class remains unknown.')


LIMITATIONS=[
    'Offline static bytes only. No game/client/DLL loading, native execution, live memory, production changes or skill certification.',
    'Eight selected new nodes: six original exception ranges and two precisely bounded wrappers; five suffix proof nodes reused. An initial thunk discovery read included adjacent8cb5a0 bytes; they are excluded from all evidence/claims. No broader1348 inventory was analyzed.',
    'Global displacement matches included unrelated objects, stack references and relative-call immediates. Only the proven setter/UI/update chain is treated as this Player field.',
    'Calling-frame origin/absolute units of update XMM2 were not traced. x1000 countdown and x0.001 UI scaling support millisecond/second-like units; explicit clock provenance is not asserted.',
    'CDnComboDlg identity is linked through a constructor vtable and validated COL/type name, combined with actual pair flow. Inner UI virtual130 rendering and69e000 formatting are not expanded.',
    'Absence of an E8/E9 direct call to53dfd0 is limited to that encoding scan. Actual52aa consumer is virtual; there may be other virtual/inlined consumers.',
    'Low-source linked-player branch updates the related player, not an entity ACT selector. State19 remains an alternative. Count/time cannot identify an entity action, parent skill, wire skill ID or globally unique cast.',
    'Low-source getter receiver is original SOURCE RBX while setter receiver is linked R14. Monster/Actor/Pet reviewed primary getter slots target57a40, not53dfd0; target body is not newly reviewed. Do not assume source old-value equals linked Player+1344.',
    'Countdown model excludes nonfinite delta, floating-point environment changes and integer overflow. Callback side effects and full game update are deliberately not reproduced.',
    'Six saved low-source rows corroborate raw pairs only; they do not observe runtime owner/class links. Native stream final-section-semantics limitation inherited fromv44 remains.',
]


def build():
    helper=ROOT/'tools/review_client_damage_types_v44.py'
    if digest(helper.read_bytes())!=V44_SHA:raise ValueError('v44 helper changed')
    view=StaticView();raw=view.load(OUT/'damage_suffix.json')
    if digest(raw)!=SUFFIX_SHA:raise ValueError('suffix report changed')
    prior=json.loads(raw)
    for path in (helper,Path(__file__),ROOT/'tests/test_native_actor_pair_v48.py',ROOT/'tools/trace_native_damage_suffix_v48.py'):
        view.load(path)
    evidence=native(view,prior);examples=saved_pair_examples(view,prior)
    changed=[p for p,h in view.inputs.items() if digest(Path(p).read_bytes())!=h]
    if changed:raise ValueError(f'input changed during audit:{changed}')
    return dict(schema='native-actor-pair-v48',client_sha256=digest(view.raw),
        tool_sha256=digest(Path(__file__).read_bytes()),inputs=view.inputs,changed_inputs=changed,
        exception_directory=view.exception,capstone_version=capstone.__version__,
        native=evidence,saved_examples=examples,limitations=LIMITATIONS,
        conclusion='Combo-display value and countdown state strongly supported; no entity-own-action/skill binding.',
        production_changes=False,new_certified_skills=0)


def markdown(r):
    n=r['native']
    lines=['# v48 Player +1344/+1348：Combo 数值与超时消费者','',
        '## 阶段结论','',
        '**有较强实证支持：第一值是此路径的 combo 计数/显示数值，第二值是 combo 超时状态。'
        '未发现可用来认证 entity 自身攻击动作的绑定。**不是仅凭(6,1500)猜测，也不是把RTTI名字单独当作UDP语义。','',
        '## 实际消费链','',
        '1. `a5cf48..a5cf8c` 读取Player+1348，扣除 `trunc(float32(update_delta×1000))`。'
        '余量为负，或为0且+1344为正时，调用虚槽+2d8并清零两个dword。第一值因而与过期窗口一起失效。',
        '2. `8cb550 → 81d930 → 657f80 → 657960` 保留两个参数。第一值传给内部对象虚槽+130；'
        '第二值×0.001存到显示对象+5d0。第一值≤0不会从81d930创建此显示。',
        '3. `657f80 → 6575b0` 的实际构造路径写vtable `ed48f0`；COL `ff1e68` → '
        'type descriptor `1171e18`，名字为 `CDnComboDlg`。这是计时行为和实际传参链之外的独立类型佐证。',
        '4. `52ac4e` 经getter虚槽+1b0取得旧值，`52ac54..52ac73` 在身份/本地玩家/正伤害等门控下，'
        '仅新值更大才累计manager+2660；新值>1则加旧累计，否则从本次伤害起算。',
        '5. LocalPlayer到期槽 `+2d8 → 8cb580 → 826b10 → c43470` 消费同一manager+2660，随后清零。'
        '该消费要求manager+25b8显示接收者与+2660累计均非零；接收者为空会直接返回，不清非零累计。'
        '这与combo累计/到期显示一致，不是按ACT动作索引取技能。','',
        '尺度说明：×1000与×0.001支持毫秒/秒式换算，1500在UI侧成为约1.5；本轮没有追踪外层update_delta的时钟来源。','',
        '## 为什么不能据此认定 entity 自身动作','',
        '小source路径的 `+1470/+1478 → 52ae18` setter接收者是关联player。'
        '它更新的是该player的combo状态；没有把pair作为entity的ACT action/signal索引。'
        '同一个combo窗口可接受多次符合门控的伤害，pair既不是对象唯一键，也不是独立cast证据。', '',
        '**关键接收者边界：** `52adbb: RCX=RBX; 52adbe: call [vtable+1b0]` 读的是原source；'
        '`52ae15: RCX=R14; 52ae18: call [vtable+1a8]` 写的是关联对象。'
        '低source累计比较并非必然在读取关联Player的旧+1344。已复核Monster主表+1b0指向`57a40`，'
        '不是Player/LocalPlayer的`53dfd0`；本轮不扩展57a40函数体。相同虚槽不能等同为同一字段状态。', '',
        'state0x13仍是一条独立读取分支，20字节suffix及相同(6,1500)无法判定实际经过哪条路径。'
        '因此不猜父技能、不推出entity自己的攻击动作，也不对主线candidate11056或4418链作新认证。', '',
        '## 保存字节的独立核对','',
        '复核此前40条报告中的6条低source原始record/hash；两个候选读法对pair数值一致，'
        '但每条仍有两种runtime解释。保存的compact fixture确有(6,1500)：source67/target61，'
        'record SHA256 `18993fd051f45081b9c700d25a1f1394bb3456b4e4e5d9b19e865fe1a03cc377`。'
        '不将它冒称为用户本轮提及的另一条感染记录。', '',
        '## 有界搜索与复现','',
        f"新增8个证明节点，共{n['new_bytes']}字节，{len(n['anchors'])}条严格指令断言；复用5个suffix节点。"
        '原始异常目录.data2 RVA0x2ddd290，563484 bytes/46957项。', '',
        'getter53dfd0未发现直接E8/E9调用；已证明消费者使用虚槽+1b0。'
        '偏移+1348裸字节命中很多，不能把其他类的同偏移字段合并解释；本轮只保留实际关联链。', '',
        '`python.exe -B tools/trace_native_actor_pair_v48.py`','',
        '`python.exe -B -m unittest discover -s tests -p test_native_actor_pair_v48.py -v`','',
        f"Client SHA256 `{r['client_sha256']}`；工具SHA256 `{r['tool_sha256']}`。", '',
        '## 证明索引','']
    lines += [f"- {f['id']}: {f['text']} Sites: "+', '.join(hex(x) for x in f['sites']) for f in n['facts']]
    lines += ['', '## 未验证边界','']+['- '+s for s in LIMITATIONS]+['']
    return '\n'.join(lines)


def main():
    r=build();OUT.mkdir(parents=True,exist_ok=True)
    paths=(OUT/'actor_pair.json',OUT/'actor_pair.md')
    paths[0].write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    paths[1].write_text(markdown(r),encoding='utf8',newline='\n')
    print(json.dumps(dict(new_nodes=r['native']['new_node_count'],new_bytes=r['native']['new_bytes'],
        anchors=len(r['native']['anchors']),saved_low_source_records=len(r['saved_examples']['examples']),
        new_certified_skills=0,outputs={str(p):digest(p.read_bytes()) for p in paths})))


if __name__=='__main__':main()
