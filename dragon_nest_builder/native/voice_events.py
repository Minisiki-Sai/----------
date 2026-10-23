"""Conservative recipient extraction for packet-backed claw announcements."""
from typing import Any

CLAW_SKILLS = frozenset({34849, 734017, 735017})
SEA_BREATH_SKILLS = frozenset({325430, 325431})
SEA_MECHANIC_SKILLS = frozenset({325450, 325447, 50318, 50317, 50311, 50313})


def is_sea_mechanic_event(event: Any) -> bool:
    if event.skill_index in SEA_MECHANIC_SKILLS | {325423, 50411, 50460, 50408, 50409, 50407, 50456, 50457, 50458, 50459, 325456}:
        # Jump warnings and release-anchored timers cannot use late hit/status evidence.
        kinds = set(event.evidence_types)
        return bool(kinds.intersection({"protocol_action", "structured_action"})) and not kinds.intersection({"hit", "status", "status_clear"})
    return False


def is_sea_breath_start(event: Any) -> bool:
    # Directional safety instructions must not be inferred from a late damage
    # or status packet, nor from an unverified packet-local action code.
    kinds = set(event.evidence_types)
    return (event.skill_index in SEA_BREATH_SKILLS
            and bool(kinds.intersection({"protocol_action", "structured_action"}))
            and not kinds.intersection({"hit", "status", "status_clear"}))


def claw_recipients(event: Any, players: dict) -> list[dict[str, str]]:
    if event.skill_index not in CLAW_SKILLS or "status" not in event.evidence_types:
        return []
    result = []
    for target in dict.fromkeys(event.affected_target_ids):
        player = players.get(target)
        job = str(getattr(player, "job", "") or "").strip()
        if not job or job in {"未知", "未知职业"} or job.startswith("Boss"):
            continue
        result.append({"id": target, "job": job})
    return result
