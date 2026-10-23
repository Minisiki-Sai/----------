from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from tools.build_dungeon_map_catalog import parse_dnt  # noqa: E402


DEFAULT_RESOURCE_ROOT = Path(
    r"E:\ext0811.rar\Resource00\Resource00\resource"
)


def _skill_level_rows(resource_root: Path) -> dict[int, int]:
    result: dict[int, int] = {}
    for path in (resource_root / "ext").glob("skillleveltable_character*.dnt"):
        for row in parse_dnt(path):
            row_id = int(row.get("RowId", 0) or 0)
            skill_id = int(row.get("_SkillIndex", 0) or 0)
            if row_id > 0 and skill_id > 0:
                result[row_id] = skill_id
    return result


def _u32_occurrences(payload: bytes, value: int) -> tuple[int, ...]:
    raw = int(value).to_bytes(4, "little", signed=False)
    result: list[int] = []
    offset = payload.find(raw)
    while offset >= 0:
        result.append(offset)
        offset = payload.find(raw, offset + 1)
    return tuple(result)


def audit(path: Path, resource_root: Path) -> dict[str, Any]:
    level_to_skill = _skill_level_rows(resource_root)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    damage_skill_ids = frozenset(int(value) for value in decoder.damage_skill_ids)
    original_feed = decoder.feed
    rows: list[dict[str, Any]] = []

    def feed(packet, packet_features=None):
        event = original_feed(packet, packet_features)
        decoded_message = decoder.last_decoded_message
        if (
            isinstance(event, CombatEvent)
            and not event.healing
            and event.source_id != "__incoming_damage__"
            and decoded_message is not None
            and "hit" in str(decoded_message.layout)
        ):
            assigned_skill_id = 0
            try:
                assigned_skill_id = int(str(event.skill_id))
            except ValueError:
                assigned_skill_id = 0
            phase_rows: list[dict[str, Any]] = []
            for phase in _v576_candidate_xor_phases(packet.payload):
                body = _decode_v576_body(packet.payload, phase)
                direct_skill_offsets = (
                    _u32_occurrences(body, assigned_skill_id)
                    if assigned_skill_id > 0
                    else ()
                )
                level_matches: list[dict[str, int]] = []
                damage_skill_matches: list[dict[str, int]] = []
                for offset in range(9, max(9, len(body) - 3)):
                    value = int.from_bytes(body[offset : offset + 4], "little")
                    if value in damage_skill_ids:
                        damage_skill_matches.append(
                            {"offset": offset, "skill_id": value}
                        )
                    skill_id = level_to_skill.get(value)
                    if skill_id is not None:
                        level_matches.append(
                            {"offset": offset, "row_id": value, "skill_id": skill_id}
                        )
                if direct_skill_offsets or level_matches or damage_skill_matches:
                    phase_rows.append(
                        {
                            "phase": phase,
                            "direct_skill_offsets": direct_skill_offsets,
                            "skill_level_matches": level_matches,
                            "damage_skill_matches": damage_skill_matches,
                        }
                    )
            rows.append(
                {
                    "timestamp_ms": event.timestamp_ms,
                    "source_id": event.source_id,
                    "job": event.job,
                    "skill_id": event.skill_id,
                    "skill_name": event.skill_name,
                    "method": event.attribution_method,
                    "layout": str(decoded_message.layout),
                    "signature": packet.payload[5:9].hex(),
                    "payload_length": len(packet.payload),
                    "record_offset": int(getattr(decoded_message, "record_offset", 0) or 0),
                    "phase_rows": phase_rows,
                }
            )
        return event

    decoder.feed = feed
    try:
        source.poll()
    finally:
        source.close()

    offset_counts: dict[str, Counter[str]] = defaultdict(Counter)
    level_match_counts: Counter[str] = Counter()
    relative_candidate_stats: dict[str, Counter[str]] = defaultdict(Counter)
    method_counts: Counter[str] = Counter()
    for row in rows:
        method_counts[row["method"]] += 1
        try:
            assigned_skill_id = int(str(row["skill_id"]))
        except ValueError:
            assigned_skill_id = -1
        key = f'{row["layout"]}|{row["signature"]}|{row["payload_length"]}'
        record_offset = int(row.get("record_offset", 0) or 0)
        for phase_row in row["phase_rows"]:
            for match in phase_row.get("damage_skill_matches", ()):
                if record_offset <= 0:
                    continue
                relative = int(match["offset"]) - record_offset
                candidate_key = (
                    f'{row["layout"]}|phase={phase_row["phase"]}|rel={relative:+d}'
                )
                relative_candidate_stats[candidate_key]["observed"] += 1
                if int(match["skill_id"]) == assigned_skill_id:
                    relative_candidate_stats[candidate_key]["assigned_match"] += 1
                else:
                    relative_candidate_stats[candidate_key]["assigned_mismatch"] += 1
                relative_candidate_stats[candidate_key][
                    f'skill:{int(match["skill_id"])}'
                ] += 1
            for offset in phase_row["direct_skill_offsets"]:
                offset_counts[key][f'direct:{phase_row["phase"]}:{offset}'] += 1
                if record_offset > 0:
                    offset_counts[key][
                        f'direct-rel:{phase_row["phase"]}:{offset - record_offset:+d}'
                    ] += 1
            for match in phase_row["skill_level_matches"]:
                if int(match["skill_id"]) == assigned_skill_id:
                    token = f'level:{phase_row["phase"]}:{match["offset"]}'
                    offset_counts[key][token] += 1
                    level_match_counts[token] += 1
                    if record_offset > 0:
                        relative = (
                            f'level-rel:{phase_row["phase"]}:'
                            f'{int(match["offset"]) - record_offset:+d}'
                        )
                        offset_counts[key][relative] += 1
                        level_match_counts[relative] += 1
    return {
        "capture": str(path),
        "event_count": len(rows),
        "method_counts": dict(method_counts.most_common()),
        "layout_skill_key_offsets": {
            key: dict(value.most_common()) for key, value in offset_counts.items()
        },
        "matching_skill_level_offsets": dict(level_match_counts.most_common()),
        "relative_damage_skill_candidates": {
            key: dict(value.most_common())
            for key, value in sorted(relative_candidate_stats.items())
        },
        "events": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit fixed skill/SkillLevel keys inside outgoing damage packets."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.capture.resolve(), args.resource_root.resolve())
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
