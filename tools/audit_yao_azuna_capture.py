from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


WATCHED_NAMES = {
    "查克拉治愈术",
    "查克拉治愈术EX",
    "能量之轮",
    "奇迹之轮",
    "阿兹那之约",
    "阿兹纳之约",
    "待识别伤害",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = V576DiagnosticReplaySource(args.capture, DATA_DIR, {})
    try:
        events = source.poll()
        decoder = source._decoder
        decoder_stats = {
            "prebattle_seeded": int(
                getattr(decoder, "prebattle_summon_seeded_count", 0) or 0
            ),
            "prebattle_state_seen": bool(
                getattr(decoder, "_prebattle_summon_state_seen", False)
            ),
            "bindings": {
                str(entity_id): {
                    "template_id": int(binding.template_id),
                    "owner_id": int(binding.record.owner_id),
                    "generation": int(binding.generation),
                    "born_at": float(binding.born_at),
                    "birth_source": str(
                        getattr(
                            getattr(decoder, "entity_owner_evidence").active_lives.get(entity_id),
                            "birth_source",
                            "",
                        )
                    ),
                }
                for entity_id, binding in getattr(
                    getattr(decoder, "entity_owner_evidence", None),
                    "bindings",
                    {},
                ).items()
                if int(binding.template_id) in {33601, 33603}
            },
            "summon_owners": {
                str(k): list(v)
                for k, v in getattr(decoder, "summon_owners", {}).items()
            },
            "monster_entities": {
                str(k): int(getattr(v, "template_id", 0) or 0)
                for k, v in getattr(decoder, "monster_entities", {}).items()
            },
        }
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    yao_ids = {
        str(player.source_id)
        for player in snapshot.players
        if str(player.job) == "曜"
    }
    combat = [
        row
        for row in snapshot.combat_events
        if str(row.source_id) in yao_ids and not row.healing and int(row.amount) > 0
    ]
    by_skill: dict[tuple[str, str], dict[str, object]] = {}
    by_wire_entity: dict[tuple[int, int], Counter[tuple[str, str, str]]] = defaultdict(Counter)
    for row in combat:
        key = (str(row.skill_id), str(row.skill_name))
        target = by_skill.setdefault(
            key,
            {"hits": 0, "damage": 0, "methods": Counter(), "entities": Counter()},
        )
        target["hits"] += 1
        target["damage"] += int(row.amount)
        target["methods"][str(row.attribution_method)] += 1
        target["entities"][(int(row.wire_entity_id or 0), int(row.entity_template_id or 0))] += 1
        by_wire_entity[(int(row.wire_entity_id or 0), int(row.entity_template_id or 0))][
            (str(row.skill_id), str(row.skill_name), str(row.attribution_method))
        ] += 1

    entity_history: dict[tuple[object, ...], dict[str, int]] = {}
    for row in snapshot.combat_events:
        if row.healing or int(row.amount) <= 0:
            continue
        if not (
            int(row.wire_source_actor_id or 0) in {51, 377}
            or int(row.wire_entity_id or 0) in {51, 377}
            or int(row.entity_template_id or 0) == 33601
        ):
            continue
        key = (
            row.source_id,
            row.skill_id,
            row.skill_name,
            row.attribution_method,
            int(row.wire_source_actor_id or 0),
            int(row.wire_entity_id or 0),
            int(row.entity_template_id or 0),
        )
        target = entity_history.setdefault(
            key,
            {
                "hits": 0,
                "damage": 0,
                "first_ms": int(row.timestamp_ms),
                "last_ms": int(row.timestamp_ms),
            },
        )
        target["hits"] += 1
        target["damage"] += int(row.amount)
        target["first_ms"] = min(target["first_ms"], int(row.timestamp_ms))
        target["last_ms"] = max(target["last_ms"], int(row.timestamp_ms))

    details = []
    for index, row in enumerate(combat):
        if row.skill_name not in WATCHED_NAMES:
            continue
        details.append(
            {
                "index": index,
                **asdict(row),
                "nearby": [
                    {
                        "timestamp_ms": near.timestamp_ms,
                        "skill_id": near.skill_id,
                        "skill_name": near.skill_name,
                        "amount": near.amount,
                        "method": near.attribution_method,
                        "wire_entity_id": near.wire_entity_id,
                        "entity_template_id": near.entity_template_id,
                        "wire_damage_layout": near.wire_damage_layout,
                    }
                    for near in combat[max(0, index - 4) : index + 5]
                ],
            }
        )

    report = {
        "capture": str(args.capture),
        "yao_ids": sorted(yao_ids),
        "decoder": decoder_stats,
        "skills": [
            {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "hits": int(row["hits"]),
                "damage": int(row["damage"]),
                "methods": dict(row["methods"]),
                "entities": {
                    f"{entity}:{template}": count
                    for (entity, template), count in row["entities"].items()
                },
            }
            for (skill_id, skill_name), row in sorted(
                by_skill.items(), key=lambda item: -int(item[1]["damage"])
            )
        ],
        "wire_entities": [
            {
                "entity_id": entity_id,
                "template_id": template_id,
                "skills": [
                    {
                        "skill_id": skill_id,
                        "skill_name": skill_name,
                        "method": method,
                        "hits": hits,
                    }
                    for (skill_id, skill_name, method), hits in rows.most_common()
                ],
            }
            for (entity_id, template_id), rows in sorted(by_wire_entity.items())
            if entity_id or template_id
        ],
        "entity_history": [
            {
                "source_id": key[0],
                "skill_id": key[1],
                "skill_name": key[2],
                "method": key[3],
                "wire_source_actor_id": key[4],
                "wire_entity_id": key[5],
                "template_id": key[6],
                **value,
            }
            for key, value in sorted(
                entity_history.items(), key=lambda item: item[1]["first_ms"]
            )
        ],
        "details": details,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
