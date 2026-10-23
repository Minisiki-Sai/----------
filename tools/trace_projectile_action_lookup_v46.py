"""Eight-node static ACT lookup audit; no native/guest execution or packet replay.

Only writes action_lookup.json/md in the fixed v46 sidecar directory. Uses the
v44 byte reader and its original file-backed exception directory, not live code.
The tiny lookup model below describes integer/index selection, not an emulator
or a new production attribution rule. Run using Python 3.12 -B.
"""
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from review_client_damage_types_v44 import StaticView, CodeReview, BASE, capstone
from probe_magma_signal_flags_v36 import parse_act6

OUT = ROOT / "outputs/projectile_gap_v46_20260909"
RESOURCE = Path("E:/ext0811.rar/Resource00/Resource00/resource")
V44_SHA = "5cb4a393f7b7bab7001aaa36991fad25035d2a71968ae002253bdba0d279a5de"
NODES = {0xA90370: None, 0x04EF30: 38, 0x04F3E0: 103, 0x0524F80: None,
         0xC6EDA0: None, 0x0535B0: None, 0x050390: None, 0x22F336: None}


def digest(data):
    return sha256(data).hexdigest()


def lookup_keys(action_bytes, child_bytes):
    """22f346/351 MOVSX followed by 4f3f3's zero-extension of EDX."""
    if len(action_bytes) != 2 or len(child_bytes) != 1:
        raise ValueError("expected exactly int16 action and int8 signal key")
    action = int.from_bytes(action_bytes, "little", signed=True)
    child = int.from_bytes(child_bytes, "little", signed=True)
    return dict(action_signed=action, action_vector_index=action & 0xFFFFFFFF,
                signal_key_signed=child)


def loader_signal_key(source_ordinal):
    if not 0 <= source_ordinal <= 0x7FFFFFFF:
        raise ValueError("nonnegative source signal ordinal required")
    short = source_ordinal & 0xFFFF
    return short if short < 0x8000 else short - 0x10000


def retained_signal_tree(records):
    """Model only visible loader insertion: retain original ordinal, first key wins.

    records = (original ordinal, retained?, arbitrary node). Filtering must not
    renumber later signals; sorting the retained vector must not change the key.
    """
    result = {}
    for ordinal, retained, node in records:
        if retained:
            result.setdefault(loader_signal_key(ordinal), node)
    return result


def lookup_model(actions, action_bytes, child_bytes):
    """Pure synthetic valid-container model of 4f3e0 plus a90995's type gate."""
    keys = lookup_keys(action_bytes, child_bytes)
    index = keys["action_vector_index"]
    if index >= len(actions):
        return None
    signal = actions[index].get(keys["signal_key_signed"])
    return signal if signal is not None and signal["type"] == 0xF else None


class Evidence:
    def __init__(self, view):
        self.view, self.code, self.nodes = view, CodeReview(view), {}
        self.windows, self.anchors = [], []
        for start, size in NODES.items():
            function = view.function(start)
            if size is None:
                if function is None or function[0] != start or function[1]-start > 32768:
                    raise ValueError(f"wrong original exception boundary: {start:#x}")
                size = function[1]-start
                instructions = self.code.decode(function)
            else:
                if function is not None:
                    raise ValueError("leaf now has unwind entry; re-review")
                instructions = list(self.code.dis.disasm(view.read(start, size), BASE+start))
            body = view.read(start, size)
            rows = [self.code.row(i) for i in instructions]
            if sum(len(bytes.fromhex(r["bytes"])) for r in rows) != size:
                raise ValueError(f"incomplete selected-node decode: {start:#x}")
            item = dict(start=start, size=size, sha256=digest(body), instructions=rows,
                        provenance=view.provenance(start, size), runtime_function=function,
                        boundary="original RUNTIME_FUNCTION" if function else "bounded direct-target leaf sample; no invented function interval")
            if function:
                item["exception_record_rva"] = view.exception["rva"] + view.starts.index(start)*12
            else:
                item["raw"] = body.hex()
                if rows[-1]["mnemonic"] != "ret":
                    raise ValueError("leaf sample missing last return")
                if any(r.get("direct_branch_target", start) not in range(start, start+size) for r in rows):
                    raise ValueError("leaf contains an unreviewed external branch")
            self.nodes[start] = item
        if sum(n["size"] for n in self.nodes.values()) > 64*1024:
            raise ValueError("eight-node byte budget")

    def row(self, rva):
        matches = [r for n in self.nodes.values() for r in n["instructions"] if r["rva"] == rva]
        if len(matches) != 1:
            raise ValueError(f"anchor lacks selected-node instruction boundary: {rva:#x}")
        return matches[0]

    def expect(self, rva, mnemonic, operands=None, target=None):
        row = self.row(rva)
        if (row["mnemonic"] != mnemonic or operands is not None and row["operands"] != operands
                or target is not None and row.get("direct_branch_target") != target):
            raise ValueError(f"anchor changed: {rva:#x} {row}")
        self.anchors.append(row)

    def window(self, label, start, end):
        rows = [r for n in self.nodes.values() for r in n["instructions"] if start <= r["rva"] < end]
        if not rows or rows[0]["rva"] != start:
            raise ValueError(f"window start is not a reviewed boundary: {label}")
        self.windows.append(dict(label=label, start=start, end=end, instructions=rows))


def old_resource_examples(view):
    # Two explicit known ACT6 files only; no directory inventory or DNT rescan.
    selections = [
        ("char/player/kali/kali_skill_2st_souleater.act", 9, "Skill_BeastSpirit_EX", [16]),
        ("weapon/kali_beastspirit_ex_pve.act", 3, "Hit", [66, 67]),
    ]
    result = []
    for rel, action_index, expected_name, signals in selections:
        path = RESOURCE / rel
        raw = view.load(path)
        parsed = parse_act6(raw)
        action = parsed["actions"][action_index]
        if action["name"] != expected_name:
            raise ValueError("old ACT anchor changed")
        rows = []
        for ordinal in signals:
            signal = action["signals"][ordinal]
            if signal["type"] != 15:
                raise ValueError("old ACT type15 anchor changed")
            at = signal["offset"]
            rows.append(dict(source_signal_ordinal=ordinal, loader_int16_key=loader_signal_key(ordinal),
                wire_int8_representable=-128 <= loader_signal_key(ordinal) <= 127,
                file_header_order=signal["order"], file_signal_offset=at,
                file_payload_offset=signal["payload_offset"], type=signal["type"],
                payload_first_u32=signal["u32"][0], raw_header_and_first_words=raw[at:at+36].hex(),
                signal_sha256=signal["span_sha256"]))
        result.append(dict(path=str(path), sha256=digest(raw), bytes=len(raw),
            version=parsed["version"], total_actions=len(parsed["actions"]),
            local_action_index=action_index, action_name=action["name"],
            action_offset=action["offset"], action_sha256=action["span_sha256"], signals=rows,
            boundary="Old ACT6 resource only. Local file index is not automatically the current receiver's merged action-vector index; no runtime/packet mapping certified."))
    return result


FACTS = [
    dict(id="A1", text="Action resource is used as an action-pointer vector index, not a FileTable row lookup: 4ef30/4f3e0 dereference receiver+30 then vector[index*8].", sites=[0x4EF30,0x4EF43,0x4EF4C,0x4EF4E,0x4F3E0,0x4F3F3,0x4F3FC,0x4F3FE]),
    dict(id="A2", text="reference branch0 copies the common reference into a lookup key, calls 524f80, then adjusts the returned object's +b50 component through its table's +4 displacement for 4f3e0.", sites=[0xA90569,0xA9056C,0xA909C8,0xA909D0,0xA90A0D,0xA90A11,0xA90A18,0xA90A26,0xA90A2A,0xA90A2D]),
    dict(id="A3", text="reference branch1/UID=0 uses the extra dword, not the common reference, to call c6eda0 (object map1191f30). Its returned parent/object +16a8 component supplies 4f3e0; it is not necessarily an actor or always a Projectile subtype.", sites=[0xA90557,0xA90562,0xA907AC,0xA907B4,0xC6EDBC,0xA90968,0xA9096F,0xA9097D,0xA90981,0xA90984]),
    dict(id="A4", text="UID!=0 scans two caller slots (virtual +258, optionally +320), compares the extra qword against object+1208 and joins the same +16a8 component path. Do not replace the qword with owner or truncated object ID.", sites=[0xA90667,0xA906A3,0xA906DD,0xA906E5,0xA9072E,0xA90775,0xA9077D,0xA90788,0xA90796]),
    dict(id="A5", text="Loader initializes signal ordinal EDI=0, writes DI at signal+a BEFORE filtering; increments even after rejection. It later inserts sign-extended signal+a as key at node+20 and signal pointer at node+28 into action+a8 tree. Sorting/filters do not justify renumbering retained signals.", sites=[0x53A92,0x53A94,0x53BFD,0x53C10,0x53C17,0x53C26,0x53C46,0x53C6D,0x53C9D,0x53F74,0x53F79,0x53FFD,0x54000]),
    dict(id="A6", text="Signal reader stores file header type/start/end/order at +8/+c/+e/+10, separately from loader ordinal+a. Four file-header values are read as dwords; runtime type/start/end narrow to words and order to byte.", sites=[0x503D1,0x503D9,0x503EF,0x503F7,0x5040D,0x50415,0x5042B,0x50433]),
    dict(id="A7", text="Wire action is MOVSX int16; child is MOVSX int8. Action then zero-extends EDX for unsigned vector bounds; negative action becomes large/out-of-range. Signal tree comparison is signed; do not mask child to uint8 or index a Python list with a negative value.", sites=[0xA90576,0xA90588,0xA90593,0xA905A5,0x22F346,0x22F351,0x4F3F3,0x4F3FC,0x4F416,0x4F41A,0x4F434,0x4F438]),
    dict(id="A8", text="4f3e0 returns any signal node; type0xf gate belongs to caller a90995, which reads payload pointer node+18. Missing/wrong-type signal leaves the payload absent. Inline definition may still provide fields, so failed lookup is not a universal rejection certificate.", sites=[0x4F43F,0xA9098C,0xA90995,0xA909A0,0xA90A5E,0xA90A63,0xA90ABC,0xA90B55]),
    dict(id="A9", text="4ef30 is not the signal resolver: a9061e precheck and a91396 Skill_ name check use CALLER +b50 even when branch1 signal lookup uses the parent's +16a8. These are distinct receiver action namespaces.", sites=[0xA90606,0xA9061B,0xA9061E,0xA90638,0xA9063F,0xA9137A,0xA91393,0xA91396,0xA913AA]),
    dict(id="A10", text="Config from stream [rbp+14] reaches c6fae0 independently at a91086. Signal payload supplies additional behavior fields; payload first u32 is also used under a flag at a90eac, but no equality certificate with stream config was established. A matching old type15 number is corroboration, not native-enforced skill binding.", sites=[0xA90451,0xA90B5A,0xA90B62,0xA90B69,0xA90B70,0xA90E8E,0xA90EAC,0xA91086,0xA9108C]),
]


def build():
    helper = ROOT / "tools/review_client_damage_types_v44.py"
    if digest(helper.read_bytes()) != V44_SHA:
        raise ValueError("v44 helper changed; review before reuse")
    view = StaticView()
    for path in [helper, Path(__file__), ROOT / "tools/probe_magma_signal_flags_v36.py",
                 ROOT / "tests/test_projectile_action_lookup_v46.py"]:
        view.load(path)
    e = Evidence(view)
    for rva, mnemonic, operands in [
        (0x4F3F3,"mov","edx, edx"), (0x4F3FC,"jae","0x14004f444"),
        (0x4F402,"mov","rdx, qword ptr [rax + 0xa8]"),
        (0x4F416,"cmp","dword ptr [rax + 0x20], r8d"),
        (0x4F41A,"jge","0x14004f422"), (0x4F43F,"mov","rax, qword ptr [rcx + 0x28]"),
        (0x4EF4E,"mov","rax, qword ptr [rax + rdx*8]"),
        (0x22F346,"movsx","eax, word ptr [rbp - 0x28]"),
        (0x22F351,"movsx","eax, byte ptr [rbp - 0x28]"),
        (0x53BFD,"mov","word ptr [rbx + 0xa], di"),
        (0x53F74,"movsx","ebx, word ptr [r14 + 0xa]"),
        (0x53F79,"lea","rsi, [r15 + 0xa8]"),
        (0x53FFD,"mov","dword ptr [rax + 0x20], ebx"),
        (0x54000,"mov","qword ptr [rax + 0x28], r14"),
        (0x50433,"mov","byte ptr [r13 + 0x10], al"),
        (0xA90995,"cmp","word ptr [rax + 8], 0xf"),
        (0xA909A0,"mov","r15, qword ptr [rax + 0x18]"),
        (0xA91086,"mov","edx, dword ptr [rbp + 0x14]"),
    ]:
        e.expect(rva, mnemonic, operands)
    for site, target in [(0xA9061E,0x4EF30),(0xA91396,0x4EF30),(0xA90984,0x4F3E0),
                         (0xA90A2D,0x4F3E0),(0xA907B4,0xC6EDA0),(0xA909D0,0x524F80),
                         (0x53C10,0x50390),(0xA9108C,0xC6FAE0)]:
        e.expect(site,"call",target=target)
    table = struct.unpack("<9I",view.read(0x22F5A4,36))
    if table[:2] != (0x22F346,0x22F351):
        raise ValueError("signed conversion dispatch changed")
    for label,a,b in [
        ("wire inputs and reference split",0xA9040E,0xA905AA),
        ("caller action-name precheck",0xA90606,0xA90645),
        ("UID branch reference slots and indexed parent lookup",0xA90651,0xA907DD),
        ("parent ACT signal and type gate",0xA90968,0xA909AD),
        ("actor ACT signal",0xA909C8,0xA90A3B),
        ("absent definition and inline fallback",0xA90A3B,0xA90B90),
        ("signal payload first word conditional use",0xA90E85,0xA90EB2),
        ("independent stream config",0xA9105D,0xA9109E),
        ("caller-only Skill_ check",0xA9137A,0xA913C5),
        ("loader ACT signature",0x53684,0x536AC),
        ("source signal ordinal initialization",0x53A92,0x53AA0),
        ("ordinal preservation across filtering",0x53BF8,0x53C7E),
        ("sort before signal tree",0x53C7E,0x53CA2),
        ("signed ordinal tree insertion",0x53F69,0x54044),
        ("append loaded action to pointer vector",0x5408B,0x540BF),
        ("file header is separate from ordinal",0x503BA,0x50444),
    ]:
        e.window(label,a,b)
    facts = [dict(f, instructions=[e.row(rva) for rva in f["sites"]]) for f in FACTS]
    literals = []
    for rva,text in [(0xFA40A8,"Eternity Action File"),(0xEBDC80,"Skill_")]:
        raw=text.encode()+b"\0"
        if view.read(rva,len(raw))!=raw:
            raise ValueError("literal changed")
        literals.append(dict(rva=rva,text=text,raw=raw.hex(),provenance=view.provenance(rva,len(raw))))
    resources = old_resource_examples(view)
    prior = ROOT / "outputs/projectile_evidence_v45_20260909/stream_fixture_audit.json"
    old = json.loads(view.load(prior))
    observations = [dict(wire_sha256=r["wire_sha256"], frame=r.get("frame"),
        object=r["expected_object"], config=r["expected_template"],
        parsed=r["parsed"]) for r in old["results"] if r["expected_object"] in (102321,102350)]
    changed = [p for p,h in view.inputs.items() if digest(Path(p).read_bytes())!=h]
    if changed:
        raise ValueError(f"input changed during audit: {changed}")
    return dict(schema="projectile-action-lookup-v46", client_sha256=digest(view.raw),
        tool_sha256=digest(Path(__file__).read_bytes()), inputs=view.inputs, changed_inputs=changed,
        exception_directory=view.exception, capstone_version=capstone.__version__,
        selected_node_count=len(e.nodes), selected_bytes=sum(n["size"] for n in e.nodes.values()),
        nodes=list(e.nodes.values()), anchors=e.anchors, facts=facts, windows=e.windows,
        literals=literals, signed_conversion_table=dict(rva=0x22F5A4,entries=list(table)),
        historical_resources=resources, prior_v45_observations=observations,
        prior_observation_boundary="Copied hash-pinned v45 report fields, not packet replay or newly accepted observations.",
        limitations=LIMITATIONS)


LIMITATIONS = [
    "Offline bytes/disassembly only. No client/DLL/API execution, live memory, packet replay, production parser/attribution changes or config-table rescan.",
    "Six original RUNTIME_FUNCTION ranges plus two explicitly bounded leaves. Signature-reference discovery also inspected three nearby ACT loader candidates; only 535b0 is retained as the loader proof node. No further caller graph is followed.",
    "Decoded streams are hash verified but final post-load semantics remain uncertified. Static paths describe conditions, not observed object lifetime or execution.",
    "The signal key assignment/reader/tree insertion are visible; arbitrary side effects of every allocator, virtual filter and delegated resource helper are not exhaustively modeled. Retained signals may have holes; negative wire child must remain signed.",
    "branch0 resolves the reference actor, not automatically the caller/player. branch1 resolves a weapon/object-map entry; actual Projectile subtype and valid generation must be independently established.",
    "Two old ACT files support ordinal-versus-order distinctions and known example shapes, not exact current merged-action ordering. No actor manifest concatenation or current-client resource version match was newly proved.",
    "Local action index9 in the old SoulEater file is not itself native global224. That requires a separately verified merged namespace. Parent action3/signal66 is an old-resource corroboration, not a universal skill mapping.",
    "Type15 payload presence is a typed signal lookup result, not damage occurrence or named skill ownership. The code also has caller-name checks, early exit and inline fallback paths; missing lookup does not universally reject creation.",
    "No automatic equality between stream config and signal payload word0 is claimed; no coverage or new confirmed Hit is claimed.",
]


def markdown(r):
    text = ["# v46：Projectile 动作索引 / signal 键静态审计", "",
        "## 关键结论", "",
        "动作字段在这条客户端路径中是**所选对象 ACT 动作数组的下标**，不是通用资源文件 ID。"
        "child 字节是**有符号 signal 查找键**；加载器把原始 signal 循环序号写入该键，"
        "不是过滤后列表序号，也不是 ACT 文件的 order 字段。", "",
        "|用途|实际接收对象/组件|原始 RVA|", "|---|---|---|",
        "|reference=0 的 signal 查找|524f80(reference) 返回对象的 +b50，按表中+4位移调整后→4f3e0|a909d0 → a90a11..a90a2d|",
        "|reference=1, UID=0 的 signal 查找|c6eda0(extra dword) 返回对象的 +16a8，按表中+4位移调整后→4f3e0|a907b4 → a90968..a90984|",
        "|reference=1, UID!=0|调用者虚槽+258/可选+320遍历两个对象，用额外qword与+1208比较，再走+16a8路径|a906a3/a9072e；a906e5/a9077d|",
        "|4ef30 动作名检查|仍是调用者 +b50，不因reference=1改成父对象|a9061e；a91396，后者查字符串Skill_|", "",
        "因此不能把 actor ACT、父对象 ACT、调用者名称检查统一为一个索引空间。"
        "父分支查到的是共享weapon/object索引对象，不能仅凭map一致断言每项都是Projectile。", "",
        "## signal 键来源与符号", "",
        "535b0加载ACT：53a94 EDI=0；53bfd [signal+a]=DI；53c10解析signal；拒绝也经53c6d EDI++；"
        "53c9d可排序保留列表；53f74符号扩展signal+a，53ffd写node+20，54000写node+28=signal。"
        "4f3e0先按动作数组取指针，再查该action+a8树，比较使用jge/jl。", "",
        "50390解析文件头：type→+8，start→+c，end→+e，order→+10；没有把文件order作为+a的赋值源。"
        "helper间接副作用未作全程序证明，报告保留原始窗口而不声称完整执行等价。", "",
        "22f346对action做int16符号扩展；22f351对child做int8符号扩展。"
        "4f3f3的mov edx,edx随后清高32位，负action经unsigned越界检查失败。"
        "child 0x80..0xff在查树时为−128..−1，不能改成128..255；"
        "普通signal ordinal128..255因此不能凭同一字节直接匹配。", "",
        "## type 0xf 与 config 的边界", "",
        "type检查在a90995，而不在4f3e0：节点+8必须为0xf，a909a0才取node+18的payload。"
        "payload提供多个行为字段；查找失败仍可能走inline定义。"
        "stream config保留在[rbp+14]，a91086→c6fae0独立使用。"
        "payload首dword在a90eac的条件分支也被使用，但本轮未找到将两者一致性作为绑定证书的依据。", "",
        "## 两个旧 ACT 文件的定点对照", "",
        "|文件/动作|本地action下标|原始signal序号|文件order|type / payload首u32|", "|---|---:|---:|---:|---|"]
    for f in r["historical_resources"]:
        for s in f["signals"]:
            text.append(f"|{Path(f['path']).name} / {f['action_name']}|{f['local_action_index']}|{s['source_signal_ordinal']}|{s['file_header_order']}|{s['type']} / {s['payload_first_u32']}|")
    text += ["", "v45已保存样本中102321使用(224,16)，102350引用102321并使用(3,66)。"
        "这里仅复用该报告：没有新读UDP、没有新接受包型。旧SoulEater文件本地下标9如何对应全局224仍依赖独立的合并顺序证明。"
        "旧父ACT的(3,66)结构吻合，但仍不代表同版本、唯一技能归属或有效父对象代际。", "",
        "## 主线可采用的约束", "",
        "1. 保留reference分支、完整对象/UID及父对象代际；不能以重复owner替代extra reference。",
        "2. 固定正确接收对象的同版本ACT集合与合并顺序，再使用动作数组下标。",
        "3. 保留int16 action/int8 child符号，按原始signal键查找，不能按过滤后数组或文件order匹配。",
        "4. 校验type15和所用payload字段；config一致、独立根技能证据与owner链仍是额外条件，不由这段代码自动保证。",
        "5. 原生早退、inline fallback及caller-only名称检查必须单独记录；不把lookup miss当作普遍非法包，也不直接提升生产Hit。", "",
        "## 复现与审计", "",
        f"固定8个证明节点：6个真实异常表范围和2个无异常表的bounded leaf；合计{r['selected_bytes']}字节。"
        "每段均保存源位置、SHA256、完整指令及关键窗口。真实exception来自原文件.data2 RVA0x2ddd290，563484 bytes/46957项。", "",
        f"client SHA256 `{r['client_sha256']}`；工具 SHA256 `{r['tool_sha256']}`。输入前后hash未变。", "",
        "指定Python 3.12加 `-B tools/trace_projectile_action_lookup_v46.py`；"
        "测试用 `-B -m unittest discover -s tests -p test_projectile_action_lookup_v46.py -v`。", "",
        "## 证明索引", ""]
    text += [f"- {f['id']}: {f['text']} Anchors: " + ", ".join(hex(x) for x in f["sites"]) for f in r["facts"]]
    text += ["", "## 未验证边界", ""] + ["- "+x for x in r["limitations"]] + [""]
    return "\n".join(text)


def main():
    report = build()
    OUT.mkdir(parents=True, exist_ok=True)
    files = [OUT / "action_lookup.json", OUT / "action_lookup.md"]
    files[0].write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    files[1].write_text(markdown(report),encoding="utf-8")
    print(json.dumps(dict(nodes=report["selected_node_count"],bytes=report["selected_bytes"],
        anchors=len(report["anchors"]),changed_inputs=report["changed_inputs"],
        outputs={str(p):digest(p.read_bytes()) for p in files})))


if __name__ == "__main__":
    main()
