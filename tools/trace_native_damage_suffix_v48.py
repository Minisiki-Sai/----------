"""Offline kind8 suffix read-plan evidence, not a skill decoder or emulator.

Only writes damage_suffix.json/md in the fixed sidecar directory. Runtime
conditions are explicit inputs to a bounded read model, never inferred from
actor-ID magnitude or capture labels. No game/DLL code is loaded or executed.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.audit_native_damage_v46 import parse_record, saved_samples
from tools.review_client_damage_types_v44 import StaticView, CodeReview, BASE, capstone

OUT = ROOT / "outputs/native_wire_v47_20260909"
V44_SHA = "5cb4a393f7b7bab7001aaa36991fad25035d2a71968ae002253bdba0d279a5de"
V46_ACTION_SHA = "28c28a13cdec2958b5871fbb28fed79fd521a362d12517b624d64e66bffcdb3c"
# Ten logical operations, thirteen evidence nodes: validity helper has four
# ORIGINAL exception fragments; six tiny direct/vtable targets lack unwind.
RANGES = (0x52A320,0x272140,0x272163,0x2721D8,0x2721FB,0x2BEF30,0x8CB550)
LEAVES = {0x46620:3,0xA56AB0:14,0x53DFD0:7,0x5374A0:43,0x41E90:3,0x9E940:3}
CLASS_TABLES = {"CDnActor":0xEBD470,"CDnPlayerActor":0xF24310,
    "CDnLocalPlayerActor":0xEFA568,"CDnMonsterActor":0xF0FE08,"CDnPetActor":0xF22508}
SLOTS = (0x70,0x80,0x108,0x1A8,0x1B0,0x290)


def digest(raw):
    return sha256(raw).hexdigest()


@dataclass(frozen=True)
class RuntimeReadConditions:
    # These summarize the COMPLETE branch predicates, not mere pointer truth.
    # low_related_pair = valid source + source virtual80 + valid +1470/+1478
    # reference pair + linked object's virtual70. It only applies below100000.
    low_related_pair: bool = False
    # Valid source, adjusted source [vbtable+8 + source +4f48] == 0x13.
    # This is independent of the earlier low_related_pair path.
    state19_pair: bool = False
    receiver_class: str = ""


def read_suffix(record, runtime):
    """Conditional exact-length byte reader; no state creation or skill output.

    A known receiver class is REQUIRED to select its verified no-op +290.
    Does not guess the runtime predicates from final packet length. Failure
    means incompatible with THIS plan, not universally invalid to native.
    """
    if runtime.receiver_class not in CLASS_TABLES:
        raise ValueError("unknown receiver virtual+290 implementation")
    if type(runtime.low_related_pair) is not bool or type(runtime.state19_pair) is not bool:
        raise ValueError("explicit boolean runtime branch facts required")
    prefix = parse_record(record)
    at = 7 + prefix.consumed
    fields = []

    def take(name,size,signed=False,site=None):
        nonlocal at
        if at+size>len(record):
            raise ValueError(f"suffix truncated under supplied runtime plan: {name}")
        raw=record[at:at+size]
        fields.append(dict(name=name,marker_offset=at-4,size=size,raw=raw.hex(),
                           value=int.from_bytes(raw,"little",signed=signed),signed=signed,site=site))
        at+=size

    def pair(name,sites):
        for i,site in enumerate(sites):take(name+f"_{i}",2,True,site)

    # Common prefix already read this byte at 52a873; offset shifts for UID.
    cc=record[4+prefix.field_offsets["common_15"]]
    if prefix.source_kind==1:
        if prefix.source_id>=100000:
            pair("high_source_pair",(0x52AA09,0x52AA2A))
        if cc==0:
            if prefix.source_id<100000 and runtime.low_related_pair:
                pair("linked_player_pair",(0x52AD62,0x52AD83))
            if runtime.state19_pair:
                pair("state19_pair",(0x52AE7D,0x52AE9E))
            take("local_d0_u32",4,site=0x52AF9B)
    # 52afb5 receiver+290 is RET imm16=0 for the five reviewed class tables.
    # This does not mean it assigns numeric zero to a return register.
    for label,site in (("dc",0x52AFCF),("e0",0x52AFE8),("e4",0x52B001)):
        take(f"local_{label}_u32",4,site=site)
    if at!=len(record):
        raise ValueError("suffix has unexplained bytes under supplied runtime plan")
    return dict(fields=fields,prefix_consumed=prefix.consumed,suffix_bytes=at-7-prefix.consumed,
                common_cc_byte=cc,receiver_class_assumption=runtime.receiver_class,
                runtime_conditions=asdict(runtime),skill_certified=False)


def compatible_plans(record, receiver_class):
    """Enumerate hypotheses, NEVER certify a runtime branch by its length.

    Irrelevant predicates are represented once. With a low source and20-byte
    suffix, linked-player-only and state19-only are distinct alternatives.
    """
    p=parse_record(record)
    cc=record[4+p.field_offsets["common_15"]]
    active=p.source_kind==1 and cc==0
    result=[]
    for linked in (False,True) if active and p.source_id<100000 else (False,):
        for state19 in (False,True) if active else (False,):
            conditions=RuntimeReadConditions(linked,state19,receiver_class)
            try:
                parsed=read_suffix(record,conditions)
            except ValueError:
                continue
            result.append(parsed)
    return result


ANCHORS = (
    (0x52A8FA,"lea","r12, [rsi - 0x900]"),
    (0x52A9E5,"cmp","edx, 0x186a0"),
    (0x52A9EB,"jb","0x14052aa33"),
    (0x52AA1A,"lea","r8d, [r9 + 3]"),
    (0x52AA40,"mov","rbx, qword ptr [rbp - 0x48]"),
    (0x52AA71,"cmp","byte ptr [rbp + 0xcc], r13b"),
    (0x52AA78,"jne","0x14052afa7"),
    (0x52AC96,"mov","r8d, dword ptr [rsp + 0x64]"),
    (0x52AC9B,"mov","edx, dword ptr [rsp + 0x5c]"),
    (0x52ACA2,"call","qword ptr [rax + 0x1a8]"),
    (0x52ACBB,"cmp","dword ptr [rsp + 0x60], 0x186a0"),
    (0x52ACC3,"jae","0x14052ae1e"),
    (0x52ACE6,"call","qword ptr [rax + 0x80]"),
    (0x52ACF4,"mov","r15, qword ptr [rbx + 0x1470]"),
    (0x52ACFB,"mov","r14, qword ptr [rbx + 0x1478]"),
    (0x52AD3B,"call","qword ptr [rax + 0x70]"),
    (0x52AE18,"call","qword ptr [rax + 0x1a8]"),
    (0x52AE53,"cmp","dword ptr [rcx + rbx + 0x4f48], 0x13"),
    (0x52AF81,"call","qword ptr [rax + 0x1a8]"),
    (0x52AF8D,"lea","rdx, [rbp + 0xd0]"),
    (0x52AFA7,"mov","rax, qword ptr [r12]"),
    (0x52AFB2,"mov","rcx, r12"),
    (0x52AFB5,"call","qword ptr [rax + 0x290]"),
    (0x52AFC1,"lea","rdx, [rbp + 0xdc]"),
    (0x52AFDA,"lea","rdx, [rbp + 0xe0]"),
    (0x52AFF3,"lea","rdx, [rbp + 0xe4]"),
    (0x52B074,"lea","rdx, [rbp - 0x30]"),
    (0x52B07B,"call","qword ptr [rax + 0x108]"),
    (0xA56AB0,"mov","dword ptr [rcx + 0x1344], edx"),
    (0xA56AB6,"mov","dword ptr [rcx + 0x1348], r8d"),
    (0x53DFD0,"mov","eax, dword ptr [rcx + 0x1344]"),
    (0x46620,"ret","0"),
    (0x41E90,"xor","al, al"),
    (0x9E940,"mov","al, 1"),
    (0x5374A0,"movsd","xmm0, qword ptr [rdx]"),
    (0x5374AC,"mov","eax, dword ptr [rdx + 8]"),
)
READ_CALLS = (0x52AA09,0x52AA2A,0x52AD62,0x52AD83,0x52AE7D,0x52AE9E)
RAW_CALLS = (0x52AF9B,0x52AFCF,0x52AFE8,0x52B001)


FACTS = (
    ("S1","source_kind1 and source_id>=100000 consume two signed16 values BEFORE source lookup and even before the common_cc bypass. They are not actor/skill IDs merely because source_id is large.",[0x52A9E1,0x52A9E5,0x52A9EB,0x52AA09,0x52AA2A]),
    ("S2","For a valid high-ID source and common_cc=0, the pair reaches source primary virtual+1a8. Player target a56ab0 writes +1344/+1348; +1b0 getter53dfd0 reads +1344. LocalPlayer delegates to the same setter then another helper. Semantic names beyond these state fields remain unproved.",[0x52AA71,0x52ABE5,0x52AC4E,0x52AC54,0x52AC9B,0x52ACA2,0xA56AB0,0xA56AB6,0x53DFD0,0x8CB559]),
    ("S3","A small source ID can consume an additional pair only through runtime gates: source virtual80, valid source+1470/+1478 reference, linked object's virtual70. Its setter receiver is the linked object, not automatically the physical source.",[0x52ACBB,0x52ACC3,0x52ACE6,0x52ACF4,0x52ACFB,0x52AD3B,0x52AD62,0x52AD83,0x52AE18]),
    ("S4","An independent source state==0x13 branch consumes another pair, even after a previous pair. State test uses the adjusted +b50 component's table+8 displacement. Consumption occurs before checking the secondary +1568/+1570 reference; setter at52af81 uses source RBX.",[0x52AE48,0x52AE4F,0x52AE53,0x52AE7D,0x52AE9E,0x52AEA3,0x52AEAA,0x52AF7A,0x52AF7E,0x52AF81]),
    ("S5","Only source_kind1/common_cc0 reads local rbp+d0 via52af9b. Other source kinds or common_cc!=0 bypass it. Therefore marker+102 is not an unconditional slot.",[0x52A9A8,0x52A9DC,0x52AA78,0x52AF8D,0x52AF9B]),
    ("S6","52afb5 calls virtual+290 on RECEIVER primary (R12=RSI-900), not resolved source RBX. All five inspected RTTI-backed class tables map this slot to46620 RET imm16=0, consuming zero stream bytes without assigning a return value. Other derived classes are not certified by inheritance guesses.",[0x52A8FA,0x52AFA7,0x52AFAB,0x52AFB2,0x52AFB5,0x46620]),
    ("S7","Three raw dwords are read after that virtual call into locals dc/e0/e4. In this selected kind8 branch no subsequent direct load of d0/dc/e0/e4 or pointer to them was found. Receiver+108's reviewed target5374a0 copies only12 bytes from rbp-30, not those suffix locals. This does not prove wire-side producer semantics.",[0x52AFC1,0x52AFCF,0x52AFDA,0x52AFE8,0x52AFF3,0x52B001,0x52B074,0x52B07B,0x5374A0,0x5374AC,0x5374CA]),
)


def native_evidence(view):
    code=CodeReview(view); nodes=[]; all_rows={}
    for start in RANGES:
        f=view.function(start)
        if not f or f[0]!=start:raise ValueError("wrong original runtime range")
        raw=view.read(start,f[1]-start)
        decoded=code.decode(f)
        # Large dispatcher contains other kinds + embedded jump tables. Keep
        # complete raw-range hash, but report only grounded kind8/entry windows.
        chosen=[i for i in decoded if start!=0x52A320 or
                i.address-BASE<0x52A393 or 0x52A48A<=i.address-BASE<0x52B1FF]
        rows=[code.row(i) for i in chosen]
        all_rows.update((r['rva'],r) for r in rows)
        nodes.append(dict(start=start,runtime_function=f,range_size=len(raw),sha256=digest(raw),
            provenance=view.provenance(start,len(raw)),
            exception_record_rva=view.exception['rva']+view.starts.index(start)*12,
            instructions=rows,analysis_scope="kind8 + dispatch-entry only" if start==0x52A320 else "original exception fragment"))
    for start,size in LEAVES.items():
        if view.function(start) is not None:raise ValueError("leaf has unexpected unwind record")
        raw=view.read(start,size)
        rows=[code.row(i) for i in code.dis.disasm(raw,BASE+start)]
        if sum(len(bytes.fromhex(r['bytes'])) for r in rows)!=size or not rows[-1]['mnemonic'].startswith('ret'):
            raise ValueError("incomplete bounded leaf")
        if any(r.get('direct_branch_target') is not None for r in rows):
            raise ValueError("leaf has unreviewed branch")
        all_rows.update((r['rva'],r) for r in rows)
        nodes.append(dict(start=start,size=size,sha256=digest(raw),raw=raw.hex(),instructions=rows,
            runtime_function=None,provenance=view.provenance(start,size),
            analysis_scope="bounded vtable/direct-target leaf; not an invented exception range"))
    anchors=[]
    for at,mnemonic,operands in ANCHORS:
        row=all_rows.get(at)
        if row is None or (row['mnemonic'],row['operands'])!=(mnemonic,operands):
            raise ValueError(f"changed anchor {at:#x}: {row}")
        anchors.append(row)
    for at,target in [(r,0x22F270) for r in READ_CALLS]+[(r,0x22F260) for r in RAW_CALLS]:
        row=all_rows[at]
        if row.get('direct_branch_target')!=target:raise ValueError("reader target changed")
        anchors.append(row)
    prior=json.loads(view.load(ROOT/'outputs/client_protection_v44_20260908/damage_types.json'))
    tables=[]
    expected={0x290:{0x46620},0x108:{0x5374A0},0x70:{0x41E90,0x9E940},
              0x80:{0x41E90,0x9E940},0x1A8:{0x46620,0xA56AB0,0x8CB550},0x1B0:{0x57A40,0x53DFD0}}
    for name,vt in CLASS_TABLES.items():
        item=next(x for x in prior['focus_types'] if x['simple_name']==name)
        loc=next(x for x in item['locators'] if x['subobject_offset']==0 and any(t['rva']==vt for t in x['vtables']))
        if view.read(loc['rva'],24).hex()!=loc['raw'] or struct.unpack('<Q',view.read(vt-8,8))[0]!=BASE+loc['rva']:
            raise ValueError("RTTI locator association changed")
        for slot in SLOTS:
            raw=view.read(vt+slot,8);target=struct.unpack('<Q',raw)[0]-BASE
            if target not in expected[slot]:raise ValueError("unreviewed virtual target")
            tables.append(dict(class_name=name,vtable=vt,locator=loc['rva'],subobject_offset=0,
                slot_byte_offset=slot,slot_index=slot//8,slot_rva=vt+slot,raw=raw.hex(),target=target,
                provenance=view.provenance(vt+slot,8)))
    signed_path=ROOT/'outputs/projectile_gap_v46_20260909/action_lookup.json'
    signed_raw=view.load(signed_path)
    if digest(signed_raw)!=V46_ACTION_SHA:raise ValueError("signed-read prior evidence changed")
    signed=json.loads(signed_raw)
    conversion=next(n for n in signed['nodes'] if n['start']==0x22F336)
    if digest(view.read(conversion['start'],conversion['size']))!=conversion['sha256']:
        raise ValueError("signed-read code changed")
    # Negative-use audit is explicitly local, not a whole-program assertion.
    stack_refs={}
    for offset in ('d0','dc','e0','e4'):
        needle=f'[rbp + 0x{offset}]'
        stack_refs[offset]=[r for r in all_rows.values() if 0x52A48A<=r['rva']<0x52B1FF and needle in r['operands']]
    expected_refs={'d0':[0x52A541,0x52AF8D], 'dc':[0x52AFC1],
                   'e0':[0x52A54E,0x52AFDA], 'e4':[0x52AFF3]}
    if {k:[r['rva'] for r in rs] for k,rs in stack_refs.items()}!=expected_refs:
        raise ValueError("local suffix usage changed; re-review")
    facts=[dict(id=key,text=text,sites=sites,instructions=[all_rows[a] for a in sites])
           for key,text,sites in FACTS]
    return dict(nodes=nodes,logical_operations=10,exception_ranges=7,bounded_leaves=6,
        anchors=anchors,class_vtable_slots=tables,facts=facts,local_stack_references=stack_refs,
        signed_reader_reused=dict(report_sha256=digest(signed_raw),node=conversion),
        selected_instruction_bytes=sum(len(bytes.fromhex(r['bytes'])) for r in all_rows.values()))


def sample_evidence(view):
    # Legacy inventory supplies existing exact outer boundaries only. This is
    # not a new record recognizer, acceptance run or coverage claim.
    from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
    prior=json.loads(view.load(ROOT/'outputs/projectile_gap_v46_20260909/native_damage.json'))
    expected={(r['wire_sha256'],r['record_offset'],r['record_sha256']):r for r in prior['rows']}
    hashes,samples=saved_samples()
    for path,wanted in hashes.items():
        if digest(view.load(Path(path)))!=wanted:raise ValueError("fixture changed")
    rows=[];seen=set();skipped=[]
    for origin,wire in samples:
        wire_hash=digest(wire)
        if wire_hash in seen:continue
        seen.add(wire_hash)
        inv=inspect_udp_damage_records(wire,'server_to_client')
        if not inv.complete:
            skipped.append(dict(origin=origin,wire_sha256=wire_hash,reason=inv.rejection_reason));continue
        for record in inv.damage_records:
            key=(wire_hash,record.record_offset,record.wire_record_sha256)
            old=expected.pop(key,None)
            if old is None:raise ValueError("sample boundary absent from saved40")
            raw=record.record_bytes;prefix=parse_record(raw)
            if asdict(prefix)!=old['native']:raise ValueError("saved native prefix changed")
            plans=compatible_plans(raw,'CDnActor')
            if not plans:raise ValueError("no compatible known-noop plan for saved record")
            # All plans currently end with the same4 dword locations but have
            # different causes for the earlier pair; preserve that ambiguity.
            offsets=[[(f['name'],f['marker_offset']) for f in p['fields'] if f['size']==4] for p in plans]
            rows.append(dict(origin=origin,wire_sha256=wire_hash,record_offset=record.record_offset,
                record_sha256=digest(raw),record_hex=raw.hex(),target_id=record.target_actor_id,
                source_id=prefix.source_id,source_kind=prefix.source_kind,uid_branch=prefix.uid_branch,
                declared_body_bytes=len(raw)-7,prefix_consumed=prefix.consumed,
                suffix_bytes=len(bytes.fromhex(prefix.suffix_raw)),conditional_plans=plans,
                same_dword_locations_across_plans=all(x==offsets[0] for x in offsets),
                runtime_route_certified=False,skill_certified=False))
    if expected or len(rows)!=40:raise ValueError("saved40 set changed")
    return dict(records=rows,rejected_outer_messages=skipped,
        summary=dict(records=len(rows),by_family=dict(Counter('UID' if x['uid_branch'] else 'index' for x in rows)),
            conditional_compatible_records=len(rows),ambiguous_runtime_plan_records=sum(len(r['conditional_plans'])>1 for r in rows),
            independently_observed_runtime_classes=0,new_certified_skills=0),
        boundary="Saved40 exact record bytes rechecked. Plans ASSUME a reviewed receiver+290 no-op and supplied branch conditions; record length prunes alternatives but does not prove object class, parent validity, state19, or skill meaning.")


LIMITATIONS = [
    "Only10 logical operations/13 evidence nodes plus one previously proved signed-read node. Dispatcher other kinds are excluded; original exception boundaries are retained even for fragments.",
    "No client/DLL loading, native guest/API execution, live memory, game modification, production parser change or capture-wide scan.",
    "source ID>=100000 is a native read-branch threshold, not an inferred player/owner/class certificate.",
    "Five inspected receiver primary tables share a no-op+290. Unreviewed subclasses may override it; an unknown callback must remain unresolved, not zero-length by default.",
    "Low-ID linked-player and state19 paths require runtime facts.40 fixtures provide bytes/bounds, not those facts; six low-ID records retain alternative plans.",
    "local_d0/dc/e0/e4 are positional names. Absence of use is scoped to the reviewed kind8 branch and resolved virtual consumers; producer/network intended semantics and other versions are unproved.",
    "Player pair fields+1344/+1348 and comparison with the+1344 getter are proved. No reliable skill table/ACT slot interpretation or invented counter name is assigned.",
    "The v46 outer inventory defines the saved40 record set; this sidecar does not expand accepted packet families or certify new skill attribution.",
    "Original client and six decoded stream hashes are checked; v44 final-section-semantics limitation remains.",
]


def build():
    helper=ROOT/'tools/review_client_damage_types_v44.py'
    if digest(helper.read_bytes())!=V44_SHA:raise ValueError("v44 helper changed")
    view=StaticView()
    for path in (helper,Path(__file__),ROOT/'tests/test_native_damage_suffix_v48.py',
                 ROOT/'tools/audit_native_damage_v46.py',ROOT/'dragon_nest_builder/live_dps/udp_damage_records.py'):
        view.load(path)
    native=native_evidence(view)
    samples=sample_evidence(view)
    changed=[p for p,h in view.inputs.items() if digest(Path(p).read_bytes())!=h]
    if changed:raise ValueError(f"input changed during audit: {changed}")
    return dict(schema='native-damage-suffix-v48',client_sha256=digest(view.raw),
        tool_sha256=digest(Path(__file__).read_bytes()),inputs=view.inputs,changed_inputs=changed,
        exception_directory=view.exception,capstone_version=capstone.__version__,
        native=native,samples=samples,limitations=LIMITATIONS,production_changes=False,new_certified_skills=0)


def markdown(r):
    s=r['samples']['summary']
    lines=['# v48 kind8 suffix：运行时读取分支与字段消费者','',
        '## 结论','',
        '当前不能把 marker+102/+114 证明为通用 self/context skill。已证明的是条件读取位置、'
        '调用接收对象及部分状态字段：source 的两个 signed16 可写 Player+1344/+1348；'
        '受击方 virtual+290 在五类已核对主表中不读字节；最后三个 dword 在本段只读取、未见用于skill lookup。','',
        '## 新的数据流','',
        '|读数|条件/接收对象|原始证据|','|---|---|---|',
        '|prefix后第一对signed16|source_kind1且source≥100000；读取先于source查表|52aa09/52aa2a|',
        '|该对的状态写入|有效source、common_cc=0 → source虚槽+1a8；Player写+1344/+1348|52aca2 → a56ab0；getter53dfd0|',
        '|低source的条件对|source虚槽+80、有效+1470/+1478关联对象、关联对象虚槽+70；setter用关联对象|52ad62/52ad83 → 52ae18|',
        '|state0x13条件对|独立判断，可叠加前一对；setter用source|52ae53，52ae7d/52ae9e → 52af81|',
        '|local_d0 rawu32|仅source_kind1且common_cc=0|52af8d/52af9b|',
        '|virtual+290|受击方主对象R12=RSI−900，不是source；五类表均指向ret0|52a8fa，52afb5 → 46620|',
        '|local_dc/e0/e4 rawu32|虚调用返回后依次读取；未见本分支后续使用|52afcf/52afe8/52b001|','',
        'common_cc 是公共prefix倒数第二字节（indexed marker+96，UID再偏移4）。它会跳过运行时分支及local_d0读取；'
        '不能忽略该字节而按固定20字节模板解释所有suffix。', '',
        '46620为机器指令RET imm16=0，表示不执行流读取便返回，并非“把返回值设为0”。', '',
        '受击方+108在同五类主表中均为5374a0：只复制参数的前12字节到receiver+ecc/+edc，'
        '不能把传入rbp−30视为这些suffix局部量的间接skill消费证明。', '',
        '## 保存的40条actual记录核对','',
        f"{s['records']}条原始record bytes/hash/prefix与v46保存集合完全一致：39 indexed、1 UID，suffix均20字节。"
        '在“受击方+290为已核对no-op”的条件下全部有兼容读法；没有通过包长认证实际class。', '',
        '33条高source indexed记录：一对16位值位于marker+98/+100，local_d0位于+102，'
        '后三个dword位于+106/+110/+114。1条UID记录整体偏移4，对应+102/+104、+106、+110/+114/+118。', '',
        '6条低source indexed记录：必须仍保留“关联player读取一对”与“state19读取一对”两种假设，'
        '现有字节不能区分。四个dword位置恰好相同不等于状态链已证明。', '',
        '因此旧+102/+114数值可作有来源的原始观察保留；本轮未发现足以把它们升级为native skill slot的消费者证据。', '',
        '## 可复现实现与范围','',
        '工具的RuntimeReadConditions必须显式指定已审查receiver class以及两个运行时读取条件。'
        '未知+290实现、截断或剩余字节均报告该计划不兼容；不冒充客户端通用拒绝规则。', '',
        f"10个逻辑操作，7个真实异常范围（包含272140的4个fragment）、6个有边界leaf；"
        f"{len(r['native']['anchors'])}条严格指令断言。异常目录来自原文件.data2 RVA0x2ddd290，563484 bytes/46957项。", '',
        '`python.exe -B tools/trace_native_damage_suffix_v48.py`','',
        '`python.exe -B -m unittest discover -s tests -p test_native_damage_suffix_v48.py -v`','',
        f"Client SHA256 `{r['client_sha256']}`；工具SHA256 `{r['tool_sha256']}`。", '',
        '## 证明索引','']
    lines += [f"- {f['id']}: {f['text']} Sites: "+', '.join(hex(x) for x in f['sites']) for f in r['native']['facts']]
    lines += ['', '## 未验证边界','']+['- '+s for s in LIMITATIONS]+['']
    return '\n'.join(lines)


def main():
    report=build();OUT.mkdir(parents=True,exist_ok=True)
    paths=(OUT/'damage_suffix.json',OUT/'damage_suffix.md')
    paths[0].write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    paths[1].write_text(markdown(report),encoding='utf8')
    print(json.dumps(dict(summary=report['samples']['summary'],anchors=len(report['native']['anchors']),
        outputs={str(p):digest(p.read_bytes()) for p in paths})))


if __name__=='__main__':main()
