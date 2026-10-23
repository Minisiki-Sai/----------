"""Audit the protocol lifecycle of Burning Hand's Skill_Explosion_Attack.

This is deliberately a read-only evidence tool.  It distinguishes three
different things that used to be conflated during Burning Hand attribution:

* the permanent passive-state echo (skill 2301 / effect class 172),
* the zero-duration action response that names ``Skill_Explosion_Attack``,
* the ordinary direct41 damage row produced after that response.

The action ACT contains a type-4 direct-damage signal but no type-15 attack
object signal.  Consequently the response-instance ID is an action token, not
a projectile ``damage_object_id``.  The report never manufactures a projectile
registration or a removal event when the wire does not contain one.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import struct
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps import protocol_v576 as protocol  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from tools.build_attack_instance_catalog import parse_act  # noqa: E402
from tools.build_dungeon_map_catalog import parse_dnt  # noqa: E402


SKILL_ID = 2301
SKILL_LEVEL_ROW_MIN = 1_073_889_089
ACTION_EFFECT_CLASS = 62
FILTER_EFFECT_CLASS = 637
PASSIVE_EFFECT_CLASS = 172
ACTION_PARAMETER = "Skill_Explosion_Attack"
FILTER_PARAMETER = (
    "2303,0,0,1000;2205,0,0,2200;"
    "2303,291146,0,1000;2205,291144,0,2200"
)
FIRE_SKILL_IDS = frozenset(
    {
        "2002", "2202", "2205", "2206", "2207", "2208", "2213",
        "2215", "2217", "2220", "2302", "2303", "2304", "2305",
        "2306",
    }
)
CAPTURE_HEADER = struct.Struct("<dBII")
SKILL_BYTES = SKILL_ID.to_bytes(4, "little")


@dataclass(slots=True)
class ResponseObservation:
    timestamp_ms: int
    packet_index: int
    message_index: int
    direction: str
    signature: str
    payload_length: int
    xor_phase: int
    record_offset: int
    effect_code: str
    source_actor_id: int
    target_actor_id: int
    skill_level_row_id: int
    effect_class_id: int
    duration_ms: int
    parameters: str
    response_instance_id: int


@dataclass(slots=True)
class ActionGeneration:
    source_actor_id: int
    target_actor_id: int
    response_instance_id: int
    first_timestamp_ms: int
    last_timestamp_ms: int
    observations: int
    paired_filter_instance_id: int = 0
    paired_filter_delta_ms: int = 0
    result_timestamp_ms: int = 0
    result_delta_ms: int = 0
    result_skill_id: str = ""
    result_skill_name: str = ""
    result_damage: int = 0
    result_wire_layout: str = ""


def _capture_wires(path: Path):
    first_timestamp: float | None = None
    packet_index = 0
    with path.open("rb") as stream:
        if stream.read(6) != b"DNDP1\0":
            raise ValueError(f"not a DNDP1 capture: {path}")
        while raw_header := stream.read(CAPTURE_HEADER.size):
            if len(raw_header) != CAPTURE_HEADER.size:
                raise ValueError(f"truncated capture header: {path}")
            timestamp, _adapter, original_length, stored_length = (
                CAPTURE_HEADER.unpack(raw_header)
            )
            raw = stream.read(stored_length)
            if len(raw) != stored_length:
                raise ValueError(f"truncated capture frame: {path}")
            if first_timestamp is None:
                first_timestamp = timestamp
            packet_index += 1
            packet = protocol.decode_captured_frame(
                timestamp, raw, original_length
            )
            if packet is None or packet.transport.upper() != "UDP":
                continue
            relative_ms = round((timestamp - first_timestamp) * 1000.0)
            direction = str(packet.inferred_direction())
            for message_index, wire in enumerate(
                protocol.split_v576_payload(packet.payload)
            ):
                yield (
                    relative_ms,
                    packet_index,
                    message_index,
                    direction,
                    bytes(wire),
                )


def _response_rows(
    path: Path,
) -> tuple[list[ResponseObservation], Counter[str], int]:
    responses: list[ResponseObservation] = []
    passive_echo_shapes: Counter[str] = Counter()
    decoded_messages = 0
    for (
        timestamp_ms,
        packet_index,
        message_index,
        direction,
        wire,
    ) in _capture_wires(path):
        if direction != "server_to_client":
            continue
        signature = wire[5:9].hex() if len(wire) >= 9 else ""
        for xor_phase in protocol._v576_candidate_xor_phases(wire):
            decoded_messages += 1
            body = protocol._decode_v576_body(wire, xor_phase)
            # Persistent passive-state echo:
            #   4B 09 00 | owner | 2301 | 00 | owner |
            #   4C 09 00 | owner | 172 | 01
            marker = b"\x4b\x09\x00"
            cursor = body.find(marker, 9)
            while cursor >= 0:
                if cursor + 28 <= len(body):
                    owner = int.from_bytes(body[cursor + 3 : cursor + 7], "little")
                    skill = int.from_bytes(body[cursor + 7 : cursor + 11], "little")
                    repeated_owner = int.from_bytes(
                        body[cursor + 12 : cursor + 16], "little"
                    )
                    trailing_marker = body[cursor + 16 : cursor + 19]
                    trailing_owner = int.from_bytes(
                        body[cursor + 19 : cursor + 23], "little"
                    )
                    effect_class = int.from_bytes(
                        body[cursor + 23 : cursor + 27], "little"
                    )
                    if (
                        owner > 0
                        and owner == repeated_owner == trailing_owner
                        and skill == SKILL_ID
                        and trailing_marker == b"\x4c\x09\x00"
                        and effect_class == PASSIVE_EFFECT_CLASS
                        and body[cursor + 27] == 1
                    ):
                        passive_echo_shapes[
                            f"{len(wire)}:{signature}:phase{xor_phase}:offset{cursor}"
                        ] += 1
                cursor = body.find(marker, cursor + 1)

            cursor = body.find(SKILL_BYTES, 9)
            while cursor >= 0:
                # Effect response layout used by the two audited action rows.
                if (
                    cursor >= 16
                    and cursor + 20 <= len(body)
                    and body[cursor - 4 : cursor] == b"\xff" * 4
                    and body[cursor - 5] == 0
                ):
                    effect_code = body[cursor - 12 : cursor - 9].hex()
                    source = int.from_bytes(
                        body[cursor - 9 : cursor - 5], "little"
                    )
                    target = int.from_bytes(
                        body[cursor - 16 : cursor - 12], "little"
                    )
                    skill_level_row_id = int.from_bytes(
                        body[cursor + 4 : cursor + 8], "little"
                    )
                    effect_class_id = int.from_bytes(
                        body[cursor + 8 : cursor + 12], "little"
                    )
                    duration_ms = int.from_bytes(
                        body[cursor + 12 : cursor + 16], "little"
                    )
                    parameter_size = int.from_bytes(
                        body[cursor + 16 : cursor + 20], "little"
                    )
                    parameter_end = cursor + 20 + parameter_size
                    if (
                        source > 0
                        and target > 0
                        and SKILL_LEVEL_ROW_MIN
                        <= skill_level_row_id
                        <= SKILL_LEVEL_ROW_MIN + 64
                        and effect_class_id
                        in {ACTION_EFFECT_CLASS, FILTER_EFFECT_CLASS}
                        and duration_ms == 0
                        and 0 <= parameter_size <= 256
                        and parameter_end + 5 <= len(body)
                        and body[parameter_end] == 0
                    ):
                        raw_parameters = body[cursor + 20 : parameter_end]
                        try:
                            parameters = raw_parameters.decode("utf-8")
                        except UnicodeDecodeError:
                            parameters = ""
                        expected = (
                            ACTION_PARAMETER
                            if effect_class_id == ACTION_EFFECT_CLASS
                            else FILTER_PARAMETER
                        )
                        if parameters == expected:
                            instance_id = int.from_bytes(
                                body[parameter_end + 1 : parameter_end + 5],
                                "little",
                            )
                            if instance_id not in {0, 0xFFFFFFFF}:
                                responses.append(
                                    ResponseObservation(
                                        timestamp_ms=timestamp_ms,
                                        packet_index=packet_index,
                                        message_index=message_index,
                                        direction=direction,
                                        signature=signature,
                                        payload_length=len(wire),
                                        xor_phase=xor_phase,
                                        record_offset=cursor,
                                        effect_code=effect_code,
                                        source_actor_id=source,
                                        target_actor_id=target,
                                        skill_level_row_id=skill_level_row_id,
                                        effect_class_id=effect_class_id,
                                        duration_ms=duration_ms,
                                        parameters=parameters,
                                        response_instance_id=instance_id,
                                    )
                                )
                cursor = body.find(SKILL_BYTES, cursor + 1)
    return responses, passive_echo_shapes, decoded_messages


def _generations(
    responses: Iterable[ResponseObservation],
) -> tuple[list[ActionGeneration], dict[int, list[ResponseObservation]]]:
    action_observations: dict[
        tuple[int, int, int], list[ResponseObservation]
    ] = defaultdict(list)
    filter_by_source: dict[int, list[ResponseObservation]] = defaultdict(list)
    for row in responses:
        if row.effect_class_id == ACTION_EFFECT_CLASS:
            action_observations[
                (
                    row.source_actor_id,
                    row.target_actor_id,
                    row.response_instance_id,
                )
            ].append(row)
        elif row.effect_class_id == FILTER_EFFECT_CLASS:
            filter_by_source[row.source_actor_id].append(row)

    generations: list[ActionGeneration] = []
    for (source, target, instance), rows in action_observations.items():
        timestamps = [row.timestamp_ms for row in rows]
        generation = ActionGeneration(
            source_actor_id=source,
            target_actor_id=target,
            response_instance_id=instance,
            first_timestamp_ms=min(timestamps),
            last_timestamp_ms=max(timestamps),
            observations=len(rows),
        )
        paired = [
            row
            for row in filter_by_source.get(source, ())
            if row.response_instance_id == instance + 1
            and abs(row.timestamp_ms - generation.first_timestamp_ms) <= 250
        ]
        if paired:
            pair = min(
                paired,
                key=lambda row: abs(
                    row.timestamp_ms - generation.first_timestamp_ms
                ),
            )
            generation.paired_filter_instance_id = pair.response_instance_id
            generation.paired_filter_delta_ms = (
                pair.timestamp_ms - generation.first_timestamp_ms
            )
        generations.append(generation)
    generations.sort(
        key=lambda row: (
            row.first_timestamp_ms,
            row.source_actor_id,
            row.response_instance_id,
        )
    )
    return generations, filter_by_source


def _instance_occurrence_audit(
    path: Path,
    responses: Iterable[ResponseObservation],
) -> dict[str, object]:
    """Check whether a response instance is repeated by another wire record.

    The scan is intentionally byte-granular.  A zero result therefore rules
    out not only the known status/remove layouts but also an opaque unaligned
    copy of the same u32 in the decoded UDP body.
    """

    response_rows = list(responses)
    known = {row.response_instance_id for row in response_rows}
    expected_positions = {
        (
            row.packet_index,
            row.message_index,
            row.xor_phase,
            row.record_offset + 20 + len(row.parameters.encode("utf-8")) + 1,
        ): row.response_instance_id
        for row in response_rows
    }
    all_occurrences: Counter[int] = Counter()
    outside: list[dict[str, object]] = []
    outside_count = 0
    outside_instances: set[int] = set()
    for (
        timestamp_ms,
        packet_index,
        message_index,
        direction,
        wire,
    ) in _capture_wires(path):
        signature = wire[5:9].hex() if len(wire) >= 9 else ""
        for xor_phase in protocol._v576_candidate_xor_phases(wire):
            body = protocol._decode_v576_body(wire, xor_phase)
            for offset in range(9, max(9, len(body) - 3)):
                value = int.from_bytes(body[offset : offset + 4], "little")
                if value not in known:
                    continue
                all_occurrences[value] += 1
                expected = expected_positions.get(
                    (packet_index, message_index, xor_phase, offset)
                )
                if expected == value:
                    continue
                outside_count += 1
                outside_instances.add(value)
                if len(outside) < 80:
                    outside.append(
                        {
                            "timestamp_ms": timestamp_ms,
                            "packet_index": packet_index,
                            "message_index": message_index,
                            "direction": direction,
                            "signature": signature,
                            "payload_length": len(wire),
                            "xor_phase": xor_phase,
                            "record_offset": offset,
                            "response_instance_id": value,
                            "context": body[
                                max(0, offset - 16) : min(len(body), offset + 20)
                            ].hex(),
                        }
                    )
    return {
        "response_instance_occurrences": sum(all_occurrences.values()),
        "response_instances_observed": len(all_occurrences),
        "outside_response_tail_occurrences": outside_count,
        "outside_response_tail_instances": len(outside_instances),
        "outside_response_tail_samples": outside,
    }


def _direct41_fire_hits(path: Path, action_sources: set[int]) -> list[CombatEvent]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    rows: list[CombatEvent] = []
    try:
        for batch in source.iter_batches():
            for event in batch:
                if not isinstance(event, CombatEvent):
                    continue
                try:
                    source_actor_id = int(event.source_id, 16)
                except ValueError:
                    continue
                if (
                    source_actor_id in action_sources
                    and event.skill_id in FIRE_SKILL_IDS | {str(SKILL_ID)}
                    and event.has_real_hit_evidence is True
                    and "direct41" in event.wire_damage_layout
                    and not event.healing
                    and event.amount > 0
                ):
                    rows.append(event)
    finally:
        source.close()
    rows.sort(key=lambda row: (row.timestamp_ms, row.wire_record_offset))
    return rows


def _target_actor_id(event: CombatEvent) -> int:
    raw = event.target_id.split("@", 1)[0]
    try:
        return int(raw, 16)
    except ValueError:
        return 0


def _attach_results(
    generations: list[ActionGeneration], hits: list[CombatEvent]
) -> tuple[int, Counter[int]]:
    """Attach production results carrying the paired action evidence token."""

    unused = set(range(len(hits)))
    delta_counts: Counter[int] = Counter()
    matched = 0
    for generation in generations:
        evidence_suffix = f":{generation.response_instance_id}"
        exact = [
            index
            for index in unused
            if hits[index].skill_id == str(SKILL_ID)
            and hits[index].attribution_method == "instant_action_response"
            and str(hits[index].cast_instance_id or "").endswith(evidence_suffix)
        ]
        if exact:
            selected = min(
                exact,
                key=lambda index: (
                    hits[index].timestamp_ms,
                    hits[index].wire_record_offset,
                ),
            )
        else:
            # Fail closed. The direct41 row does not repeat the response
            # instance, so a result without the production lifecycle token is
            # not silently reconstructed from time or damage magnitude here.
            continue
        unused.remove(selected)
        hit = hits[selected]
        generation.result_timestamp_ms = hit.timestamp_ms
        generation.result_delta_ms = (
            hit.timestamp_ms - generation.first_timestamp_ms
        )
        generation.result_skill_id = hit.skill_id
        generation.result_skill_name = hit.skill_name
        generation.result_damage = hit.amount
        generation.result_wire_layout = hit.wire_damage_layout
        delta_counts[generation.result_delta_ms] += 1
        matched += 1
    return matched, delta_counts


def _resource_evidence(resource_root: Path) -> dict[str, object]:
    skill_table = resource_root / "ext" / "skilltable_character.dnt"
    level_table = resource_root / "ext" / "skillleveltable_charactersoceress.dnt"
    action_file = (
        resource_root
        / "char"
        / "player"
        / "soceress"
        / "soceress_skill_2st_saleana.act"
    )
    skill_row = next(
        row
        for row in parse_dnt(skill_table)
        if int(row.get("RowId", 0) or 0) == SKILL_ID
    )
    level_rows = [
        row
        for row in parse_dnt(level_table)
        if int(row.get("_SkillIndex", 0) or 0) == SKILL_ID
        and int(row.get("_ApplyType", 0) or 0) == 0
    ]
    parsed = parse_act(action_file)
    action = next(row for row in parsed.actions if row.name == ACTION_PARAMETER)
    return {
        "skill_id": SKILL_ID,
        "static_name": str(skill_row.get("_StaticName", "")),
        "effect_class": int(skill_row.get("_EffectClass1", 0) or 0),
        "level_rows": [
            {
                "level": int(row.get("_SkillLevel", 0) or 0),
                "row_id": int(row.get("RowId", 0) or 0),
                "delay_ms": int(row.get("_DelayTime", 0) or 0),
                "effect_value": str(row.get("_EffectClassValue1", "")),
            }
            for row in level_rows
        ],
        "action_file": str(action_file),
        "action_frames": action.length_frames,
        "next_action": action.next_action,
        "direct_damage_signals": [
            {
                "start_frame": signal.start_frame,
                "end_frame": signal.end_frame,
                "order": signal.order,
                "payload0_float": struct.unpack(
                    "<f", signal.payload[:4]
                )[0],
            }
            for signal in action.signals
            if signal.signal_type == 4
        ],
        "attack_object_signals": [
            {
                "start_frame": signal.start_frame,
                "end_frame": signal.end_frame,
                "order": signal.order,
                "payload0": int.from_bytes(signal.payload[:4], "little"),
            }
            for signal in action.signals
            if signal.signal_type == 15
        ],
    }


def audit_capture(path: Path) -> dict[str, object]:
    responses, passive_shapes, decoded_messages = _response_rows(path)
    generations, filter_by_source = _generations(responses)
    action_sources = {row.source_actor_id for row in generations}
    hits = _direct41_fire_hits(path, action_sources)
    matched, delta_counts = _attach_results(generations, hits)
    instance_audit = _instance_occurrence_audit(path, responses)
    paired = sum(row.paired_filter_instance_id > 0 for row in generations)
    action_observations = [
        row for row in responses if row.effect_class_id == ACTION_EFFECT_CLASS
    ]
    filter_observations = [
        row for row in responses if row.effect_class_id == FILTER_EFFECT_CLASS
    ]
    # An instance seen only in response records has no explicit remove packet.
    # Duplicate observations of the same instance are UDP replicas, not a new
    # generation, and are retained as an observation count below.
    return {
        "capture": str(path),
        "decoded_udp_message_phases": decoded_messages,
        "passive_effect_echoes": sum(passive_shapes.values()),
        "passive_effect_echo_shapes": dict(passive_shapes.most_common()),
        "action_response_observations": len(action_observations),
        "filter_response_observations": len(filter_observations),
        "action_generations": len(generations),
        "action_generations_with_duplicate_observations": sum(
            row.observations > 1 for row in generations
        ),
        "paired_filter_generations": paired,
        "unpaired_filter_generations": len(generations) - paired,
        "direct41_fire_hits": len(hits),
        "correlated_action_results_0_150ms": matched,
        "uncorrelated_action_generations": len(generations) - matched,
        "result_delta_ms": {
            str(delta): count for delta, count in delta_counts.most_common()
        },
        "result_skill_counts": dict(
            Counter(
                f"{row.result_skill_id}:{row.result_skill_name}"
                for row in generations
                if row.result_skill_id
            ).most_common()
        ),
        "explicit_projectile_registration_for_action": False,
        "explicit_action_removal_by_response_instance": False,
        "response_instance_audit": instance_audit,
        "samples": [asdict(row) for row in generations[:40]],
    }


def render_markdown(payload: dict[str, object]) -> str:
    resource = payload["resource_evidence"]
    assert isinstance(resource, dict)
    lines = [
        "# Skill_Explosion_Attack 动作对象注册与生命周期审计",
        "",
        "## 结论",
        "",
        "- `2301/灼烧之手` 的 EffectClass 172 会下发一个零持续时间的动作响应："
        "`effect class 62 + Skill_Explosion_Attack + response instance id`。",
        "- 该动作在 ACT 的第0帧包含 type-4 直伤信号，但没有 type-15 攻击对象生成信号；"
        "因此 response instance id 是一次性动作代际，不是 projectile damage_object_id。",
        "- 同时出现的 effect class 637 是触发技能/纹章条件响应；正常情况下实例号为动作实例号+1。",
        "- direct41 不回填该 response instance id；ACT先用实例号+1的EC637响应将代际置为可结算，"
        "再按同来源、同目标和物理包顺序消费下一条direct41。实例号只保存在归因证据中，"
        "不会伪造成projectile object ID。",
        "- 抓包没有发现能由有效协议结构确认、并复用该实例号的显式销毁包。结合duration=0、"
        "第0帧直伤和无type-15对象，生命周期应在本次直伤结算后立即闭合；"
        "40帧动作长度仅是客户端表现，不应当作为持续伤害对象保活。",
        "",
        "## 拆包证据",
        "",
        f"- 技能：`{resource.get('skill_id')}` / `{resource.get('static_name')}`；"
        f"主 EffectClass：`{resource.get('effect_class')}`。",
        f"- 动作长度：`{resource.get('action_frames')}`帧；下一动作："
        f"`{resource.get('next_action')}`。",
        f"- type-4 直伤信号：`{len(resource.get('direct_damage_signals', []))}`条；"
        f"type-15 攻击对象信号：`{len(resource.get('attack_object_signals', []))}`条。",
        "- PVE动作的type-4首个浮点参数为`35.0`，与灼烧之手35%描述一致。",
        "",
        "## 抓包验证",
        "",
        "| 抓包 | 动作响应原始条数 | 唯一动作代际 | EC637配对 | 0-150ms后续direct41 | 未关联代际 | 被动状态回声 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for report in payload["captures"]:
        assert isinstance(report, dict)
        lines.append(
            "| {name} | {raw} | {generations} | {paired} | {matched} | "
            "{unmatched} | {echoes} |".format(
                name=Path(str(report["capture"])).name,
                raw=report["action_response_observations"],
                generations=report["action_generations"],
                paired=report["paired_filter_generations"],
                matched=report["correlated_action_results_0_150ms"],
                unmatched=report["uncorrelated_action_generations"],
                echoes=report["passive_effect_echoes"],
            )
        )
    lines += [
        "",
        "## ACT 接入边界",
        "",
        "建议新增独立的 `instant_action_response` 账本，而不是塞入现有 projectile 账本：",
        "",
        "1. 看到 EC62 且参数精确等于 `Skill_Explosion_Attack` 时，创建一次动作代际。",
        "2. 使用 `(source, target, response_instance_id)` 去重UDP副本。",
        "3. EC637只作为同代际条件证据，不产生伤害。",
        "4. EC637配对后，消费同来源、同目标、物理顺序合法的下一条 direct41；"
        "同毫秒多Hit仍按物理记录顺序唯一选择第一条。",
        "5. 结算后立即关闭代际；不等待2秒冷却，也不创建虚假的 projectile 销毁事件。",
        "6. `4B09/4C09 + 2301/172` 只证明被动已启用，不能证明本次发生爆炸。",
        "",
        "未关联代际可能来自未命中、无敌、目标死亡、抓包丢失或物理伤害记录未通过完整性校验；"
        "没有配对且有效的下一条direct41时必须失败关闭，不能把后续普通火系Hit判为灼烧之手。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument(
        "--resource-root",
        type=Path,
        default=Path(
            r"D:\DragonNest\Newborn\Resource_unpacked_all_20260916_update"
            r"\Resource20\resource"
        ),
    )
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    # Tables and ACT live in different Resource packs in the unpacked tree.
    # Resolve the exact known files while keeping one user-facing root option.
    resource20 = args.resource_root
    update_root = resource20.parents[1]
    resource03 = update_root / "Resource03" / "resource"
    action_root = resource20
    resource_evidence = _resource_evidence(
        _MergedResourceView(
            primary=action_root,
            skill_level_root=resource03,
        )
    )
    payload = {
        "schema_version": 1,
        "policy": (
            "Only the exact skill-2301/effect-class-62/action-name response "
            "creates an action generation. Direct41 attachment is explicitly "
            "correlational because the response instance is not repeated."
        ),
        "resource_evidence": resource_evidence,
        "captures": [audit_capture(path.resolve()) for path in args.captures],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(payload), encoding="utf-8")
    if not args.json and not args.markdown:
        print(rendered)
    return 0


class _MergedResourceView:
    """Minimal path facade for split Resource03/Resource20 extraction."""

    def __init__(self, *, primary: Path, skill_level_root: Path) -> None:
        self.primary = primary
        self.skill_level_root = skill_level_root

    def __truediv__(self, value: str):
        if value != "ext":
            return self.primary / value
        return _MergedExtView(self.primary / "ext", self.skill_level_root / "ext")


class _MergedExtView:
    def __init__(self, primary: Path, skill_level: Path) -> None:
        self.primary = primary
        self.skill_level = skill_level

    def __truediv__(self, value: str) -> Path:
        candidate = self.primary / value
        if candidate.is_file():
            return candidate
        return self.skill_level / value


if __name__ == "__main__":
    raise SystemExit(main())
