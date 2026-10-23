"""Bounded, byte-only WeaponTable/config audit. Run with Python 3.12 -B.

No guest execution, native client/DLL loading, live memory, protection changes,
network operations, production writes, or identity/+0x1bc0 tracing. Writes only
config.json/config.md under the fixed sidecar output directory. The v44 reader
rechecks original-file exception metadata and all six decoded-stream hashes.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from review_client_damage_types_v44 import StaticView, CodeReview, BASE, capstone

OUT = ROOT / "outputs/projectile_evidence_v45_20260909"
RESOURCE = Path("E:/ext0811.rar/Resource00/Resource00/resource/ext")
V44_SHA = "5cb4a393f7b7bab7001aaa36991fad25035d2a71968ae002253bdba0d279a5de"
MAX_FUNCTIONS, MAX_CODE_BYTES = 32, 128 * 1024
EXCLUDED_FUNCTIONS = {0xA8DEA0, 0xA90370, 0xC69EB0}


def digest(data):
    return sha256(data).hexdigest()


def parse_dnt(data):
    """Strict old-resource subset, with exact row/cell offsets; no ID mapping.

    Layout follows the existing build_live_dps_monster_catalog.py parser.
    Unlike that compatibility reader, this audit rejects replacement decoding,
    unknown kinds, duplicate fields, non-finite floats, and trailing bytes.
    Duplicate row IDs are retained as distinct records and explicitly reported;
    no dictionary overwrite or current-client override policy is assumed.
    The initial four bytes are retained as opaque, not an invented version.
    """
    if not 10 <= len(data) <= 16 * 1024 * 1024:
        raise ValueError("DNT byte budget/header")
    pos = 4

    def take(size):
        nonlocal pos
        if size < 0 or pos + size > len(data):
            raise ValueError(f"truncated DNT at {pos:#x}")
        value = data[pos:pos + size]
        pos += size
        return value

    def number(fmt):
        return struct.unpack(fmt, take(struct.calcsize(fmt)))[0]

    columns, count = number("<H"), number("<I")
    if not 0 < columns <= 1024 or count > 100000:
        raise ValueError("DNT row/column budget")
    fields, names = [], {"RowId"}
    for _ in range(columns):
        start = pos
        name = take(number("<H")).decode("ascii")
        kind = number("<B")
        if not name or name in names or kind not in (1, 2, 3, 4, 5):
            raise ValueError("duplicate/empty DNT column or unsupported kind")
        names.add(name)
        fields.append(dict(name=name, kind=kind, offset=start, size=pos-start))
    rows, row_ids = [], Counter()
    for _ in range(count):
        start = pos
        row_id = number("<I")
        row_ids[row_id] += 1
        cells = {"RowId": dict(value=row_id, offset=start, size=4)}
        for field in fields:
            at = pos
            kind = field["kind"]
            if kind == 1:
                value = take(number("<H")).decode("utf-8")
            elif kind in (2, 3):
                value = number("<i")
            else:
                value = number("<f")
                if not -float("inf") < value < float("inf"):
                    raise ValueError("non-finite DNT float")
            cells[field["name"]] = dict(value=value, offset=at, size=pos-at)
        rows.append(dict(offset=start, size=pos-start, cells=cells))
    if pos != len(data):
        raise ValueError("unparsed DNT trailing bytes")
    return dict(opaque_header=data[:4].hex(), fields=fields, rows=rows,
                row_count=count, parsed_bytes=pos,
                duplicate_row_ids={key: n for key, n in row_ids.items() if n > 1})


def relative_candidates(data, base, targets):
    """Prefilter only; even a displacement/immediate may contain E8/E9."""
    result = []
    for match in re.finditer(b"[\xe8\xe9]", data):
        at = match.start()
        if at + 5 <= len(data):
            target = base + at + 5 + struct.unpack_from("<i", data, at+1)[0]
            if target in targets:
                result.append((base+at, target))
    return result


def matching_branch(rows, rva, target):
    return next((row for row in rows if row["rva"] == rva
                 and row["mnemonic"] in ("call", "jmp")
                 and row.get("direct_branch_target") == target), None)


class Audit:
    def __init__(self, view):
        self.view, self.code = view, CodeReview(view)
        self.functions, self.windows, self.anchors = {}, [], []

    def rows(self, rva):
        function = self.view.function(rva)
        if function is None:
            raise ValueError(f"no original exception range: {rva:#x}")
        start, end, unwind = function
        if start in EXCLUDED_FUNCTIONS:
            raise ValueError("main-owned function excluded")
        if start not in self.functions:
            if (len(self.functions) >= MAX_FUNCTIONS or end-start > 32768
                    or sum(x["size"] for x in self.functions.values()) + end-start > MAX_CODE_BYTES):
                raise ValueError("bounded code budget")
            body = self.view.read(start, end-start)
            ins = self.code.decode(function)
            self.functions[start] = dict(begin=start, end=end, unwind=unwind,
                size=len(body), sha256=digest(body),
                exception_record_rva=self.view.exception["rva"] + self.view.starts.index(start)*12,
                provenance=self.view.provenance(start, end-start),
                decoded_through=ins[-1].address + ins[-1].size - BASE if ins else start)
        return [self.code.row(i) for i in self.code.decode(function)]

    def window(self, label, start, end):
        rows = [r for r in self.rows(start) if start <= r["rva"] < end]
        if not rows or rows[0]["rva"] != start or rows[-1]["rva"] + len(bytes.fromhex(rows[-1]["bytes"])) < end:
            raise ValueError(f"window lacks contiguous boundary coverage: {label}")
        if any("0x1bc0" in r["operands"] for r in rows):
            raise ValueError("identity field is outside this sidecar")
        self.windows.append(dict(label=label, start=start, end=end,
                                 function=self.view.function(start)[0], instructions=rows))

    def expect(self, rva, mnemonic, operands=None, target=None):
        row = next((r for r in self.rows(rva) if r["rva"] == rva), None)
        if (row is None or row["mnemonic"] != mnemonic
                or operands is not None and row["operands"] != operands
                or target is not None and row.get("direct_branch_target") != target):
            raise ValueError(f"evidence anchor mismatch {rva:#x}: {row}")
        self.anchors.append(row)
        return row

    def string(self, rva, expected):
        raw = expected.encode("ascii") + b"\0"
        if self.view.read(rva, len(raw)) != raw:
            raise ValueError(f"string anchor mismatch {rva:#x}")
        return dict(rva=rva, text=expected, raw=raw.hex(), sha256=digest(raw),
                    provenance=self.view.provenance(rva, len(raw)))


def resource_evidence(view):
    names = ["weapontable.dnt", "weapontable_projectile.dnt", "weapontable_equipment.dnt",
             "skilltable_character.dnt", "skillleveltable_characteracademic.dnt"]
    reports, parsed = [], {}
    weapon_fields = ["RowId", "_EquipType", "_ActName", "_Length", "_Durability", "_SetItemID"]
    for name in names:
        path = RESOURCE / name
        raw = view.load(path)
        table = parse_dnt(raw)
        parsed[name] = table
        fields = weapon_fields if name.startswith("weapon") else (
            ["RowId", "_StaticName", "_Processor1", "_Processor2", "_Processor3", "_Processor4", "_Processor5"]
            if name.startswith("skilltable") else ["RowId", "_SkillIndex", "_SkillLevel", "_ProcessParam1", "_ProcessParam2"])
        samples = []
        for row in table["rows"][:3]:
            cells = {key: dict(row["cells"][key]) for key in fields}
            for cell in cells.values():
                cell["raw"] = raw[cell["offset"]:cell["offset"]+cell["size"]].hex()
            samples.append(dict(row_offset=row["offset"], row_size=row["size"], cells=cells))
        reports.append(dict(path=str(path), sha256=digest(raw), bytes=len(raw),
            opaque_header=table["opaque_header"], row_count=table["row_count"],
            duplicate_row_ids=table["duplicate_row_ids"],
            schema=table["fields"], parsed_all_bytes=table["parsed_bytes"] == len(raw), samples=samples))
    skills = parsed["skilltable_character.dnt"]["rows"]
    hits = [r["cells"]["RowId"]["value"] for r in skills
            if any(r["cells"][f"_Processor{i}"]["value"] == 3 for i in range(1, 6))]
    schema_equal = len({tuple((f["name"], f["kind"]) for f in parsed[n]["fields"])
                        for n in names[:3]}) == 1
    return dict(files=reports, weapon_schemas_equal=schema_equal,
        processor3_character_skill_rows=hits, character_skill_rows_examined=len(skills),
        boundary="Old resource snapshot only; not a current-client table dump. No runtime ID equivalence. "
                 "No type-3 processor in this character SkillTable subset, so no skill/config pair inferred.")


def build():
    source = ROOT / "tools/review_client_damage_types_v44.py"
    if digest(source.read_bytes()) != V44_SHA:
        raise ValueError("v44 reader changed; re-review before reuse")
    view = StaticView()
    for path in [source, Path(__file__), ROOT / "tools/build_live_dps_monster_catalog.py"]:
        view.load(path)
    audit = Audit(view)
    # Ranges here select evidence only. Function bounds are always original .data2 entries.
    windows = [
        ("processor constructor", 0x29D280, 0x29D3CA),
        ("processor non-destructor method", 0x29D5E0, 0x29D6C2),
        ("config key and five named columns", 0xC6FAE0, 0xC6FC0D),
        ("table selector", 0x350770, 0x350843),
        ("registry key2 WeaponTable", 0x35ADED, 0x35AE12),
        ("registry key12 SkillTable", 0x35AF4B, 0x35AF6B),
        ("registry key19 SkillLevelTable", 0x35B0FB, 0x35B120),
        ("descriptor registration", 0x34F460, 0x34F6E5),
        ("lazy loader reads descriptor then installs selected table", 0x34F2F0, 0x34F459),
        ("loaded-table map insertion", 0x34F210, 0x34F2E6),
        ("table loading .dnt and stream-vector setup", 0x359C00, 0x359E04),
        ("basename extension wildcard enumeration", 0x34FE30, 0x34FF18),
        ("basename extraction", 0x22D640, 0x22D70C),
        ("extension extraction", 0x22D5C0, 0x22D640),
        ("column getter", 0x235960, 0x235A15),
        ("factory table19 and bounded switch", 0x369F00, 0x369F96),
        ("kind3 second textual parameter into R9D", 0x36A726, 0x36A7FD),
        ("numeric helper forwards base10", 0xDECC24, 0xDECC4D),
        ("SkillTable/SkillLevelTable selected by factory caller", 0xB9B2A6, 0xB9B2F3),
        ("caller zero-based parameter cursor and ProcessorN selection", 0xB9B7E4, 0xB9B869),
        ("caller sends processor kind and separate level row key", 0xB9B924, 0xB9B941),
    ]
    for label, start, end in windows:
        audit.window(label, start, end)
    # Exact semantic assertions fail closed when the pinned corpus changes.
    for rva, operands in [
        (0x29D292, "edi, r9d"), (0x29D295, "ebx, r8d"),
        (0x29D2CF, "dword ptr [rsi + 0x30], ebx"),
        (0x29D2D2, "dword ptr [rsi + 0x34], edi"),
        (0x29D377, "edx, dword ptr [rsi + 0x34]"),
        (0x29D69B, "edx, dword ptr [rbx + 0x34]"),
        (0xC6FB09, "dword ptr [rcx + 0x1164], edx"), (0xC6FB1C, "edx, 2"),
        (0xC6FB39, "edx, dword ptr [rbx + 0x1164]"),
        (0x35AE05, "edx, 2"), (0x35B113, "edx, 0x13"),
        (0x36A7E6, "r9d, edi"), (0x36A7E9, "r8d, ebx"),
        (0xDECC3D, "r8d, 0xa"),
    ]:
        audit.expect(rva, "mov", operands)
    for rva, target in [
        (0x29D342, 0xA8D190), (0x29D37D, 0xC6FAE0), (0x29D65E, 0xA8D190),
        (0x29D6A1, 0xC6FAE0), (0xC6FB28, 0x350770), (0xC6FB42, 0x235AA0),
        (0x35AE0D, 0x34F460), (0x35AF66, 0x34F460), (0x35B11B, 0x34F460),
        (0x3507E7, 0x34F2F0), (0x34F3F9, 0x359C00), (0x34F407, 0x34F210),
        (0x359D05, 0x34FE30), (0x359DD0, 0x35A780), (0x359DF9, 0x234310),
        (0x34FEF2, 0x1ECAA0), (0x36A746, 0x235960), (0x36A783, 0x235960),
        (0x36A7A7, 0xDECC24), (0x36A7B1, 0xDECC24), (0x36A7F0, 0x29D280),
        (0xB9B93C, 0x369F00),
    ]:
        audit.expect(rva, "call", target=target)
    strings = [audit.string(rva, text) for rva, text in [
        (0xE983A0, "WeaponTable"), (0xE984A8, "SkillTable"), (0xE985D8, "SkillLevelTable"),
        (0xE96300, "_SkillIndex"), (0xE9E1F8, "_ProcessParam%d"),
        (0xEF1178, "_Processor%d"), (0xE9AB04, "%s*.%s"), (0xE9AB0C, "ext"),
    ]]
    fields = []
    for name, lea, call, writes in [
        ("_Length", 0xC6FB4F, 0xC6FB5B, [0xC6FB6D]),
        ("_Durability", 0xC6FB73, 0xC6FB7F, [0xC6FB8F, 0xC6FB95]),
        ("_DurabilityRepairCoin", 0xC6FB9B, 0xC6FBA7, [0xC6FBB7]),
        ("_EquipType", 0xC6FBBD, 0xC6FBC9, [0xC6FBD9]),
        ("_SetItemID", 0xC6FBEB, 0xC6FBF7, [0xC6FC07]),
    ]:
        row = audit.expect(lea, "lea")
        literal = audit.string(row["rip_relative_targets"][0], name)
        audit.expect(call, "call", target=0x235960)
        stores = [audit.expect(rva, "mov") for rva in writes]
        fields.append(dict(column=literal, lea_rva=lea, getter_call_rva=call, stores=stores))
    # Leaf lacking unwind info: exactly 77 bytes, two returns, NOT an invented function.
    leaf_rva, leaf_size = 0x235AA0, 77
    if view.function(leaf_rva) is not None:
        raise ValueError("row-exists leaf unexpectedly has exception metadata")
    raw = view.read(leaf_rva, leaf_size)
    instructions = [audit.code.row(i) for i in audit.code.dis.disasm(raw, BASE+leaf_rva)]
    if (sum(len(bytes.fromhex(r["bytes"])) for r in instructions) != leaf_size
            or [r["rva"] for r in instructions if r["mnemonic"] == "ret"] != [0x235AE1, 0x235AEC]):
        raise ValueError("bounded row-exists leaf changed")
    leaf = dict(rva=leaf_rva, byte_count=leaf_size, sha256=digest(raw), raw=raw.hex(),
        runtime_function=None, provenance=view.provenance(leaf_rva, leaf_size), instructions=instructions,
        boundary="Direct-call-target 77-byte sample only; no inferred exception range.")
    switch_raw = view.read(0x36B5A0, 21*4)
    switch = list(struct.unpack("<21I", switch_raw))
    if switch[2] != 0x36A726 or view.function(switch[2]) != view.function(0x369F00):
        raise ValueError("kind3 switch destination changed")
    switch_evidence = dict(rva=0x36B5A0, raw=switch_raw.hex(), sha256=digest(switch_raw),
        entry_count=21, selector="incoming EDX - 1, checked <= 20", kind=3, slot=2,
        target=switch[2], provenance=view.provenance(0x36B5A0, len(switch_raw)))
    # Only two callee targets, not a broad call graph / RTTI / offset scan.
    text_base, text = view.segments[".text"]
    references = []
    for site, target in relative_candidates(text, text_base, {0x29D280, 0x369F00}):
        rows = audit.rows(site) if view.function(site) else []
        row = matching_branch(rows, site, target)
        references.append(dict(rva=site, target=target, boundary_verified=bool(row),
            function=view.function(site), instruction=row,
            boundary="Direct E8/E9 prefilter plus original-range linear decode; indirect callers not counted."))
    resources = resource_evidence(view)
    prior_path = ROOT / "outputs/client_protection_v44_20260908/damage_types.json"
    prior = json.loads(view.load(prior_path))
    inheritance = [h for h in prior["hierarchies"].values()
                   if h["bases"][0]["name"] == "CDnProjectile"]
    # Reuse only prior type hierarchy metadata, not its identity-field analysis.
    if not inheritance or not any(b["name"] == "CDnWeapon" for b in inheritance[0]["bases"]):
        raise ValueError("v44 projectile/weapon hierarchy evidence missing")
    changed = [path for path, old in view.inputs.items() if digest(Path(path).read_bytes()) != old]
    if changed:
        raise ValueError(f"input changed during audit: {changed}")
    return dict(schema="projectile-config-v45", client_sha256=digest(view.raw),
        tool_sha256=digest(Path(__file__).read_bytes()), inputs=view.inputs,
        exception_directory=view.exception, segment_provenance=view.streams,
        capstone_version=capstone.__version__, bounds=dict(max_functions=MAX_FUNCTIONS,
        max_function_bytes=32768, max_total_code_bytes=MAX_CODE_BYTES,
        excluded_functions=sorted(EXCLUDED_FUNCTIONS), identity_field_tracing=False),
        functions=list(audit.functions.values()), windows=audit.windows, anchors=audit.anchors,
        strings=strings, property_fields=fields, row_exists_leaf=leaf, kind3_switch=switch_evidence,
        direct_references=references, prior_projectile_hierarchy=inheritance,
        old_resources=resources, changed_inputs=changed,
        conclusions=CONCLUSIONS, limitations=LIMITATIONS,
        main_thread_context=dict(source="User-provided main-thread findings, 2026-09-09; not independently reverified by this sidecar",
            finding="a90370 stream start owner+4 (c2s) / marker+3 (s2c); +5 objectid, +9 configrow; c74540 writes objectid to +0x1730 and calls c6fec0; constructor reference pair goes to +0x1740/+0x1748.",
            assessment="Compatible with separate object identity and configuration domains. This sidecar does not prove stream offsets, packet variants, or production attribution."))


CONCLUSIONS = [
    "+0x1164 is the row key passed to table selector 2 (WeaponTable) on this concrete config path, not the key used to select SkillTable (12) or SkillLevelTable (19).",
    "CDnChangeProjectileProcessor+0x34 is constructor R9D, the second base-10-converted _ProcessParam(cursor+N) string in factory kind3; constructor R8D goes to +0x30.",
    "Both constructor and non-destructor processor method pass +0x34 in EDX to c6fae0 on the returned a8d190 projectile object. c6fae0 stores EDX at +0x1164 BEFORE row validation, so the store alone does not prove a valid binding.",
    "WeaponTable registration flows through descriptor registry +0x28 and loaded-table map +8. The loader forms WeaponTable.dnt then basename/extension wildcard WeaponTable*.dnt under ext.",
    "The named column reads and prior CDnProjectile -> CDnWeapon inheritance support shared weapon-family configuration, including projectile templates. Equipment columns alone do not establish an equipment-only object or an equipped instance ID.",
    "The selected old weapontable, weapontable_projectile, and weapontable_equipment files have matching schemas. The selected character SkillTable has no kind3 processor; no concrete skill/config pair is invented from unrelated numeric coincidences.",
]
LIMITATIONS = [
    "All code evidence is static decoded bytes. No client/DLL execution, syscall/live memory, game changes, protection changes, capture replay, or production writes.",
    "Original file-backed .data2 exception directory supplies all function boundaries. Linear boundary validation does not prove runtime branch reachability; explicit kind3 jump-table evidence is retained separately.",
    "The row-exists target 0x235aa0 has no exception record; its two-return 77-byte sample is explicitly bounded, not claimed as a recovered RUNTIME_FUNCTION.",
    "Decoded stream hashes are verified but final_section_semantics_verified remains false; post-load fixups, runtime objects and table contents were not observed.",
    "Old resources are an independent historical snapshot. No same-version equivalence, file ordering, duplicate-row override policy or successful runtime table load is claimed.",
    "No skill ID, wire ID, single UDP message meaning, owner association, damage attribution coverage, or one-to-one template/skill mapping is established here. Constructor vtable writes alone never certify skill binding.",
    "Main-owned +0x1bc0 and a8dea0/a90370 factory bodies are excluded. The other direct caller 0x7d0540 uses table selectors 0x9d/0x9c; its higher-level semantics were not expanded.",
    "The numeric helper statically forwards base 10. Its inner conversion, invalid-string/overflow behavior and runtime inputs were not executed or reimplemented.",
]


def markdown(report):
    lines = ["# Projectile config v45 — bounded static evidence", "",
        "## 结论", "", "`processor+0x34 → EDX → c6fae0 → object+0x1164` 在本路径中是 **WeaponTable 表族行键**。"
        "可包含 projectile 模板；不是已证明的 SkillTable key、装备实例 ID 或 wire ID。", "",
        "最关键的收紧：c6fb09 先写 +0x1164，c6fb42 才验行存在；即使写入发生，也不能单凭它认定配置成功。", "",
        "## 可复核链（全部地址为 RVA）", "",
        "|证据|原始代码 / 数据流|结论边界|", "|---|---|---|",
        "|C1 注册表|35adf9: R8=&WeaponTable；35ae05: EDX=2；35ae0d→34f460。对照35af66 key12=SkillTable；35b11b key19=SkillLevelTable，索引列_SkillIndex。|表名不仅是字符串命中，有实参、注册写入与查询链。|",
        "|C2 加载链|34f460保存key与表名到manager+28描述符树；350770按EDX在manager+8树取表，缺失→34f2f0；34f3f9→359c00；34f407→34f210装回同key。|不是跨表相同数字就同语义。|",
        "|C3 表族|359cab追加.dnt；359d05→34fe30；34fea1/34feb3拆base/ext；34fec5使用%s*.%s；34feeb使用ext；34fef2→1ecaa0。|WeaponTable*.dnt候选族；不证明运行时装载顺序/覆盖规则。|",
        "|C4 配置输入|29d292 EDI=R9D；29d2d2 [RSI+34]=EDI；29d342→a8d190；29d377 EDX=[RSI+34]；29d37d→c6fae0。29d5e0非析构路径在29d69b/29d6a1重复使用。|对象创建不等于技能绑定。R8D进入+30，不是+34。|",
        "|C5 实际查询|c6fb09 [RCX+1164]=EDX；c6fb1c EDX=2；c6fb28→350770；c6fb39 EDX=[RBX+1164]；c6fb42→235aa0。|+1164存的是WeaponTable行查询参数，未验行前已写入。|",
        "|C6 行/列访问|235aa0读table+70树，node+1c比较行键、node+20检查索引!=−1。235960对同一行树取索引，再调用235700解列名，索引数据位于table+80/+88、单元步长12。|静态数据结构支持行/列查询；未虚构C++类型声明。|",
        "|C7 上游参数|369f00选key19；EDX−1经21项跳表，kind3(slot2)→36a726。36a746/36a783取连续两个_ProcessParam%d；decc24传base10；36a7e6 R9D=第二值；36a7f0→29d280。|是技能等级行携带的配置参数，不是把等级行键直接写+34。|",
        "|C8 上游调用者|b9b2c6选SkillTable(12)，b9b2dc选SkillLevelTable(19)；b9b7e9清参数游标；b9b839取_Processor%d；b9b92d传游标，b9b931传独立等级行键，b9b936传processor kind；b9b93c→369f00。|后续processor消费游标；不能一律把第二个参数说成固定_ProcessParam2。|", "",
        "## c6fae0 的具名字段写入", "", "|表列|查询调用 RVA|对象目标|", "|---|---|---|"]
    for item in report["property_fields"]:
        lines.append(f"|{item['column']['text']}|{item['getter_call_rva']:#x}|" +
                     "; ".join(f"{r['rva']:#x}: {r['operands']}" for r in item["stores"]) + "|")
    lines += ["", "CDnProjectile继承CDnWeapon的依据复用v44已记录的RTTI层级；本轮不新增RTTI扫描。"
              "以上字段证明共享武器配置性质，但旧projectile表也具有这些列，因此不能反推仅为装备。", "",
              "## 旧资源对照（历史样本，不是当前客户端运行态）", "",
              "|文件|完整解析行数|首行原始字段|", "|---|---|---|"]
    for file in report["old_resources"]["files"]:
        sample = file["samples"][0]["cells"]
        lines.append(f"|{Path(file['path']).name}|{file['row_count']}|" +
                     "; ".join(f"{k}={v['value']}" for k, v in sample.items()) + "|")
    resources = report["old_resources"]
    lines += ["", f"三份WeaponTable表结构完全相同：{resources['weapon_schemas_equal']}。"
              f"skilltable_character的{resources['character_skill_rows_examined']}行、每行5个_Processor列中，"
              f"kind3匹配为{len(resources['processor3_character_skill_rows'])}。因此本轮没有可声明的具体skill→config样本配对；"
              "不扩展到其他资源族来补齐结论。JSON保存每个所列样本的row/cell偏移、长度、原始字节及整文件哈希。", "",
              "旧skilltable_character存在重复RowId 30633、30634，各2条；解析器逐行保留并标注，未采用字典覆盖。"
              "这不影响逐行检查processor kind3的阴性结果。", "",
              "## 复现与校验", "", f"原始客户端 SHA256 `{report['client_sha256']}`。",
              f"解释/审计工具 SHA256 `{report['tool_sha256']}`。", "",
              "exception directory = original file-backed .data2 RVA 0x2ddd290，563484 bytes，46957 valid sorted disjoint ranges。",
              f"本轮限定解码{len(report['functions'])}个函数区间，"
              f"合计{sum(x['size'] for x in report['functions'])} bytes；断言{len(report['anchors'])}个指令锚点。"
              "每个区间保存原始exception record RVA、unwind RVA、整段SHA256和证据窗口。", "",
              "仅扫描29d280/369f00两个目标的E8/E9候选并用真实函数起点复核；间接调用不计入穷尽性。"
              "没有展开主线的a8dea0/a90370。运行前后全部登记输入哈希无变化。", "",
              "复现：指定Python 3.12解释器加 `-B tools/trace_projectile_config_v45.py`；"
              "单元测试：同解释器加 `-B -m unittest discover -s tests -p test_projectile_config_v45.py -v`。", "",
              "## 尚未验证 / 不得外推", ""]
    lines += ["- " + limitation for limitation in report["limitations"]]
    lines += ["", "## 下一步交接", "",
        "主线通过用户消息提供：a90370流布局中+5为objectid、+9为configrow，c74540把objectid写+1730并经c6fec0插索引；"
        "引用对写+1740/+1748。本侧没有重新检查这两条工厂或消息字节，故仅记录为主线结果，不算本报告独立证明。"
        "它与本侧配置键结论相容，两个ID域仍应分开。", "",
        "主线可把+1164暂记为weapon-family config row key；在独立对象身份链确认后，"
        "仍需取得同版本表资源并证明具体processor分支、参数游标与选中行，才可连接到命名技能。"
        "本报告不建议据此立即更改生产归因规则。", ""]
    return "\n".join(lines)


def main():
    report = build()
    OUT.mkdir(parents=True, exist_ok=True)
    json_path, md_path = OUT / "config.json", OUT / "config.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    print(json.dumps(dict(functions=len(report["functions"]), anchors=len(report["anchors"]),
        changed_inputs=report["changed_inputs"], outputs={str(p): digest(p.read_bytes())
        for p in (json_path, md_path)}), ensure_ascii=True))


if __name__ == "__main__":
    main()
