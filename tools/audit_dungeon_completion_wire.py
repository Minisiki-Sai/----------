"""Compare terminal result/trigger wire evidence without scoring a capture."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.clear_trigger import RULES
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.live_dps.capture_sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger
from dragon_nest_builder.live_dps.models import CombatEvent, BossActionEvent
from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler, decode_captured_frame, split_v576_payload,
)
from dragon_nest_builder.live_dps.settlement import (
    SettlementEvent, SettlementLedger, decode_settlement,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import decode_registered_server_tcp_envelope
from tools.analyze_npcap_trace import read_trace


def audit(path: Path, *, events: bool = False, aggregate: bool = False, score_lava: bool = False, tcp_window: tuple[float, float] | None = None) -> None:
    transport = V576TransportReassembler()
    decoder = _V576PacketEventDecoder(ROOT / "dragon_nest_builder" / "data", {}) if events else None
    event_ledger = V576EventLedger()
    settlement_ledger = SettlementLedger()
    aggregator = CombatAggregator() if aggregate else None
    last_elapsed_ms = 0
    last_state_event = (0, "")
    combat_tail = []
    target_damage = Counter()
    target_last_hit = {}
    target_max_hp = {}
    boss_events = []
    lava_events = []
    origin = None
    rows = []
    raw_trigger_rows = []
    udp_end_rows = []
    udp_end_context = []
    raw_tcp_window = []
    raw_tcp_acks = []
    raw_unparsed_window = []
    previous_tcp_end = {}
    for timestamp, adapter, original_length, frame in read_trace(path):
        if adapter >= 250:
            continue
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            if origin is not None and tcp_window and tcp_window[0] <= timestamp - origin <= tcp_window[1]:
                raw_unparsed_window.append((round(timestamp - origin, 3), adapter, original_length, len(frame), frame[:18].hex()))
            continue
        if origin is not None and packet.transport.upper() == "TCP" and packet.inferred_direction() == "server_to_client" and packet.payload:
            flow = (packet.source_ip, packet.source_port, packet.destination_ip, packet.destination_port)
            sequence = packet.tcp_sequence
            expected = previous_tcp_end.get(flow)
            gap = sequence - expected if sequence is not None and expected is not None else None
            if sequence is not None and (expected is None or sequence + len(packet.payload) > expected):
                previous_tcp_end[flow] = sequence + len(packet.payload)
            at = timestamp - origin
            if tcp_window and tcp_window[0] <= at <= tcp_window[1]:
                raw_tcp_window.append((round(at, 3), len(packet.payload), gap, packet.source_ip, packet.source_port, adapter))
        if origin is not None and packet.transport.upper() == "TCP" and packet.inferred_direction() == "client_to_server" and tcp_window and tcp_window[0] <= timestamp - origin <= tcp_window[1]:
            raw_tcp_acks.append((round(timestamp - origin, 3), packet.tcp_sequence, packet.tcp_acknowledgment, len(packet.payload), packet.destination_ip, packet.destination_port))
        for logical in transport.feed(packet):
            if origin is None:
                origin = logical.timestamp_seconds
            last_elapsed_ms = max(last_elapsed_ms, round((logical.timestamp_seconds - origin) * 1000))
            if logical.transport.upper() == "UDP" and logical.inferred_direction() == "server_to_client":
                for segment in split_v576_payload(logical.payload):
                    if len(segment) >= 9 and segment[5:9] == bytes.fromhex("f1110f00"):
                        udp_end_rows.append((round(logical.timestamp_seconds - origin, 3), len(segment), segment[:100].hex()))
                        if decoder is not None:
                            boss_actor = getattr(decoder, "boss_actor_id", None)
                            boss_template = getattr(decoder, "monster_entities", {}).get(boss_actor)
                            udp_end_context.append((round(logical.timestamp_seconds - origin, 3), getattr(decoder, "detected_map_id", None), boss_actor, getattr(boss_template, "template_id", None), combat_tail[-2:]))
            if decoder is not None and event_ledger.accept(logical):
                for event in decoder.feed_events(logical):
                    if "漫长熔浆" in str(getattr(event, "skill_name", "")):
                        lava_events.append(event)
                    if aggregator is not None:
                        aggregator.ingest(event)
                    if not isinstance(event, SettlementEvent):
                        event_time = getattr(event, "timestamp_ms", -1)
                        if event_time >= last_state_event[0]:
                            last_state_event = (event_time, type(event).__name__)
                    if isinstance(event, SettlementEvent):
                        settlement_ledger.observe(event)
                    if isinstance(event, CombatEvent) and event.amount and not event.healing:
                        if event.target_job.strip().partition(" ")[0].casefold() == "boss":
                            settlement_ledger.observe_boss_damage(event.timestamp_ms, event.source_id, event.target_id)
                        target_damage[event.target_id] += event.amount
                        target_last_hit[event.target_id] = round(event.timestamp_ms / 1000, 3)
                        target_max_hp[event.target_id] = max(target_max_hp.get(event.target_id, 0), event.target_max_hp)
                        combat_tail.append((round(event.timestamp_ms / 1000, 3), event.source_id, event.target_id, event.target_name, event.skill_name, event.amount))
                        if len(combat_tail) > 50:
                            combat_tail.pop(0)
                    elif isinstance(event, BossActionEvent) and event.skill_id == "boss_completion_trigger":
                        boss_events.append((round(event.timestamp_ms / 1000, 3), event.source_id, event.template_id, event.evidence_types))
            if logical.transport.upper() != "TCP" or logical.inferred_direction() != "server_to_client":
                continue
            at = logical.timestamp_seconds - origin
            result = decode_settlement(logical, origin)
            decoded = decode_registered_server_tcp_envelope(
                logical.payload, connection_context=logical,
            )
            body = decoded[0] if decoded else b""
            opcode = body[2:4].hex() if len(body) >= 4 else "undecoded"
            matched_rules = [
                row for row in RULES
                if len(logical.payload) == 15 and logical.payload[3:11] == row.prefix
            ]
            if matched_rules:
                raw_trigger_rows.append((round(at, 3), [(row.index, row.map_ids, row.boss_template_id) for row in matched_rules]))
            rows.append((at, opcode, len(body), body[:40].hex(), result))
    print(path.name, "last_tcp", round(rows[-1][0], 3) if rows else None)
    print("results", [(round(at, 3), result.clear_table_id, len(result.actor_ids)) for at, _, _, _, result in rows if result])
    print("triggers", raw_trigger_rows)
    print("udp_end", udp_end_rows[-12:])
    print("stage_result_candidates", [(round(at, 3), opcode, size, prefix) for at, opcode, size, prefix, _ in rows if opcode in {"0603", "0629", "0c05"}])
    print("tcp_around_last_end", [(round(at, 3), opcode, size, prefix) for at, opcode, size, prefix, _ in rows if udp_end_rows and udp_end_rows[-1][0] - 0.2 <= at <= udp_end_rows[-1][0] + 25][:90])
    print("terminal_protocol", [(round(at, 3), opcode, size, prefix) for at, opcode, size, prefix, _ in rows if opcode in {"0603", "0604", "0629", "0c05", "0313", "0201"} and at > 100][-45:])
    print("last_120s_opcodes", Counter((opcode, size) for at, opcode, size, _, _ in rows if rows[-1][0] - 120 <= at <= rows[-1][0]).most_common(25))
    if decoder is not None:
        print("decoder_boss", getattr(decoder, "boss_actor_id", None), getattr(decoder, "detected_map_id", None))
        print("combat_tail", combat_tail[-30:])
        print("target_damage", [(target, amount, target_max_hp[target], target_last_hit[target]) for target, amount in target_damage.most_common(12)])
        print("boss_completion_events", boss_events)
        print("udp_end_context", udp_end_context)
        print("completion", settlement_ledger.snapshot(current_timestamp_ms=last_elapsed_ms))
        print("last_state_event", last_state_event, "last_packet_ms", last_elapsed_ms)
        print("lava_types", Counter(type(event).__name__ for event in lava_events))
        print("lava_status_groups", Counter((getattr(event, "effect_key", ""), getattr(event, "value", ""), getattr(event, "duration_ms", ""), getattr(event, "target_scope", "")) for event in lava_events if type(event).__name__ == "BossStatusEvent").most_common(20))
        print("lava_status_sources", Counter((getattr(event, "duration_ms", ""), getattr(event, "effect_code", ""), getattr(event, "parameters", "")) for event in lava_events if type(event).__name__ == "BossStatusEvent" and getattr(event, "target_scope", "") == "boss").most_common(12))
        print("lava_casts", [(round(event.timestamp_ms / 1000, 3), event.source_id) for event in lava_events if type(event).__name__ == "SkillCastEvent"])
        lava_cast_times = [event.timestamp_ms for event in lava_events if type(event).__name__ == "SkillCastEvent"]
        lava_status_times = [event.timestamp_ms for event in lava_events if type(event).__name__ == "BossStatusEvent" and getattr(event, "target_scope", "") == "boss" and getattr(event, "duration_ms", 0) == 15000]
        print("lava_refresh_spans", [(round(start / 1000, 3), round((min(rows) - start) / 1000, 3), round((max(rows) - min(rows)) / 1000, 3), len(rows)) for start, stop in zip(lava_cast_times, lava_cast_times[1:] + [10**12]) if (rows := [t for t in lava_status_times if start <= t < stop])])
        print("lava_boss_hits", [(round(event.timestamp_ms / 1000, 3), event.source_id, event.target_id) for event in lava_events if type(event).__name__ == "CombatEvent" and str(getattr(event, "target_job", "")).casefold().startswith("boss")][:80])
        print("lava_boss_hit_targets", Counter(event.target_id for event in lava_events if type(event).__name__ == "CombatEvent" and str(getattr(event, "target_job", "")).casefold().startswith("boss")))
        if aggregator is not None:
            snapshot = aggregator.snapshot(include_events=score_lava)
            print("aggregator_completion", snapshot.instance_completion)
            print("lava_snapshot", [(row.source_id, row.target_id, row.key, row.value, round(row.coverage, 3), round(row.effective_coverage, 3), round(row.uptime_seconds, 3), len(row.intervals), len(row.effective_intervals), row.effective_state, row.effective_reason) for row in snapshot.boss_effects if "漫长熔浆" in row.skill_name])
            print("fire_reduction_snapshot", [(row.target_id, row.skill_name, row.value, row.stack_group, round(row.coverage, 3), round(row.effective_coverage, 3), row.intervals[:2]) for row in snapshot.boss_effects if row.key == "减火抗%" and row.target_id in {"0x000000E2@4181009", "0x0000007E@4181008"}])
            print("lava_status_targets", Counter((event.target_id, event.key, event.value, event.duration_seconds) for event in snapshot.boss_effect_events if "漫长熔浆" in event.skill_name).most_common(30))
            if score_lava:
                engine = TeamScoreEngine(DataStore.load())
                context = engine._build_support_counterfactual_context(
                    snapshot, ((0.0, snapshot.elapsed_seconds),),
                    frozenset({"0x000000E2@4181009"}),
                )
                print("lava_score_intervals", [(identity, context.intervals_by_identity.get(identity), context.effective_intervals_by_identity.get(identity), context.effective_state_by_identity.get(identity)) for identity in context.intervals_by_identity if "漫长熔浆" in identity])
                print("lava_score_contribution", [(identity, context.actual_detail_contributions.get(identity, 0.0)) for identity in context.intervals_by_identity if "漫长熔浆" in identity])
    if tcp_window:
        print("raw_tcp_window", raw_tcp_window)
        print("raw_tcp_acks", raw_tcp_acks)
        print("raw_unparsed_window", raw_unparsed_window[:50], "count", len(raw_unparsed_window))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument("--events", action="store_true")
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("--score-lava", action="store_true")
    parser.add_argument("--tcp-window", type=float, nargs=2)
    args = parser.parse_args()
    for path in args.captures:
        audit(path, events=args.events or args.aggregate or args.score_lava, aggregate=args.aggregate or args.score_lava, score_lava=args.score_lava, tcp_window=tuple(args.tcp_window) if args.tcp_window else None)


if __name__ == "__main__":
    main()
