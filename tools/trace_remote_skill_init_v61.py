"""Bounded, static-only remote 3019 lazy skill initialization audit.

Only writes outputs/remote_skill_init_v61_20260909. Does not execute the
client, inspect live memory, modify production, or replay the meter.
"""
from __future__ import annotations

from hashlib import sha256
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.trace_chain_lightning_meter_v59 import StaticView, _disassemble
from tools.review_client_damage_types_v44 import BASE
from tools.trace_projectile_config_v45 import parse_dnt
from tools.inspect_installed_resources_v37 import index as index_pak
from tools import trace_chain_lightning_meter_v59 as v59

OUT = ROOT / "outputs/remote_skill_init_v61_20260909"
PRIOR = ROOT / "outputs/chain_lightning_meter_v60_20260909"
INPUTS = {}


def read_json(path, expected=None):
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        raise ValueError(f"input changed: {path}")
    INPUTS[str(path)] = digest
    return json.loads(raw)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump(view, name, start, end):
    fragment = view.function(start)
    if fragment:
        boundaries = {r["rva"] for r in _disassemble(view, fragment[0], fragment[1])}
        if start not in boundaries:
            raise ValueError(f"not a verified instruction boundary: {start:#x}")
    rows = _disassemble(view, start, end)
    (OUT / (name + ".txt")).write_text("\n".join(
        f"{r['rva']:08X}  {r['bytes']:<32} {r['instruction']}" for r in rows) + "\n", encoding="utf-8")
    return rows


def references(view, targets):
    """Lossless byte prefilter, then verify each hit from its pdata boundary.

    Only direct E8/E9 and RIP-relative LEA are inventoried. This is NOT an
    exhaustive virtual-call or points-to analysis; explicitly report limits.
    """
    base, data = view.segments[".text"]
    pending = []
    for m in re.finditer(rb"[\xe8\xe9]", data):
        off = m.start()
        if off + 5 > len(data):
            continue
        target = base + off + 5 + struct.unpack_from("<i", data, off + 1)[0]
        if target in targets:
            pending.append((base + off, target))
    for m in re.finditer(rb"[\x48-\x4f]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", data):
        off = m.start()
        if off + 7 <= len(data):
            target = base + off + 7 + struct.unpack_from("<i", data, off + 3)[0]
            if target in targets:
                pending.append((base + off, target))
    cache, accepted, rejected = {}, [], []
    for rva, target in sorted(pending):
        f = view.function(rva)
        if not f:
            rejected.append({"rva": hex(rva), "target": hex(target), "reason": "no pdata boundary"})
            continue
        if f not in cache:
            cache[f] = _disassemble(view, f[0], f[1])
        rows = cache[f]
        i = next((i for i, row in enumerate(rows) if row["rva"] == rva), None)
        if i is None:
            rejected.append({"rva": hex(rva), "target": hex(target), "reason": "not decoded boundary"})
            continue
        accepted.append({"rva": hex(rva), "target": hex(target), "target_name": targets[target],
                         "pdata_fragment": [hex(x) for x in f[:2]],
                         "context": rows[max(0, i-8):i+5]})
    return {"accepted": accepted, "rejected": rejected,
            "scope": "direct E8/E9 and RIP LEA only; virtual calls require separately pinned vtables"}


def resources():
    root = Path("E:/ext0811.rar/Resource00/Resource00/resource/ext")
    names = ("skilltable_character.dnt", "skillleveltable_charactercleric.dnt")
    result = {"authority":"old extracted snapshot; NOT verified current client plaintext", "files":[]}
    for name in names:
        path = root / name
        raw = path.read_bytes()
        table = parse_dnt(raw)
        selected = []
        for ordinal, row in enumerate(table["rows"]):
            cells = row["cells"]
            if cells.get("_SkillIndex", cells["RowId"])["value"] == 3019:
                selected.append({"ordinal":ordinal, **row})
        result["files"].append({"path":str(path),"sha256":sha256(raw).hexdigest(),
                                "bytes":len(raw),"table_rows":table["row_count"],"selected_3019_rows":selected})
    # Prior v55 identifies the exact current SkillTable archive. Read its
    # directory and only matching packed bodies, not all archives or captures.
    archive = Path("D:/DragonNest/Reborn/Resource05.pak")
    rows, meta = index_pak(archive)
    entries = []
    with archive.open("rb") as stream:
        for row in rows:
            if Path(row["name"]).name.lower() not in names:
                continue
            stream.seek(row["offset"])
            body = stream.read(row["compressed"])
            entries.append({**row,"packed_body_sha256":sha256(body).hexdigest(),
                            "plaintext_verified":False})
    result["current_resource05"] = {"directory":meta,"entries":entries,
        "scope":"Only known Resource05 directory, not a search or precedence audit across every PAK"}
    save("resource_3019.json", result)
    return result


def parse_request(payload):
    if len(payload) not in (31, 35):
        raise ValueError("unreviewed 0E payload shape")
    return {"row_id":int.from_bytes(payload[:4], "little"), "level":payload[4],
            "alternate":payload[5], "activation_flag":payload[17],
            "additional_row":int.from_bytes(payload[18:22], "little"),
            "opaque_trailer":payload[31:].hex()}


def candidate_transition(payload, before, assumptions):
    """Executable conditional fixture, NOT a client emulator or attribution.

    Deliberately only the empty/baseline-zero or exact-level-existing 3019
    branch. Runtime truth values are supplied assumptions, never inferred
    from nearby hits, time windows, job alone, or the request itself.
    """
    request = parse_request(payload)
    state = deepcopy(before)
    result = {"request":request,"before":before,"after":state,
              "actual_start_proven":False,"meter_memory_proven":False,
              "deterministic_damage_attribution_allowed":False}

    def finish(status):
        result["status"] = status
        return result

    if (request["row_id"],request["alternate"],request["activation_flag"],request["additional_row"]) != (3019,0,0,0):
        return finish("OUTSIDE_MINIMAL_RULE")
    if assumptions.get("actor_class") != "CDnPlayerActor":
        return finish("ACTOR_BRANCH_UNPROVEN")
    required = ("owner_handle_valid", "registry_epoch_complete", "callbacks_return_normally")
    if any(assumptions.get(k) is not True for k in required):
        return finish("RUNTIME_PRECONDITION_UNPROVEN")
    entries = [r for r in state["registry"] if r["row_id"] == 3019]
    exact = [r for r in entries if r["level"] == request["level"]]
    if len(entries) > 1 or entries and not exact:
        return finish("EXISTING_LEVEL_OR_DUPLICATE_BRANCH_UNMODELED")
    if not exact:
        if assumptions.get("baseline_level") != 0:
            return finish("LEVEL_BASELINE_UNPROVEN")
        # Only for this fixture: zero baseline makes outer u8 subtraction and
        # inner baseline addition exactly equal to the observed level byte.
        result["factory_arguments"] = {"row_id":3019,"effective_level":request["level"]}
        if assumptions.get("factory_initialized") is False:
            return finish("FACTORY_FAILED_NO_REGISTER_NO_ACTIVATE")
        if assumptions.get("factory_initialized") is not True:
            return finish("FACTORY_RESOURCE_OR_REMAP_UNPROVEN")
        state["registry"].append({"row_id":3019,"level":request["level"],"entry_flag":1})
        result["registration"] = "APPEND_24_BYTE_ENTRY_0x3748BA_TO_0x3748E0"
    else:
        result["registration"] = "REUSE_EXACT_ROW_AND_LEVEL_0x376F00"
    if assumptions.get("lookup_handle_valid") is not True:
        return finish("REGISTERED_BUT_ACTIVATION_LOOKUP_UNPROVEN")
    skill_type = assumptions.get("skill_type")
    duration_type = assumptions.get("duration_type")
    if skill_type == 3:
        return finish("REGISTERED_BUT_TYPE3_REJECTED_0x37C4EF")
    if skill_type is None or duration_type is None:
        return finish("ACTIVE_MODE_FIELDS_UNPROVEN")
    if duration_type == 7:
        return finish("REGISTERED_BUT_TYPE7_EXTRA_GUARD_UNMODELED")
    # The normal remote handler passes R8B=0, bypassing the checked-local
    # error-code gate at 37C5D1. Registry remains distinct from current pair.
    result["current_pair_operation"] = "START" if state["current_pair"] is None else "REPLACE"
    state["current_pair"] = {"row_id":3019,"level":request["level"]}
    return finish("CONDITIONAL_CURRENT_PAIR_COMMIT_0x375ACC")


def verify_two_wire_fixtures(requests):
    chosen = []
    for length in (31,35):
        chosen.append(next(r for r in requests if len(bytes.fromhex(r["payload_hex"])) == length))
    by_frame = {r["frame"]:r for r in chosen}
    verified = []
    # Read raw trace prefix only to the second selected frame. Decode only
    # the two selected UDP frames; never instantiate a meter or damage model.
    for frame, (timestamp, adapter, original_length, raw) in enumerate(v59.read_trace(v59.CAPTURE),1):
        if frame in by_frame:
            expected = by_frame[frame]
            packet = v59.protocol.decode_captured_frame(timestamp, raw, original_length)
            assert packet is not None and packet.transport == "UDP"
            matches = []
            for wire in v59.protocol.split_v576_payload(packet.payload):
                inventory = v59.inspect_udp_damage_records(wire, packet.inferred_direction())
                if not inventory.complete:
                    continue
                for record in inventory.typed_records:
                    if record.wire_record_sha256 == expected["wire_record_sha256"]:
                        assert record.record_type == 0x0e
                        assert int.from_bytes(record.record_bytes[:4],"little") == v59.FOCUS_ACTOR_ID
                        assert record.record_bytes[7:].hex() == expected["payload_hex"]
                        matches.append(record.record_bytes.hex())
            assert len(matches) == 1, (frame,len(matches))
            verified.append({"frame":frame,"prior_sequence":expected["sequence"],
                "wire_record_sha256":expected["wire_record_sha256"],"record_hex":matches[0],
                "payload_hex":expected["payload_hex"],"physical_frame_sha256":sha256(raw).hexdigest(),
                "parsed":parse_request(bytes.fromhex(expected["payload_hex"])),
                "unique_typed_walk":True})
        if frame >= max(by_frame):
            break
    assert len(verified) == 2
    return {"capture":str(v59.CAPTURE),"capture_sha256_from_prior_full_audit":v59.CAPTURE_SHA256,
            "fresh_read_scope":f"raw prefix through frame {max(by_frame)}; only two selected frames decoded",
            "fresh_full_capture_hash_performed":False,"fixtures":verified}


def fixtures_and_validation():
    parsed = read_json(PRIOR / "parser_candidates.json",
        "fa758e80d7bc818abb35eadb639d55baaa2692a3994c2fd71beea8d242b2eea7")["requests"]
    assert len(parsed) == 93
    for row in parsed:
        got = parse_request(bytes.fromhex(row["payload_hex"]))
        assert (got["row_id"],got["level"],got["alternate"],got["activation_flag"],got["additional_row"]) == (3019,21,0,0,0)
    wire = verify_two_wire_fixtures(parsed)
    save("wire_fixtures.json",wire)
    payload = bytes.fromhex(parsed[0]["payload_hex"])
    assumptions = {"actor_class":"CDnPlayerActor", "owner_handle_valid":True,
        "registry_epoch_complete":True,"callbacks_return_normally":True,"baseline_level":0,
        "factory_initialized":True,"lookup_handle_valid":True,"skill_type":0,"duration_type":2}
    empty = {"registry":[],"current_pair":None}
    cases = []

    def test(name, before, fields, expected):
        result = candidate_transition(payload,before,fields)
        assert result["status"] == expected,(name,result)
        cases.append({"name":name,"supplied_assumptions":fields,**result})
        return result

    success = test("old_snapshot_assumed_empty_registry_no_66",empty,assumptions,"CONDITIONAL_CURRENT_PAIR_COMMIT_0x375ACC")
    assert success["after"]["registry"] == [{"row_id":3019,"level":21,"entry_flag":1}]
    reuse = test("same_row_level_reuses_registry",success["after"],assumptions,"CONDITIONAL_CURRENT_PAIR_COMMIT_0x375ACC")
    assert len(reuse["after"]["registry"]) == 1 and reuse["current_pair_operation"] == "REPLACE"
    for name,patch,expected in (
        ("factory_failure",{"factory_initialized":False},"FACTORY_FAILED_NO_REGISTER_NO_ACTIVATE"),
        ("current_resource_not_verified",{"factory_initialized":None},"FACTORY_RESOURCE_OR_REMAP_UNPROVEN"),
        ("baseline_not_observed",{"baseline_level":None},"LEVEL_BASELINE_UNPROVEN"),
        ("owner_not_observed",{"owner_handle_valid":None},"RUNTIME_PRECONDITION_UNPROVEN"),
        ("invalid_lookup",{"lookup_handle_valid":False},"REGISTERED_BUT_ACTIVATION_LOOKUP_UNPROVEN"),
        ("type3",{"skill_type":3},"REGISTERED_BUT_TYPE3_REJECTED_0x37C4EF"),
        ("duration7",{"duration_type":7},"REGISTERED_BUT_TYPE7_EXTRA_GUARD_UNMODELED"),
        ("actor_other_branch",{"actor_class":"CDnMonsterActor"},"ACTOR_BRANCH_UNPROVEN"),
    ):
        outcome = test(name,empty,assumptions|patch,expected)
        assert outcome["after"]["current_pair"] is None
        if name in ("factory_failure","current_resource_not_verified","baseline_not_observed","owner_not_observed","actor_other_branch"):
            assert not outcome["after"]["registry"]
        else:
            assert len(outcome["after"]["registry"]) == 1
    test("existing_different_level_fail_closed",{"registry":[{"row_id":3019,"level":20}],"current_pair":None},
         assumptions,"EXISTING_LEVEL_OR_DUPLICATE_BRANCH_UNMODELED")
    truncated_rejected = False
    try:
        parse_request(payload[:-1])
    except ValueError:
        truncated_rejected = True
    assert truncated_rejected
    save("candidate_fixture.json",{"schema":"conditional-remote-lazy-3019-v61",
        "evidence_kind":"static path plus explicit hypothetical runtime assumptions; NOT actual START observation",
        "source_wire_fixture":wire["fixtures"][0],"cases":cases,"truncated_payload_rejected":True,
        "requests_sharing_parser_branch":93,"actual_start_proven_count":0})
    old = read_json(PRIOR / "v59_completed.json")
    hits = old["capture_evidence"]["focus_hits"]
    legacy = [r for r in hits if r["active_skill_id"] == 3019]
    object_truth = [r for r in legacy if r["native_deterministic"] and r["native_method"] == "damage_object_projectile_link"]
    primary = [r for r in legacy if r["native_method"] == "packet_skill_id"]
    assert len(object_truth) == 1012 and len(primary) == 0
    conflicts = [r for r in object_truth if r["native_skill_id"] != 3019]
    assert len(conflicts) == 525
    risk = [r for r in legacy if r not in object_truth and r not in primary]
    assert len(risk) == 375 and all(r["damage_object_id"] for r in risk)
    strata = {
        "scope":"Reclassification of existing v59/v60 request-holding audit; not a new current-state replay or time-window proof",
        "object_key_closed":{"truth":1012,"same_label":487,"different_label":525,
            "fallback_counterexamples":0,"policy":"retain object route; differences do not test fallback"},
        "independent_native_primary_without_object_chain":{"legacy_3019_candidate_rows":0,
            "verified_current_action_fallback_tests":0,"policy":"only eligible independent label stratum, and still require actual fallback route proof"},
        "object_creation_or_binding_unproven":{"rows":375,"methods":dict(Counter(r["native_method"] or "unmatched" for r in risk)),
            "policy":"exclude as truth; missing creation may hide a valid prior projectile object key"},
        "all_focus_actor_packet_label_inventory":{"no_damage_object_id":sum(r["native_method"]=="packet_skill_id" and not r["damage_object_id"] for r in hits),
            "has_damage_object_id":sum(r["native_method"]=="packet_skill_id" and bool(r["damage_object_id"]) for r in hits),
            "scope":"method-tag inventory only, outside legacy 3019 candidate; raw-primary eligibility not newly audited; not used as a positive test"},
        "new_v61_rule":{"conditional_request_candidates":93,"actual_START_proven":0,"new_deterministic_hits":0},
        "first_object_route_difference":conflicts[0],
    }
    save("validation_strata.json",strata)
    return strata


def report(view, strata):
    # Byte-pin the decisive edges; all starts were also verified by bounded
    # instruction decoding above. Vtable slots are pinned separately below.
    anchors = {
        0x373A62:"4c89bb68010000",0x373A69:"4c89bb70010000",0x373A70:"4c89bb78010000",
        0x373CE1:"c6832801000001",0x51E881:"e89a50e5ff",0xA481D5:"488987a0060000",
        0x3747D1:"e8fa018200",
        0x3748BA:"488d8b68010000",0x3748E0:"4883410818",0x374A23:"ff5038",
        0x37462D:"488d8e68010000",0x374669:"4883410818",
        0xB94A6A:"ff5010",0xB9630E:"89919c000000",0x7CFEC0:"8b417c",
        0xB9BC5A:"898798000000",0xB9BC7D:"898738020000",0xB9BF9F:"898798020000",
        0x37C4EF:"83bb9800000003",0x37C5D4:"744d",0x37C63A:"83bb3802000007",
        0x37C69B:"ff5010",0x375ACC:"0f114610",
        0xBA77E8:"e8c3227dff",0xBA7AB2:"e8c9ca7cff",
    }
    checked = []
    for rva, expected in anchors.items():
        actual = view.read(rva,len(bytes.fromhex(expected))).hex()
        if actual != expected:
            raise ValueError(f"anchor mismatch at {rva:#x}: {actual} != {expected}")
        checked.append({"rva":hex(rva),"bytes":actual})
    for rva,expected in ((0xF24788+0x58,0x374750),(0xF24788+0x80,0x37C460),
                         (0xF24788+0x10,0x375940),(0xF24788+0x38,0xA562F0),
                         (0xF11988+0x10,0xB96300),(0xF119B0+0x10,0x7CFEC0)):
        assert struct.unpack("<Q",view.read(rva,8))[0] == BASE+expected
    save("anchors.json",checked)
    result = {
        "schema":"remote-skill-initialization-v61",
        "closed":"Normal remote 0E can lazily create and append its requested skill without a 66/68 registration",
        "not_closed":"No capture-only proof of actual successful START, current resource/remap/registry epoch or meter transition",
        "actor":v59.FOCUS_ACTOR_ID,"requests":93,"actual_starts_proven":0,"new_deterministic_hits":0,
        "constructor":{"chain":["0xA481B3 -> 0x51E640","0x51E881 -> 0x373920"],
            "empty_vector_writes":["0x373A62 +168=0","0x373A69 +170=0","0x373A70 +178=0"],
            "owner_init":"+120=0,+128=1; lazy MASkillUser -> CDnActor dynamic cast at 0x37718C"},
        "writers":[
            {"entry":"0x374750 / remote vslot+58","source":"0E normal miss; explicit RowId and level delta",
             "vector_write":"0x3748BA..0x3748E0; 24-byte entry; flag +10=1; grow helper 0x373390"},
            {"entry":"0x374580 nonvirtual","source":"already constructed skill pair, not a class identifier",
             "vector_write":"0x37462D..0x374669; 24-byte entry; flag +10=0; grow helper 0x373390"},
            {"entry":"0x379090 / 0x379AB0","source":"explicit erase-by-skill / clear-all registry",
             "vector_write":"0x379A48 updates end after compaction; 0x379B80 drops invalid entry by 24 bytes"},
        ],
        "identified_other_initialization_callers":[
            {"caller":"0x370D21 -> factory; 0x370DAB -> 0x374580",
             "fields":"explicit source entry+190 RowId, +194 level, entry stride 0x308; does not derive 3019 from job alone"},
            {"caller":"0xBA7750 skill-list bank selection",
             "fields":"manager+10C selects 0/1 bank, bank begin/end at +158/+160 + bank*0x18; entries 16-byte skill pairs",
             "actor_gate":"0xBA779E RTTI CDnLocalPlayerActor; 0xBA77D4 cast; remote actor cannot enter registration branch",
             "writes":"0xBA77E8 clear registry; 0xBA7AB2 append existing skill after slot+50 acceptance"},
        ],
        "other_direct_register_existing_callers":"0xBA4CAC,0xBA5BA4,0xBA6B16,0xBA70A3,0xBB019E,0xBB9BC2: indexed with verified call contexts; full actor/job gates not closed in this minimal audit",
        "job_prebuild_conclusion":"No affirmative evidence of remote job-based prebuild; constructor is empty. This bounded review is not proof that no indirect/preload writer exists anywhere.",
        "resource_fields":{"row_key":"skill+9C, returned by (skill+20) vslot+10=0x7CFEC0",
            "+98":"_SkillType", "+238":"_DurationType", "+298":"_SkillLevel"},
        "validation":strata,
        "production_changed":False,"original_capture_changed":False,"client_executed":False,
    }
    save("REPORT.json",result)
    text = """# 远端3019技能初始化最小审计 v61

## 结论

闭合了一个不依赖66/68的远端注册路径：**普通0E请求在技能表未命中时，会直接按请求 RowId/等级创建 CDnSkill，追加 MASkillUser+168/+170，再尝试激活。** 因此“93条3019请求没有66”不是无法创建或显示施放的证据，不需要用“职业预建全技能”来解释。

本次只证明静态可达路径与可执行的条件 fixture。93条均落在同一正常请求分支；**从抓包证明实际成功 START 的数量仍为0，新确定性Hit为0**。未把请求、最近动作、时间窗或伤害指纹当作运行时动作状态。

## Actor构造、容器writer与其他初始化

|路径|已核事实|远端3019意义|
|---|---|---|
|CDnPlayerActor构造|A481B3→51E640；51E881→373920；373A62/69/70把+168/+170/+178清零|容器出生为空，未见构造时按职业遍历SkillTable预建|
|owner初始化|373CDA写+120=0，373CE1写+128=1；37718C执行MASkillUser→CDnActor动态转换并缓存+120|远端也能取得Actor owner；不是根Projectile的local-only复制规则|
|374750，remote slot+58|3748BA..3748E0追加24字节条目；pair16字节，+10标志1；满容量调用373390|正常0E缺注册时的直接懒建writer|
|374580，非虚函数|已有skill pair，精确RowId/level去重后追加；37462D..374669，标志0|源头必须已有技能对象，不能仅用job ID代替|
|370D21→factory→370DAB→374580|输入条目+190显式RowId、+194等级，条目步长308|这是显式参数对象创建，不是已证实的“职业→3019”规则|
|BA7750技能列表切换|manager+10C选0/1库；+158/+160加bank×18；16字节pair；BA77E8清表，BA7AB2重挂|BA779E/BA77D4明确dynamic_cast到CDnLocalPlayerActor；不能用于远端|
|379090 / 379AB0|按技能移除并压缩容器，379A48写end；全清循环，379B80跳过无效末条目|技能注册表和当前动作pair是两份状态；不得以任意动作结束推断技能表清空|

其他374580直接调用点 BA4CAC、BA5BA4、BA6B16、BA70A3、BB019E、BB9BC2 已保留字节和上下文，未在本最小审计中闭合全部职业/Actor分支。`writer_callers.json` 仅覆盖 .text 中E8/E9及RIP LEA，不是穷尽间接调用的证明；因此不声称不存在任何其他预载路径。

## 普通0E→懒建→当前动作提交

远端MASkillUser在Actor+6A0，虚表F24788：+58=374750，+80=37C460，+10=375940，+38=A562F0。不能误套本机覆写，也不能把374580误叫slot+50。

1. A599路径将body+0作为RowId、+4作为等级传给376F00。后者遍历24字节条目，比较 `(skill+20).vslot+10` 的RowId及 `skill+298`。
2. 未命中则调用376E10取得基础/附加等级，执行8位差值，调用slot+58。374750自身也检查RowId/等级，再取基值相加调用B949D0。fixture只覆盖基值明确为0的最小分支，未知基值、不同等级或同RowId多条均拒绝假定。
3. B949D0分配7E0字节，B8FE30构造，CDnSkill主虚表F11988的+10=B96300。B9630E写请求RowId到skill+9C；子表F119B0的+10=7CFEC0读取子对象+7C，即skill+9C。不是IconImageIndex。
4. B96300先后以mode1及0调用B9B270；后者选择SkillTable(12)、SkillLevelTable(19)，按_SkillIndex及等级查询，并可能经玩家部件/环境remap。查询失败可返回false；特别注意B9B564缓存为空会走B9C9E0返回true，不能把factory返回true单独当作完整资源初始化证明。
5. 有效pair在3748BA写入注册容器。远端+38回调为A562F0，不是基类3771B0；本工具保留其完整静态范围，fixture明确要求回调正常返回且相关状态有效。
6. 37C460在player且skill+98==3时返回6。普通0E的R8B固定0，37C5D4跳过本地检查型错误码门控；duration!=7时不走B9A9E0特殊分支，37C69B调用375940，375ACC提交当前pair。提交与可见ACT START、5286C0动作名转换、meter记忆表写入仍须分开，不从此直接推定meter更新。

## 3019资源与可重建边界

旧提取快照 `skilltable_character.dnt` RowId3019（ordinal185，offset49397）：StaticName=ChargeVolt，NeedJob=4，SkillType=0，DurationType=2，MaxLevel=30，Processor1=1。

旧 `skillleveltable_charactercleric.dnt` 中等级21有两条：RowId1073935061/ApplyType0及1090712277/ApplyType1，均ProcessParam1=Skill_ChargeVolt。原始行及逐字段偏移、文件哈希在 `resource_3019.json`。字节闭合：B9BC5A→+98=_SkillType，B9BC7D→+238=_DurationType，B9BF9F→+298=_SkillLevel。

这解释了body+4的21，而不是猜测动作variant。NeedJob=4描述资源归属，但不足以证明该远端实例已有注册、有效handle、当前等级基值或remap选择。初始化按明确RowId查表，不是根据抓包职业自动补全所有技能。

当前Resource05的SkillTable声明长度472652，旧快照472902，内容不等版；当前封装body与目录已取哈希，但明文未验证。只核这个已知档案，未遍历所有PAK；当前cleric等级分表位置/内容未在本次闭合。故旧资源只作结构/分支fixture，不能升级为当前安装资源权威证据。

## 可执行候选与实包验证

`candidate_fixture.json` 的最小条件：已确认remote CDnPlayerActor及owner、注册epoch完整、容器为空且等级基值0（或已存在唯一3019/21）、完整初始化和查询handle有效、相关回调正常返回、实际mode字段SkillType!=3且DurationType!=7。成立才给出 `CONDITIONAL_CURRENT_PAIR_COMMIT_0x375ACC`；依旧不声称实测START或伤害归因。

覆盖空表懒建、重复请求复用、factory失败、当前资源未知、基础等级未知、owner未知、查询handle无效、type3、duration7、错误Actor、已有不同等级以及截断payload拒绝。失败分支明确区分“没有注册”和“已注册但不能激活”。

93条请求复用已哈希锁定的v60逐包清单（31字节9条、35字节84条）。本次从原始抓包只读取到frame7997，对frame3999/7997两条UDP包重新唯一typed-walk，逐字节核对Actor=1314843433、0E、RowId3019、level21及原record SHA；35字节多出的4字节保留不解释。未重跑meter。原始record、payload、帧哈希在 `wire_fixtures.json`；全抓包哈希沿用前次审计，未声称本次重新全文件哈希。

## 验证分层及525反例纠正

这部分只是重分类既有v59/v60请求保持影子，不代表新的真实状态回放。

|路线|既有3019候选中的数量|解释|
|---|---:|---|
|对象链已闭合|1012：同标签487、不同525|全部method=damage_object_projectile_link；525全部属于object优先路线，作为当前动作fallback反例的数量为0|
|原生packet_skill_id独立标签|0|没有合格的3019候选区间对照，不能宣称fallback验证通过或失败|
|对象创建/绑定未闭合|375：continuation_window316、未匹配59|均有damage_object_id，不能用作truth；可能遗漏先前对象创建，保持风险|

首个不同标签frame4037/object100115的独立对象键为3425，动作请求为3019；两者共存合理，不能证明fallback错误。全Actor另有3740条无damage_object_id、633条有damage_object_id的packet_skill_id方法标签，均不在上述3019候选区间，未重新逐字段确认primary资格或挪用作正例。下一次验证必须以原生primary键为独立标签，并单独证明客户端实际选到fallback路线；对象已闭合保持优先，缺创建的Projectile不得直接启用。

旧数量口径不变：1521原始3019=820对象确定+674continuation+27learned；聚合后376改标到别技能，留下820确定+325缺口。fixed35全体3019缺口842=本捕获325+其余517。此次未降低任一缺口。

## 复现与边界

```powershell
Set-Location 'E:\\新建文件夹\\龙之谷模拟器版本\\龙之谷模拟器4.40'
& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B -X utf8 tools/trace_remote_skill_init_v61.py
```

单进程，仅新建v61专项工具和本输出目录；不改生产、v59/v60工具、旧输出或原抓包；不执行客户端、不读进程内存、不扫描35份捕获，不涉及根Projectile/ChargeVolt子对象创建路径。下一项缺证是同版资源及远端注册/等级基值/remap的可重建前置状态，不是再找最近动作或伤害窗口。
"""
    (OUT / "REPORT.md").write_text(text,encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for module,expected in (
        (v59,"b60bb3c7665866843724f904aac57a002787b4634f83b56336e5cf6516f888ff"),
    ):
        path = Path(module.__file__)
        actual = sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"reviewed helper changed: {path}")
        INPUTS[str(path)] = actual
    view = StaticView()
    blocks = {
        "skill_ctor": (0xB8FE30, 0xB904C9),
        "skill_factory": (0xB949D0, 0xB94AF0),
        "lazy_register": (0x374750, 0x374A55),
        "ensure_owner": (0x377150, 0x3771AB),
        "network_0e": (0xA59780, 0xA59A07),
        "activate": (0x37C460, 0x37C714),
        "current_pair_setter": (0x375940, 0x376714),
        "skill_user_ctor": (0x373920, 0x373D13),
        "register_existing": (0x374580, 0x374743),
        "remote_ctor": (0xA48020, 0xA491B1),
        "skill_init_full": (0xB96300, 0xB96678),
        "skill_config_init": (0xB9B270, 0xB9CA5C),
        "actor_base_ctor": (0x51E640, 0x51F1B0),
        "external_skill_list": (0xBA7750, 0xBA7B0A),
        "actor_property_skill_init": (0x370C80, 0x370E8A),
        "erase_skill": (0x379900, 0x379AA7),
        "clear_registry": (0x379AB0, 0x379BA2),
        "remote_registration_callback": (0xA562F0, 0xA56571),
        "active_mode_selector": (0xB9A360, 0xB9A3DD),
        "row_key_getter": (0x7CFEC0, 0x7CFEC4),
        "membership_row_level": (0x376F00, 0x37705B),
        "baseline_level": (0x376E10, 0x376EFB),
    }
    literals = []
    for name, (start, end) in blocks.items():
        rows = dump(view, name, start, end)
        for row in rows:
            raw = bytes.fromhex(row["bytes"])
            if len(raw) == 7 and raw[1] == 0x8d and (raw[2] & 0xc7) == 5:
                target = row["rva"] + 7 + struct.unpack_from("<i", raw, 3)[0]
                try:
                    value = view.read(target, 128)
                    if value[16:20] in (b".?AV", b".?AU"):
                        value = value[16:]
                    text = value.split(b"\0")[0].decode("ascii")
                    if len(text) >= 3 and text.isprintable():
                        literals.append({"at":hex(row["rva"]),"target":hex(target),"text":text})
                except (ValueError, UnicodeDecodeError):
                    pass
    save("literals.json", literals)
    # MASkillUser code neighborhood only, with full small function fragments
    # decoded individually so an unrelated data island cannot stop the walk.
    vector_refs = []
    for f in view.functions:
        if not 0x373000 <= f[0] < 0x37D700:
            continue
        rows = _disassemble(view, f[0], f[1])
        for i, r in enumerate(rows):
            if any(s in r["instruction"] for s in ("+ 0x168]", "+ 0x170]", "+ 0x178]")):
                vector_refs.append({"pdata_fragment": [hex(x) for x in f[:2]],
                                    "context": rows[max(0,i-4):i+6]})
    save("bounded_vector_references.json", vector_refs)
    targets = {0xE9E788:"MASkillUser vtable", 0xF24788:"remote MASkillUser vtable",
               0x374750:"slot58 add/update", 0x373390:"vector append grow",
               0x37C280:"alternate action activate", 0xB949D0:"skill factory",
               0x373920:"skill user constructor",0x374580:"nonvirtual register existing",
               0x379090:"erase skill",0x379AB0:"clear registry"}
    save("writer_callers.json", references(view, targets))
    skill_vtable = 0xB8FF2A + 0x381A5E
    vtables = {}
    for name, rva in (("skill", skill_vtable), ("base_skill_user",0xE9E788),
                      ("remote_skill_user",0xF24788)):
        slots = {hex(i):hex(struct.unpack("<Q", view.read(rva+i, 8))[0]-BASE)
                 for i in range(0, 0x20 if name == "skill" else 0x90, 8)}
        vtables[name] = {"rva":hex(rva), "slots":slots}
    init = int(vtables["skill"]["slots"]["0x10"],16)
    f = view.function(init)
    dump(view,"skill_init",f[0],f[1])
    save("vtables.json", vtables)
    resources()
    strata = fixtures_and_validation()
    report(view,strata)
    save("provenance.json", {"inputs":view.inputs|INPUTS, "scope":"one client; existing 93 packet fixtures; two fresh target-frame verifications; no meter scan",
                             "tool_sha256":sha256(Path(__file__).read_bytes()).hexdigest()})
    save("output_manifest.json", {p.name:sha256(p.read_bytes()).hexdigest()
        for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "output_manifest.json"})
    print(json.dumps({"out":str(OUT),"skill_init":hex(init),"vector_refs":len(vector_refs),"strata":strata["new_v61_rule"]}))


if __name__ == "__main__":
    main()
