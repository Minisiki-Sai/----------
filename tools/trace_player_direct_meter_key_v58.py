"""Reproduce the player-body damage-meter SkillTable-key audit.

This script is intentionally static and read-only with respect to the client and
production decoder.  It reads the hash-pinned client image/decoded segments,
disassembles bounded RVA ranges, reads prior fixed-corpus audit reports, and
writes only the v58 audit sidecar under outputs/.
"""
from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.review_client_damage_types_v44 import BASE, StaticView
import capstone


OUT = ROOT / "outputs/player_direct_meter_key_v58_20260909"
CLIENT = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
EXPECTED_CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
PROTOCOL = ROOT / "dragon_nest_builder/live_dps/protocol_v576.py"
BASELINE = ROOT / "outputs/native_result_key_v57_20260909/comparison.json"
CORRELATIONS = ROOT / "outputs/native_wire_v50_20260909/native_meter_correlations_merged.json"
ACTION_AUDIT = ROOT / "outputs/direct_action_state_v56_20260909/REPORT.json"
PRIOR_OBJECT_REPORT = ROOT / "outputs/damage_object_skill_getter_v56_20260909/REPORT.json"


RANGES = {
    "meter_key_resolution": (0x6A5646, 0x6A5916),
    "component_element_fallback": (0x6A5AF2, 0x6A5EAF),
    "current_action_pair_copy": (0x376D50, 0x376D63),
    "action_vector_skill_lookup": (0x376AA0, 0x376C25),
    "damage_result_copy_and_meter_call": (0x529775, 0x52998D),
    "actor_component_enumerator": (0x5253B0, 0x5254AE),
    "component_descriptor_key_helpers": (0xB9AC10, 0xB9AD2B),
    "current_action_clear_1": (0x378066, 0x378083),
    "current_action_clear_2": (0x3788CD, 0x3788ED),
}


def digest_bytes(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def disassemble(view: StaticView, start: int, end: int) -> list[dict[str, object]]:
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    rows = []
    for ins in decoder.disasm(view.read(start, end - start), BASE + start):
        rows.append({
            "rva": int(ins.address - BASE),
            "bytes": ins.bytes.hex(),
            "mnemonic": ins.mnemonic,
            "operands": ins.op_str,
            "instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
        })
    return rows


def c_string(view: StaticView, rva: int, maximum: int = 64) -> str:
    raw = view.read(rva, maximum)
    return raw.split(b"\0", 1)[0].decode("ascii")


def protocol_surface() -> dict[str, object]:
    source = PROTOCOL.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PROTOCOL))
    message_fields: list[str] = []
    skill_action_keyword_sets: list[list[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DecodedV576Message":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    message_fields.append(item.target.id)
        if not isinstance(node, ast.Call):
            continue
        called = node.func.id if isinstance(node.func, ast.Name) else ""
        if called != "DecodedV576Message":
            continue
        kind = next((kw.value for kw in node.keywords if kw.arg == "kind"), None)
        if isinstance(kind, ast.Constant) and kind.value == "skill_action":
            skill_action_keyword_sets.append(sorted(kw.arg for kw in node.keywords if kw.arg))
    return {
        "decoded_message_fields": message_fields,
        "skill_action_return_keyword_sets": sorted(skill_action_keyword_sets),
        "exposes_action_instance_handle": any(
            name in message_fields
            for name in ("action_instance_id", "action_handle", "action_object_id", "action_serial")
        ),
        "exposes_action_lifecycle": any(
            name in message_fields
            for name in ("action_phase", "action_start", "action_end", "action_cancel", "action_replace")
        ),
    }


def assert_static_contract(evidence: dict[str, list[dict[str, object]]]) -> None:
    instructions = {
        row["rva"]: (row["mnemonic"], row["operands"])
        for rows in evidence.values()
        for row in rows
    }
    checks = {
        0x6A5646: ("mov", "[r15 + 0x90]"),
        0x6A5693: ("call", "[rax + 0x30]"),
        0x6A56A8: ("mov", "[r12 + 0x658]"),
        0x6A5717: ("mov", "+ 0x10]"),
        0x6A57C8: ("lea", "[rax + 0x6a0]"),
        0x6A57D3: ("call", "0x140376d50"),
        0x6A5823: ("call", "[rax + 0x10]"),
        0x6A588D: ("mov", "[rcx + 0xa0]"),
        0x6A590B: ("call", "[rax + 0x10]"),
        0x376D50: ("mov", "[rcx + 0x10]"),
        0x376D57: ("mov", "[rcx + 0x18]"),
        0x376ABC: ("mov", "[rcx + 0x170]"),
        0x376AC6: ("mov", "[rcx + 0x168]"),
        0x376BB3: ("call", "[rax + 0x10]"),
        0x376BB6: ("cmp", "r15d"),
        0x529775: ("mov", "[r14 + 0x90]"),
        0x52977C: ("mov", "[r15 + 0x90]"),
        0x529783: ("mov", "[r14 + 0x94]"),
        0x529987: ("call", "0x1406a52e0"),
        0x6A5AF2: ("mov", "0xc"),
        0x6A5B14: ("mov", "[rax + 0x94]"),
        0x6A5B1D: ("cmp", "3"),
        0x6A5B45: ("call", "0x1405253b0"),
        0x6A5B9C: ("add", "0x88"),
        0x6A5BAC: ("call", "0x140b9ac10"),
        0x6A5C9F: ("cmp", "[rcx + 0x88]"),
        0x6A5CCC: ("mov", "[rsi + 0x94]"),
        0x6A5CD2: ("cmp", "[rcx + 0xe98]"),
        0x6A5EA8: ("mov", "[rsi]"),
        0x525440: ("cmp", "[rdx + 0x40]"),
        0x52545F: ("cmp", "[rax + 0x44]"),
        0xB9AC55: ("mov", "[rbx]"),
        0xB9AC93: ("mov", "[rbx]"),
        0x378076: ("mov", "0xffffffffffffffff"),
        0x37807E: ("mov", "[rbx + 0x18]"),
        0x3788DE: ("mov", "0xffffffffffffffff"),
        0x3788E8: ("mov", "[rbx + 0x18]"),
    }
    failures = []
    for rva, (mnemonic, fragment) in checks.items():
        actual = instructions.get(rva)
        if actual is None or actual[0] != mnemonic or fragment not in actual[1]:
            failures.append({"rva": hex(rva), "expected": [mnemonic, fragment], "actual": actual})
    if failures:
        raise AssertionError(json.dumps(failures, ensure_ascii=False, indent=2))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if digest_file(CLIENT) != EXPECTED_CLIENT_SHA256:
        raise ValueError("client hash mismatch")
    view = StaticView()
    static_evidence = {
        name: disassemble(view, start, end)
        for name, (start, end) in RANGES.items()
    }
    assert_static_contract(static_evidence)

    literals = {
        hex(rva): c_string(view, rva)
        for rva in (0xED8E68, 0xED8E78, 0xED8E88)
    }
    if literals != {
        "0xed8e68": "_PDamage",
        "0xed8e78": "_MDamage",
        "0xed8e88": "_Element",
    }:
        raise AssertionError(f"literal mismatch: {literals}")

    baseline = load_json(BASELINE)
    correlations = load_json(CORRELATIONS)
    action_audit = load_json(ACTION_AUDIT)
    protocol = protocol_surface()
    native_stats = correlations["summary"]["native_field_stats"]
    action_routes = action_audit["aggregate"]["route_summary"]
    observed_player_direct37 = native_stats["direct37|player"]["observed"]
    primary_catalogued_player_direct37 = native_stats["direct37|player"]["primary_catalogued"]

    report = {
        "schema": "player-direct-meter-key-v58",
        "scope": "hash-pinned static client audit plus existing fixed-corpus summaries; no production mutation",
        "verdict": {
            "client_side_path_closed": True,
            "network_only_reconstruction_closed": False,
            "player_direct_exact_path": (
                "source actor -> actor+0x6A0 action state -> current action pair at state+0x10/+0x18 "
                "(actor+0x6B0/+0x6B8) -> action object+0x20 -> vtable+0x10 -> SkillTable RowId"
            ),
            "reason_network_is_blocked": (
                "the current decoder exposes actor+skill action records but not the action object handle/serial "
                "and exact START/REPLACE/CLEAR transitions needed to reproduce the client current-action pair"
            ),
        },
        "inputs": {
            "client": str(CLIENT),
            "client_sha256": EXPECTED_CLIENT_SHA256,
            "decoded_segment_inputs": view.inputs,
            "protocol": {"path": str(PROTOCOL), "sha256": digest_file(PROTOCOL)},
            "baseline": {"path": str(BASELINE), "sha256": digest_file(BASELINE)},
            "correlations": {"path": str(CORRELATIONS), "sha256": digest_file(CORRELATIONS)},
            "action_audit": {"path": str(ACTION_AUDIT), "sha256": digest_file(ACTION_AUDIT)},
            "prior_object_report": {"path": str(PRIOR_OBJECT_REPORT), "sha256": digest_file(PRIOR_OBJECT_REPORT)},
        },
        "meter_order": [
            {
                "priority": 1,
                "rvas": ["0x6A5646", "0x529775", "0x52977C"],
                "source": "damage result +0x90",
                "finding": "the caller copies +0x90 unchanged before invoking the meter",
            },
            {
                "priority": 2,
                "rva": "0x6A5693",
                "source": "validated related object vtable+0x30",
                "finding": "prior v56 proves this getter contract for Projectile/Weapon; CDnActor's same slot is unrelated",
            },
            {
                "priority": 3,
                "rvas": ["0x6A56A8", "0x6A5717"],
                "source": "meter-local vector CDnDamageMeterDlg+0x658..+0x660, stride 0x28",
                "finding": "matches source handle pair at record +0/+8 and reads candidate key at record +0x10; insertion semantics remain unclosed",
            },
            {
                "priority": 4,
                "rvas": ["0x6A57C0", "0x6A57C8", "0x6A57D3", "0x6A5823", "0x6A590B"],
                "source": "source Actor current action object",
                "finding": "exact player-body direct-damage fallback; returns the action object's SkillTable RowId",
            },
            {
                "priority": 5,
                "rvas": ["0x6A5AF2", "0x6A5EAA"],
                "source": "typed actor component plus element/state guards",
                "finding": "special result-class fallback for +0x94 in 0x6F..0x72",
            },
        ],
        "player_current_action_fallback": {
            "source_actor_resolution": "0x6A57C0/0x6A57C3 resolves the source handle pair to an Actor pointer",
            "state_base": "Actor+0x6A0",
            "current_pair": {
                "handle_or_index": "state+0x10 = Actor+0x6B0",
                "object_pointer": "state+0x18 = Actor+0x6B8",
                "copy_helper": "0x376D50",
            },
            "key_getter": "current action object+0x20, vtable+0x10",
            "independent_getter_confirmation": (
                "0x376AA0 scans state+0x168/+0x170 in 0x18-byte records and at 0x376BB3 calls "
                "object+0x20 vtable+0x10, then compares the return value with the requested skill key"
            ),
            "damage_validation": {
                "row_key": "current action object+0xA0",
                "registry_type": 19,
                "required_nonzero_columns": ["_PDamage", "_MDamage"],
                "literal_rvas": literals,
            },
            "lifecycle_evidence": {
                "clear_sites": ["0x378076/0x37807E", "0x3788DE/0x3788E8"],
                "exact_population_writer": "not closed",
            },
        },
        "fallback_0x6A5AF2_0x6A5EAA": {
            "entry_condition": "earlier key attempts still non-positive; registry type 12 (SkillTable) is available",
            "result_class": {
                "field": "damage result +0x94",
                "accepted_values": ["0x6F", "0x70", "0x71", "0x72"],
                "business_name": "unproved; do not rename from static evidence alone",
            },
            "enumeration": {
                "function": "0x5253B0(source_actor, result_plus_0x94, vector_out)",
                "actor_component_container": "Actor+0x1010",
                "filter": "component+0x40 equals requested type and component+0x44 is not 3",
                "selected_descriptor": "component+0x88",
            },
            "guards": [
                "0xB9AC10(descriptor) obtains the effective SkillTable row, with descriptor+0 as raw fallback",
                "damage result +0x88 must equal that SkillTable row's _Element",
                "source Actor+0xE98 must equal descriptor+0x94",
            ],
            "final_key": "descriptor first dword at 0x6A5EA8, written as meter key at 0x6A5EAA",
            "strict_unknowns": [
                "the semantic name of damage result +0x94",
                "the semantic name of Actor+0xE98 and descriptor+0x94",
                "the network messages that populate the matching component descriptor",
            ],
        },
        "network_reconstruction": {
            "current_decoder_surface": protocol,
            "can_reconstruct_result_plus_0x90": "partially, for bounded direct37/direct41 result layouts already proved by the native-result audit",
            "can_reconstruct_actor_current_action_pair": False,
            "can_reconstruct_0x6A5AF2_component_path": False,
            "missing_state": [
                "action object handle/identity corresponding to Actor+0x6B0/+0x6B8",
                "packet-driven START/REPLACE/CLEAR lifecycle for that current action object",
                "actor component membership for result classes 0x6F..0x72",
                "component descriptor+0x88 and its raw SkillTable RowId",
                "Actor+0xE98 and descriptor+0x94 equality-guard values",
            ],
            "important_boundary": (
                "this proves the current Python decoder does not expose enough state; it does not prove the wire lacks it. "
                "The missing fields must be tied to the exact client state writers/deserializers before use."
            ),
        },
        "fixed_corpus_context": {
            "captures": len(baseline["capture_checks"]),
            "jobs": len(baseline["jobs"]),
            "jobs_meeting_95": baseline["jobs_meeting_named_target"],
            "jobs_below_95": baseline["jobs_below_named_target"],
            "all_physical_totals_conserved": baseline["all_conserved"],
            "direct37_player_native_primary": {
                "observed": observed_player_direct37,
                "catalogued": primary_catalogued_player_direct37,
                "catalogued_percent": round(100.0 * primary_catalogued_player_direct37 / observed_player_direct37, 6),
            },
            "generic_action_signal_validation": {
                "0x0E": {
                    "truth_hits": action_routes["hit_0e_direct_signal"]["truth_hits"],
                    "truth_matches": action_routes["hit_0e_direct_signal"]["truth_matches"],
                    "precision_percent": round(
                        100.0 * action_routes["hit_0e_direct_signal"]["truth_matches"]
                        / action_routes["hit_0e_direct_signal"]["truth_hits"], 6
                    ),
                },
                "0x12": {
                    "truth_hits": action_routes["hit_12_direct_signal"]["truth_hits"],
                    "truth_matches": action_routes["hit_12_direct_signal"]["truth_matches"],
                    "precision_percent": round(
                        100.0 * action_routes["hit_12_direct_signal"]["truth_matches"]
                        / action_routes["hit_12_direct_signal"]["truth_hits"], 6
                    ),
                },
                "meaning": "generic action-to-hit association is unsafe and cannot substitute for the client current-action object state",
            },
        },
        "next_minimum_safe_rule": {
            "production_now": (
                "do not add a player-body fallback: accept an exact positive native result key or an already deterministic "
                "object/entity chain; otherwise preserve the existing unresolved/inferred status"
            ),
            "next_shadow_state_machine": [
                "trace the exact writer/deserializer that sets Actor+0x6B0/+0x6B8",
                "decode its discrete packet-driven START/REPLACE/CLEAR transitions using packet order and object identity, never elapsed time",
                "derive the SkillTable RowId only from the reconstructed action object/getter-equivalent state",
                "validate against positive result+0x90 and deterministic object/entity rows on multiple captures and actors with zero conflicts before promotion",
            ],
            "explicitly_forbidden": [
                "last skill cast",
                "nearest action",
                "fixed millisecond window",
                "damage-size or hit-spacing similarity",
                "forcing a known skill name solely to improve coverage",
            ],
        },
        "blocker": (
            "the client-side key getter is closed, but the packet handler/state-writer that constructs and replaces "
            "Actor+0x6B0/+0x6B8 is not. Therefore Crusader/Paladin player-body direct skills cannot yet receive "
            "a network-only deterministic key without an unproved temporal substitution."
        ),
        "production_changed": False,
    }

    static_path = OUT / "static_evidence.json"
    report_json_path = OUT / "REPORT.json"
    report_md_path = OUT / "REPORT.md"
    static_path.write_text(json.dumps({
        "client_sha256": EXPECTED_CLIENT_SHA256,
        "ranges": {name: {"start": hex(a), "end": hex(b)} for name, (a, b) in RANGES.items()},
        "literals": literals,
        "disassembly": static_evidence,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md = f"""# 玩家本体直接伤害：内置 meter 最终 SkillTable RowId 取键审计（v58）

## 结论先行

本轮已经闭合**客户端内部**对玩家本体直接伤害的主回退取键路径，但尚未闭合**仅靠当前抓包解码**对该路径的等价重建。

确切路径是：

`来源 Actor → Actor+0x6A0 动作状态 → 当前动作对象对（Actor+0x6B0/+0x6B8）→ 动作对象+0x20 → vtable+0x10 → SkillTable RowId`

因此，十字军、圣骑士等没有 Projectile/Weapon 实体的直接技能，并不是从 `CDnActor vtable+0x30` 取技能；`vtable+0x30` 仍只适用于前一份报告确认的 Projectile/Weapon 契约。当前网络解码没有动作对象句柄及精确的开始、替换、清除状态，不能拿“最近动作”或固定毫秒窗口代替。

## 内置 meter 的取键顺序

|优先级|地址|来源|结论|
|---:|---|---|---|
|1|`0x6A5646`|伤害结果 `+0x90`|原生结果键；`0x529775–0x52978A` 证明调用者原样复制 `+0x90/+0x94`|
|2|`0x6A5693`|related object `vtable+0x30`|Projectile/Weapon getter；不是通用 Actor getter|
|3|`0x6A56A8–0x6A5717`|meter 自身 `+0x658..+0x660` 表|步长 `0x28`，以来源句柄对匹配，读取记录 `+0x10`；插入端尚未闭合|
|4|`0x6A57C0–0x6A5914`|来源 Actor 当前动作对象|玩家本体直接伤害的确切主回退|
|5|`0x6A5AF2–0x6A5EAA`|Actor 指定类型组件|仅针对结果 `+0x94 ∈ 0x6F..0x72` 的后续特殊回退|

## 玩家/Actor 当前动作回退

1. `0x6A57C3` 把来源句柄对解析成 Actor 指针，`0x6A57C8` 定位到 `Actor+0x6A0`。
2. `0x376D50` 原样复制状态 `+0x10/+0x18`，即 `Actor+0x6B0/+0x6B8` 的当前动作句柄/对象指针。
3. 句柄验证后，`0x6A5823` 调用“当前动作对象 `+0x20` 子对象的 `vtable+0x10`”。
4. `0x376AA0` 提供独立证明：它扫描动作状态 `+0x168/+0x170` 的 `0x18` 字节记录，并在 `0x376BB3` 调用同一 getter，再于 `0x376BB6` 与请求的技能键比较。因此该返回值可严格命名为动作 SkillTable RowId。
5. meter 还以动作对象 `+0xA0` 查询注册表类型 19，并要求 `{literals['0xed8e68']}` 或 `{literals['0xed8e78']}` 至少一项非零；通过后 `0x6A590B` 再取一次 RowId 作为最终键。
6. `0x378076/0x37807E` 与 `0x3788DE/0x3788E8` 证明当前动作对会被明确清空；但本轮尚未定位到设置该对的确切网络处理写入端。这是抓包等价重建的核心阻断，而不是 getter 本身不清楚。

## `0x6A5AF2–0x6A5EAA` 数据流

该分支的可核验数据流如下：

1. `0x6A5AF2` 取得注册表类型 12，即 SkillTable。
2. `0x6A5B14` 读取伤害结果 `+0x94`；`sub 0x6F / cmp 3` 将范围严格限制为 `0x6F、0x70、0x71、0x72`。这个字段的业务名称尚无证据，报告不擅自命名。
3. `0x6A5B45` 调用 `0x5253B0(source_actor, result+0x94, vector)`。该函数枚举 `Actor+0x1010` 的组件，要求组件 `+0x40` 等于请求类型且 `+0x44 != 3`。
4. meter 对每个组件取 `component+0x88` 描述符，并由 `0xB9AC10` 得到有效 SkillTable 行；该辅助函数无法重映射时回退到描述符首 DWORD。
5. `0x6A5C9F` 要求伤害结果 `+0x88` 等于该 SkillTable 行的 `{literals['0xed8e88']}`。
6. `0x6A5CD2` 还要求来源 `Actor+0xE98 == descriptor+0x94`。两字段的业务含义尚未闭合，只能称为精确相等保护。
7. 两项保护均通过后，`0x6A5EA8` 读取 `descriptor+0`，`0x6A5EAA` 把它写成最终 meter key。

这说明它是“结果类别 → Actor 组件 → SkillTable Element/状态双保护 → RowId”的确定性客户端回退，不是按时间猜最近技能。

## 能否由抓包重建

**目前不能完整重建。** 已经能从受限 `direct37/direct41` 布局重建部分 `result+0x90`，但当前 `DecodedV576Message` 没有暴露：

- 对应 `Actor+0x6B0/+0x6B8` 的动作对象句柄/序号；
- 当前动作对象精确的 START、REPLACE、CLEAR 状态迁移；
- `0x6F..0x72` 类型组件集合、`component+0x88` 描述符；
- `Actor+0xE98` 与 `descriptor+0x94` 的保护值。

这个结论只表示“当前解码器的字段不足”，并不证明线上协议一定没有这些信息。下一步必须从上述 Actor 字段的写入函数反向定位具体反序列化器，再和原始包字节逐字段闭合。

固定 35 份语料中，`direct37|player` 共 {observed_player_direct37:,} 行，原生 `+0x90` 能映射到目录技能的只有 {primary_catalogued_player_direct37:,} 行（{100.0 * primary_catalogued_player_direct37 / observed_player_direct37:.2f}%）。这解释了为什么“原生结果键”只新增少量命名 Hit、没有改变技能构成：客户端 meter 的大量玩家本体取键依赖运行时动作/组件状态，而不是每个伤害结果都携带有效 RowId。

同时，旧的泛化动作关联在独立真值上的精度只有：`0x0E` {100.0 * action_routes['hit_0e_direct_signal']['truth_matches'] / action_routes['hit_0e_direct_signal']['truth_hits']:.2f}%（{action_routes['hit_0e_direct_signal']['truth_matches']}/{action_routes['hit_0e_direct_signal']['truth_hits']}），`0x12` {100.0 * action_routes['hit_12_direct_signal']['truth_matches'] / action_routes['hit_12_direct_signal']['truth_hits']:.2f}%（{action_routes['hit_12_direct_signal']['truth_matches']}/{action_routes['hit_12_direct_signal']['truth_hits']}）。因此，任何“最近动作/固定窗口”方案都不能成为这个分支的替代品。

## 下一条最小安全规则

当前可进入生产的最小规则仍应是：

> 玩家本体直接 Hit 只有在伤害结果携带受边界验证的正原生键，或已有确定对象/实体链时才升级为确定性技能；否则保留未确定/现有推断状态，不用最近动作补键。

下一步只在影子审计中建立**离散状态机**：定位设置 `Actor+0x6B0/+0x6B8` 的确切处理器，把对应网络记录解成 START/REPLACE/CLEAR，以包顺序和对象身份重放，不使用任何时间阈值。它必须在多份抓包、多角色上与正 `result+0x90` 及确定对象链做到零冲突，才允许转为生产规则。

明确禁止：最近一次技能、最近时间动作、统一毫秒窗口、伤害数值/Hit 间隔相似、为了提高覆盖率强塞已有技能名。

## 当前阻断与 95% 基线

固定 35 份、30 个二转职业的基线仍为 {baseline['jobs_meeting_named_target']}/30 达到 95%，剩余 {len(baseline['jobs_below_named_target'])} 个职业未达标。客户端取键路径已经闭合，但网络到 `Actor+0x6B0/+0x6B8` 的状态写入还没有闭合，所以现在不能诚实声称十字军/圣骑士等本体直接技能已获得确定键，更不能用时间窗口把覆盖率“补”到 95%。

## 复现方法与边界

运行：

```powershell
& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B tools/trace_player_direct_meter_key_v58.py
```

脚本会校验客户端 SHA-256 `{EXPECTED_CLIENT_SHA256}`、已解码段清单、关键指令地址和三个表字段文字，再生成本报告、结构化报告和逐指令证据。全程未执行、加载、注入客户端，未读取进程内存，未修改生产代码或评分规则。
"""
    report_md_path.write_text(md, encoding="utf-8")

    reproducibility = {
        "command": "python -B tools/trace_player_direct_meter_key_v58.py",
        "outputs": {
            path.name: {"sha256": digest_file(path), "bytes": path.stat().st_size}
            for path in (report_md_path, report_json_path, static_path)
        },
        "production_changed": False,
    }
    repro_path = OUT / "reproducibility.json"
    repro_path.write_text(json.dumps(reproducibility, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(report_md_path),
        "client_side_path_closed": True,
        "network_only_reconstruction_closed": False,
        "jobs_meeting_95": baseline["jobs_meeting_named_target"],
        "production_changed": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
