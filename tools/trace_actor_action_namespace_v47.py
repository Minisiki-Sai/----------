"""Bounded byte-only ACT manifest/namespace audit. Never loads native code.

Run Python 3.12 -B. Only action_namespace.json/md in the fixed output directory
are generated. The small merge model covers successful cold loads, not the
engine, filesystem resolver, cache lifecycle, or production attribution.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from review_client_damage_types_v44 import StaticView, CodeReview, capstone
from probe_magma_signal_flags_v36 import parse_act6

OUT = ROOT / "outputs/native_wire_v47_20260909"
OLD = Path("E:/ext0811.rar/Resource00/Resource00/resource/char/player/kali")
V44_SHA = "5cb4a393f7b7bab7001aaa36991fad25035d2a71968ae002253bdba0d279a5de"
V46_SHA = "28c28a13cdec2958b5871fbb28fed79fd521a362d12517b624d64e66bffcdb3c"
MAGIC = b"Eternity Action File\0"
# Ten NEW original exception ranges; the final two are adjacent fragments of
# one post-load operation. Do not manufacture a single larger function range.
NODES = (0x528B0, 0x52DD0, 0x4EDF0, 0x52AE0, 0xDFCBAC, 0xE12DD4,
         0x4FF9B0, 0x5283C0, 0x4FD470, 0x4FD490)
REUSED = (0x535B0, 0x4EF30, 0x4F3E0)


def digest(data):
    return sha256(data).hexdigest()


def manifest_tokens(raw):
    """52ef2/53177 -> dfcbac -> e12dd4: CR/LF delimiters, C-string end.

    No whitespace trimming, comment removal, BOM stripping, sorting, case
    folding, path resolution, or deduplication is inferred here.
    """
    return tuple(x for x in re.split(b"[\r\n]+", raw.split(b"\0", 1)[0]) if x)


def merge_model(manifest, files):
    """Conditional safe-input model of 52dd0, preserving raw body byte order.

    files is an explicitly supplied token->bytes/None resolution. None means
    failed open; invalid signatures are skipped by native. Truncated headers,
    negative counts, empty accepted sets and overflow are rejected here, NOT
    claims about native behavior on malformed inputs. No guest code executes.
    """
    accepted, rejected, bodies, count = [], [], [], 0
    header = None
    for ordinal, token in enumerate(manifest_tokens(manifest)):
        raw = files.get(token)
        if raw is None:
            rejected.append(dict(token_hex=token.hex(), ordinal=ordinal, reason="unavailable"))
            continue
        if len(raw) < 40:
            raise ValueError("short ACT header outside safe-input model")
        # 52f7b overwrites the shared header BEFORE its signature test. A final
        # wrong-signature opened file can leave the synthetic header invalid.
        header = bytearray(raw[:40])
        if not raw.startswith(MAGIC):
            rejected.append(dict(token_hex=token.hex(), ordinal=ordinal, reason="signature"))
            continue
        version, actions = struct.unpack_from("<ii", raw, 32)
        if actions < 0 or count + actions > 0x7FFFFFFF:
            raise ValueError("signed count/overflow outside safe-input model")
        accepted.append(dict(token_hex=token.hex(), ordinal=ordinal, first_action=count,
                             action_count=actions, version=version,
                             bytes=len(raw), sha256=digest(raw)))
        bodies.append(raw[40:])
        count += actions
    if not accepted:
        raise ValueError("no accepted ACT; native empty-list path not modeled")
    struct.pack_into("<i", header, 36, count)
    return dict(accepted=accepted, rejected=rejected, action_count=count,
                final_header_signature_valid=header.startswith(MAGIC),
                merged_bytes=bytes(header) + b"".join(bodies))


def global_index(accepted, source_token_ordinal, local_action):
    """Source token ordinal distinguishes repeated filenames."""
    entry = next((e for e in accepted if e["ordinal"] == source_token_ordinal), None)
    if entry is None or not 0 <= local_action < entry["action_count"]:
        raise ValueError("unknown source or out-of-range local action")
    return entry["first_action"] + local_action


def assert_anchor(rows, rva, mnemonic, operands=None, target=None):
    row = rows.get(rva)
    if (row is None or row["mnemonic"] != mnemonic
            or operands is not None and row["operands"] != operands
            or target is not None and row.get("direct_branch_target") != target):
        raise ValueError(f"instruction anchor changed: {rva:#x}")
    return row


ANCHORS = (
    (0x4EDFA, "cmp", "qword ptr [rcx + 0x30], 0"),
    (0x4EE3C, "mov", "qword ptr [rdi + 0x30], rax"),
    (0x52A57, "test", "dil, dil"),
    (0x52EF2, "lea", "rdx, [rip + 0xf511cf]"),
    (0x52F53, "test", "al, al"),
    (0x52F55, "jne", "0x140052f6a"),
    (0x52F8E, "test", "eax, eax"),
    (0x52F90, "je", "0x140052fa4"),
    (0x53071, "mov", "dword ptr [rsp + 0x44], r15d"),
    (0x53080, "mov", "dword ptr [rdx], r15d"),
    (0x53098, "add", "r15d, dword ptr [rbp + 0x94]"),
    (0x530AE, "lea", "ebx, [rax - 0x28]"),
    (0x530E0, "mov", "qword ptr [rdi], r15"),
    (0x53145, "mov", "eax, dword ptr [rbp + 0x90]"),
    (0x5314B, "mov", "dword ptr [rdx], eax"),
    (0x53177, "lea", "rdx, [rip + 0xf50f4a]"),
    (0x5317E, "xor", "ecx, ecx"),
    (0x5318B, "jne", "0x140052f20"),
    (0x531D3, "mov", "dword ptr [rbp + 0x94], r15d"),
    (0x53224, "mov", "rdx, qword ptr [rsi + rax*8]"),
    (0x53250, "inc", "r14d"),
    (0x53259, "jb", "0x140053220"),
    (0x532FC, "mov", "byte ptr [rsp + 0x30], 1"),
    (0x53314, "lea", "r9, [rsp + 0x48]"),
    (0x52CA9, "mov", "rax, qword ptr [r14 + 0x40]"),
    (0x52CAD, "inc", "dword ptr [rax]"),
    (0x52D84, "lea", "r15, [rbx + 0x28]"),
    (0xE12E3E, "mov", "r9, qword ptr [rdi]"),
    (0xE12EC2, "mov", "byte ptr [r9], r10b"),
    (0xE12ECB, "mov", "qword ptr [rdi], r9"),
    (0x53990, "cmp", "r12d, dword ptr [rcx]"),
    (0x53993, "jge", "0x1400539a3"),
    (0x539B0, "lea", "rcx, [r15 + 0xc0]"),
    (0x539D8, "mov", "dword ptr [r15 + 0xbc], ecx"),
    (0x54095, "mov", "qword ptr [rdx], r15"),
    (0x54098, "add", "qword ptr [r13 + 8], 8"),
    (0x540AD, "inc", "r12d"),
    (0x540B9, "jl", "0x1400538b2"),
    (0x4EF4E, "mov", "rax, qword ptr [rax + rdx*8]"),
    (0x4FD584, "mov", "dword ptr [rdx + rbx*4], eax"),
)
CALLS = ((0x4EE37, 0x528B0), (0x52A5C, 0x52DD0), (0x52A75, 0x535B0),
         (0x52EFC, 0xDFCBAC), (0x53180, 0xDFCBAC), (0x53328, 0x535B0),
         (0x53243, 0xDD0840), (0x4FF9BD, 0x4EDF0), (0x5283E2, 0x4FF9B0),
         (0x4FF9CC, 0x4FD470))


FACTS = (
    ("N1", "Component+30 is installed from 528b0's result; a pre-existing non-null +30 bypasses loading. 4ef30 subsequently indexes exactly this vector.", [0x4EDFA, 0x4EE37, 0x4EE3C, 0x4EF4E]),
    ("N2", "528b0 probes a 40-byte header. Binary Eternity Action File selects 535b0; short/nonbinary .act-list route selects 52dd0. Selection is content-based, not a universal filename-extension classification.", [0x529D1, 0x529F6, 0x52A0B, 0x52A34, 0x52A57, 0x52A5C, 0x52A75]),
    ("N3", "52dd0 tokenizes in input order with delimiter bytes LF/CR. dfcbac delegates to e12dd4 with saved continuation state; empty CR/LF spans are skipped. Spaces, case, comments and BOM are not normalized by this tokenizer.", [0x52EF2, 0x52EFC, 0x53177, 0x53180, 0x5318B, 0xDFCBD5, 0xE12E3E, 0xE12EC2, 0xE12ECB]),
    ("N4", "Each token is opened and its ACT signature checked. Failed open or wrong signature skips it. Accepted entries append a filename, current cumulative action count, body bytes, body length and source version; duplicate tokens are not deduplicated in this loop.", [0x52F34, 0x52F3A, 0x52F53, 0x52F55, 0x52F8E, 0x52F90, 0x53071, 0x53080, 0x53098, 0x530AE, 0x530E0, 0x53145, 0x5314B]),
    ("N5", "The synthetic stream uses the shared last-read 40-byte header buffer with total accepted action count at +36, followed by accepted file bodies in token order. A trailing opened wrong-signature file overwrites this header before rejection; do not assume successful load then. Per-file start-index and version arrays also reach 535b0; mixed versions cannot be parsed using only the final header version.", [0x52F7B, 0x52F90, 0x531D3, 0x531EB, 0x53224, 0x53243, 0x53250, 0x53259, 0x53301, 0x5330B, 0x53314, 0x53328]),
    ("N6", "535b0 selects the last cumulative file-start <= current action ordinal, stores its filename at action+c0 and version at +bc, then appends each successfully allocated action pointer in stream order. Signal filtering does not renumber the surrounding action loop.", [0x53990, 0x53993, 0x539B0, 0x539D8, 0x53A22, 0x54095, 0x54098, 0x540AD, 0x540B9]),
    ("N7", "52ae0 can return an existing filename-keyed container before any new merge. Cache-hit pointer is container+28, with a separate name-index reference supplied to receiver+28. New resource bytes alone do not prove the already-loaded runtime namespace.", [0x52B78, 0x52CA9, 0x52CAD, 0x52CBC, 0x52D84, 0x52D9F]),
    ("N8", "4ff9b0 wraps component loading; 5283c0 adjusts an incoming subobject before calling it. Inspected post-load fragments build a separate dword vector at component+70 indexed by unchanged action positions; delegated helpers and 531c30 remain outside this proof.", [0x5283CA, 0x5283D4, 0x5283DB, 0x5283E2, 0x4FF9BD, 0x4FF9CC, 0x4FD48C, 0x4FD540, 0x4FD584]),
)


def historical_example(view, prior_v46):
    manifest = view.load(OLD / "kali.act")
    files = {token: view.load(OLD / token.decode("ascii")) for token in manifest_tokens(manifest)}
    merged = merge_model(manifest, files)
    token = b"Kali_Skill_2st_SOULEATER.act"
    ordinal = manifest_tokens(manifest).index(token)
    raw = files[token]
    previous = next(r for r in prior_v46["historical_resources"]
                    if r["action_name"] == "Skill_BeastSpirit_EX")
    if digest(raw) != previous["sha256"]:
        raise ValueError("historical ACT differs from hash-pinned v46 evidence")
    parsed = parse_act6(raw)
    action = parsed["actions"][9]
    if action["name"] != "Skill_BeastSpirit_EX":
        raise ValueError("historical action anchor changed")
    signal = action["signals"][16]
    if (signal["type"], signal["u32"][0], signal["order"]) != (15, 11075, 3):
        raise ValueError("historical signal anchor changed")
    prior = json.loads(view.load(ROOT / "outputs/projectile_evidence_v45_20260909/stream_fixture_audit.json"))
    observations = [r for r in prior["results"] if r["expected_object"] == 102321]
    index = global_index(merged["accepted"], ordinal, 9)
    preceding = [r["action_count"] for r in merged["accepted"] if r["ordinal"] < ordinal]
    if preceding != [45,39,55,41,35] or index != 224:
        raise ValueError("historical manifest prefix changed; re-review narrative")
    if (not observations or any((r["parsed"]["action_resource"],
                                 r["parsed"]["child_sequence"],
                                 r["expected_template"]) != (224,16,11075)
                                for r in observations)):
        raise ValueError("saved wire observation changed; re-review corroboration")
    return dict(manifest_path=str(OLD / "kali.act"), manifest_sha256=digest(manifest),
        token_count=len(manifest_tokens(manifest)), files=merged["accepted"],
        total_header_action_count=merged["action_count"],
        synthesized_sha256=digest(merged["merged_bytes"]),
        synthesized_bytes=len(merged["merged_bytes"]),
        example=dict(file=token.decode(), file_local_action=9, file_first_action=215,
                     cumulative_counts=preceding, global_action=index,
                     action_name=action["name"], action_offset=action["offset"],
                     signal_ordinal=16, signal_offset=signal["offset"],
                     type=15, payload_first_u32=11075, file_order=3),
        prior_wire_observations=observations,
        prior_observation_replay=False,
        current_resource_identity_verified=False, new_certified_mappings=0,
        boundary="Independent OLD manifest arithmetic matches saved wire (224,16). This is corroboration, not current-version resource equality or new named-Hit certification.")


LIMITATIONS = [
    "Offline file bytes and host disassembly only; no client/DLL loading, native guest execution, live memory, game changes or production modifications.",
    "Ten new original exception ranges (including two adjacent post-load fragments), plus three hash-pinned v46 nodes. No broad RTTI inventory or general call graph.",
    "Post-load ranges 4fd470..490 and 4fd490..5b6 are kept separate; the subsequent epilogue, delegated helpers, virtual callbacks and 531c30 are unreviewed. Complete absence of later namespace mutation is not claimed.",
    "Successful cold-load ordering is proved; allocator failure, corrupt/truncated ACT inputs, cumulative overflow, all-invalid manifests and concurrency are outside the safe model.",
        "Resource opener 1eeed0 and actual actor/template-to-manifest selection are not traced. Relative path resolution, PAK precedence, hot reload and current class/job overrides remain external conditions.",
    "The cache can retain earlier loaded data; current on-disk manifest is not sufficient to identify an existing object namespace. Runtime cache/lifetime is not observed.",
    "Old Kali manifest plus 18 referenced ACT files are historical evidence only. Headers use versions 1 and 6; only the selected SoulEater ACT6 file is fully parsed. Merged mixed-version bytes are not fed to a single-version parser.",
    "The (224,16)/11075 alignment is copied from a hash-recorded v45 wire audit, not a new raw replay. Current ACT bytes/manifest, actual receiver route, and same-version identity are still missing for a new certificate.",
    "Decoded streams retain v44's final-section-semantics limitation. Static ordering is conditional code evidence, not a measured 95% coverage result.",
]


def build():
    helper = ROOT / "tools/review_client_damage_types_v44.py"
    if digest(helper.read_bytes()) != V44_SHA:
        raise ValueError("v44 helper changed")
    view = StaticView()
    prior_path = ROOT / "outputs/projectile_gap_v46_20260909/action_lookup.json"
    prior_raw = view.load(prior_path)
    if digest(prior_raw) != V46_SHA:
        raise ValueError("v46 report changed")
    prior = json.loads(prior_raw)
    for path in (helper, Path(__file__), ROOT / "tests/test_actor_action_namespace_v47.py",
                 ROOT / "tools/probe_magma_signal_flags_v36.py"):
        view.load(path)
    code = CodeReview(view)
    nodes, rows = [], {}
    for start in NODES:
        function = view.function(start)
        if function is None or function[0] != start or function[1]-start > 8192:
            raise ValueError("unexpected original exception range")
        body = view.read(start, function[1]-start)
        instructions = [code.row(i) for i in code.decode(function)]
        if sum(len(bytes.fromhex(r["bytes"])) for r in instructions) != len(body):
            raise ValueError("incomplete selected range decode")
        nodes.append(dict(start=start, runtime_function=function, size=len(body),
            sha256=digest(body), provenance=view.provenance(start,len(body)),
            exception_record_rva=view.exception["rva"]+view.starts.index(start)*12,
            instructions=instructions))
        rows.update((r["rva"],r) for r in instructions)
    if len(nodes) != 10 or sum(n["size"] for n in nodes) > 16384:
        raise ValueError("bounded new-node budget exceeded")
    reused = []
    for start in REUSED:
        node = next(n for n in prior["nodes"] if n["start"] == start)
        if digest(view.read(start,node["size"])) != node["sha256"]:
            raise ValueError("reused v46 byte hash mismatch")
        rows.update((r["rva"],r) for r in node["instructions"])
        reused.append(node)
    anchors = [assert_anchor(rows,*args) for args in ANCHORS]
    anchors += [assert_anchor(rows,site,"call",target=target) for site,target in CALLS]
    anchors.append(assert_anchor(rows,0xDFCBD5,"jmp",target=0xE12DD4))
    literals = []
    for rva, raw in ((0xFA40A8,MAGIC),(0xFA40C0,b".act\0"),(0xFA40C8,b"\n\r\0")):
        if view.read(rva,len(raw)) != raw:
            raise ValueError("literal mismatch")
        literals.append(dict(rva=rva,raw=raw.hex(),provenance=view.provenance(rva,len(raw))))
    facts = [dict(id=key,text=text,sites=sites,instructions=[rows[a] for a in sites])
             for key,text,sites in FACTS]
    example = historical_example(view, prior)
    changed = [p for p,h in view.inputs.items() if digest(Path(p).read_bytes()) != h]
    if changed:
        raise ValueError(f"inputs changed during audit: {changed}")
    return dict(schema="actor-action-namespace-v47",client_sha256=digest(view.raw),
        tool_sha256=digest(Path(__file__).read_bytes()), inputs=view.inputs,
        exception_directory=view.exception, capstone_version=capstone.__version__,
        new_range_count=len(nodes),new_bytes=sum(n["size"] for n in nodes),
        nodes=nodes,reused_v46_nodes=reused,anchors=anchors,literals=literals,
        facts=facts,historical_example=example,limitations=LIMITATIONS,
        changed_inputs=changed,new_certified_mappings=0,production_changes=False)


def markdown(report):
    e = report["historical_example"]
    lines = ["# v47 ACT 合并顺序 / 动作索引命名空间", "",
        "## 阶段结论", "",
        "已证明成功冷加载路径的顺序：清单的 CR/LF token 顺序 → 跳过打不开/签名错误文件 → "
        "有效文件按原顺序拼接 ACT body → 535b0 按 stream 动作顺序追加指针 → "
        "4edf0 保存到组件+30 → 4ef30 直接以全局 action 下标取指针。不是目录顺序、文件名排序或动作名称去重。", "",
        "旧 Kali 清单中 SoulEater 之前的动作数为 45+39+55+41+35=215，"
        "其本地 action9 因此为全局224。signal16 是 type15，payload首u32=11075、文件order=3。"
        "与保存的 object102321 wire (224,16) 数值吻合，但尚无当前版本 ACT/清单身份，**新增认证映射为0**。", "",
        "## 可复核字段与代码", "",
        "|字段/步骤|证据 RVA|结果|", "|---|---|---|",
        "|组件动作容器|4edfa / 4ee37 / 4ee3c / 4ef4e|已有+30不重载；加载返回vector后存+30；index×8取action|",
        "|入口分派|528b0，52a5c / 52a75|清单走52dd0，二进制ACT走535b0|",
        "|清单顺序|52ef2 / 53177 → dfcbac → e12dd4|仅LF/CR分隔；不trim、不排序、不去重|",
        "|文件base ordinal|53071 / 53080 / 53098|先记累计动作数，再加该文件header+36 count|",
        "|拼接|530ae / 53224 / 53243 / 53250|去掉每个40字节头，按接受顺序拷贝body|",
        "|各文件版本|53145 / 5314b / 53301；539d8|源header+32分别保存，加载到action+bc|",
        "|源文件身份|53314；53990..539cd|按最后一个base≤动作序号取源文件名，写action+c0|",
        "|action索引|54095 / 54098 / 540ad|顺序追加指针；无signal过滤后动作重编号证据|",
        "|缓存条件|52ae0，52ca9 / 52d84|可直接复用旧容器；磁盘文件不等于当前对象已加载空间|", "",
        "## 旧资源独立对照", "",
        f"固定读取 Kali 清单及其 {e['token_count']} 个引用文件（不是资源全扫描）；"
        f"header合计 {e['total_header_action_count']} 个动作。各文件SHA、版本、起始索引和大小见JSON。"
        "源文件版本混合1/6，因此仅合成字节和审计元数据，不能把合并文件全按末尾版本6解析。", "",
        "失败条件须单独保留：52f7b先覆盖共享header缓冲，52f90才决定签名是否有效。"
        "若最后一个成功打开的文件签名错误，它虽不贡献动作body，仍可能使最终合成header无效；"
        "不能把“跳过坏文件”实现成无条件成功。本模型有相应合成负控。", "",
        "v45观察字段来自stream_fixture_audit.json（本报告记录其hash），未重新运行UDP回放。"
        "本轮解决的是 local9→global224 的旧清单计算及 native 顺序机制，不是旧资源与当前客户端版本等价。", "",
        "## 主线仍需提供的条件", "",
        "1. 当前接收对象实际选中的清单/单ACT及同版本资源身份；actor/template→清单选择尚未追踪。",
        "2. 文件解析路径、PAK优先级、成功/失败打开集合以及缓存/重载代际。",
        "3. 正确receiver空间；父weapon ACT不可与actor或caller ACT混用。",
        "4. 同版本type15 payload、config关系、对象生代和根动作证据；本报告不以字符串或数值吻合直接认证Hit。", "",
        "## 范围和复现", "",
        f"新增 {report['new_range_count']} 个真实异常表范围，共 {report['new_bytes']} bytes；"
        f"复用3个v46节点；{len(report['anchors'])} 条严格指令断言。"
        "4fd470/4fd490是相邻但独立的异常范围，不伪造完整函数边界。"
        "异常目录来自原文件.data2 RVA0x2ddd290，563484 bytes / 46957项。", "",
        "`python.exe -B tools/trace_actor_action_namespace_v47.py`", "",
        "`python.exe -B -m unittest discover -s tests -p test_actor_action_namespace_v47.py -v`", "",
        f"Client SHA256 `{report['client_sha256']}`；工具 SHA256 `{report['tool_sha256']}`。", "",
        "## 逐指令证明索引", ""]
    lines += [f"- {f['id']}: {f['text']} Sites: "+", ".join(hex(x) for x in f["sites"])
              for f in report["facts"]]
    lines += ["", "## 未验证边界", ""] + ["- "+s for s in LIMITATIONS] + [""]
    return "\n".join(lines)


def main():
    report = build()
    OUT.mkdir(parents=True,exist_ok=True)
    paths = (OUT / "action_namespace.json",OUT / "action_namespace.md")
    paths[0].write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    paths[1].write_text(markdown(report),encoding="utf8")
    print(json.dumps(dict(new_ranges=report["new_range_count"],new_bytes=report["new_bytes"],
        anchors=len(report["anchors"]),new_certified_mappings=0,
        outputs={str(p):digest(p.read_bytes()) for p in paths})))


if __name__ == "__main__":
    main()
