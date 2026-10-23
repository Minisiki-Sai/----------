"""Read-only trace for exact Saint entity hits rejected by the PvE target gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.v576 import actor_state as actor_state_module  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    source = V576DiagnosticReplaySource(args.capture, DATA_DIR, {})
    decoder = source._decoder
    original = decoder._finalize_wire_damage_owner
    original_observe = decoder._observe_damage_target
    directly_player_confirmed_targets: set[int] = set()
    rows: list[dict[str, object]] = []
    last_decision_reason = ""
    original_annotate = actor_state_module.annotate_existing_entity_damage

    def annotate_with_reason(*args, **kwargs):
        nonlocal last_decision_reason
        decision = original_annotate(*args, **kwargs)
        last_decision_reason = decision.reason
        return decision

    actor_state_module.annotate_existing_entity_damage = annotate_with_reason

    def observe(source_actor_id, target_actor_id, timestamp, *, confirmed):
        players = decoder.skill_actor_ids | decoder.local_actor_ids | set(decoder.known_actor_names)
        result = original_observe(
            source_actor_id, target_actor_id, timestamp, confirmed=confirmed
        )
        if (
            result
            and confirmed
            and source_actor_id in players
            and target_actor_id is not None
        ):
            directly_player_confirmed_targets.add(int(target_actor_id))
        return result

    decoder._observe_damage_target = observe

    def traced(event, packet):
        entity = int(event.wire_source_actor_id or 0)
        template = decoder.monster_entities.get(entity)
        template_id = int(getattr(template, "template_id", template) or 0)
        if template_id == 30401 and not event.healing and event.amount > 0:
            target = int(event.wire_target_actor_id or 0)
            before = {
                "timestamp": float(packet.timestamp_seconds),
                "source_id": event.source_id,
                "target_id": event.target_id,
                "wire_entity_id": entity,
                "wire_target_id": target,
                "amount": int(event.amount),
                "skill_id_before": int(event.skill_id or 0),
                "method_before": event.attribution_method,
                "target_in_monsters": target in decoder.monster_entities,
                "target_confirmed": target in decoder.confirmed_damage_target_ids,
                "target_directly_player_confirmed": target in directly_player_confirmed_targets,
                "target_production_direct_player_confirmed": target in decoder.direct_player_confirmed_damage_target_ids,
                "target_damage_count": int(decoder.damage_target_counts.get(target, 0)),
                "target_recent_count": int(decoder.recent_damage_target_counts.get(target, 0)),
                "target_is_known_player": target in (
                    decoder.skill_actor_ids | decoder.local_actor_ids | set(decoder.known_actor_names)
                ),
                "boss_actor_id": decoder.boss_actor_id,
                "map_id": decoder.detected_map_id,
                "map_active": bool(decoder.detected_map_active),
            }
            updated = original(event, packet)
            before.update({
                "skill_id_after": int(updated.skill_id or 0),
                "method_after": updated.attribution_method,
                "owner_evidence_after": updated.entity_owner_evidence_id,
                "attack_evidence_after": updated.entity_attack_evidence_id,
                "owner_gate_reason": last_decision_reason,
            })
            rows.append(before)
            return updated
        return original(event, packet)

    decoder._finalize_wire_damage_owner = traced
    try:
        source.poll()
    finally:
        actor_state_module.annotate_existing_entity_damage = original_annotate
        source.close()

    result = {"capture": str(args.capture), "rows": rows}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
