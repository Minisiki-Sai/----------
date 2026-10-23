from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
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
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.v576.core_decoder import _canonical_live_skill_id  # noqa: E402
from tools.audit_direct_damage_release_gate import _classification  # noqa: E402


U32 = struct.Struct("<I")
U64 = struct.Struct("<Q")
MARKER = b"\x08\x77\x00"
THRESHOLDS = (0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.75, 1.00, 1.50, 2.50, 5.00)


@dataclass(frozen=True, slots=True)
class Row:
    packet: int
    timestamp: float
    source: int
    target: int
    uid: int
    result_group: int
    exact: bool
    skill: int
    assigned_skill: int
    assigned_method: str
    blocking: bool


def _canonical(value: object) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return int(_canonical_live_skill_id(parsed)) if parsed > 0 else 0


def _body(packet: object, decoded: object) -> bytes | None:
    payload = bytes(getattr(packet, "payload", b""))
    marker = int(getattr(decoded, "record_offset", 0) or 0)
    source = int(getattr(decoded, "source_actor_id", 0) or 0)
    damage = int(getattr(decoded, "damage", 0) or 0)
    for phase in _v576_candidate_xor_phases(payload):
        candidate = _decode_v576_body(payload, phase)
        if (
            marker >= 0
            and marker + 31 <= len(candidate)
            and candidate[marker : marker + 3] == MARKER
            and U32.unpack_from(candidate, marker + 8)[0] == source
            and U64.unpack_from(candidate, marker + 23)[0] == damage
        ):
            return candidate
    return None


def _capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed
    rows: list[Row] = []
    packet = 0

    def feed(raw_packet: object, packet_features: int | None = None):
        nonlocal packet
        packet += 1
        event = original(raw_packet, packet_features)
        decoded = decoder.last_decoded_message
        if decoded is None or not isinstance(event, CombatEvent) or event.healing:
            return event
        layout = str(getattr(decoded, "layout", ""))
        if "direct41" not in layout:
            return event
        decoded_body = _body(raw_packet, decoded)
        if decoded_body is None:
            return event
        marker = int(getattr(decoded, "record_offset", 0) or 0)
        wire_skill = _canonical(getattr(decoded, "skill_id", 0))
        assigned = _canonical(event.skill_id)
        exact = bool(
            getattr(decoded, "skill_id_exact", False)
            and wire_skill > 0
            and assigned == wire_skill
        )
        rows.append(
            Row(
                packet=packet,
                timestamp=float(getattr(raw_packet, "timestamp_seconds", 0.0)),
                source=int(getattr(decoded, "source_actor_id", 0) or 0),
                target=int(getattr(decoded, "target_actor_id", 0) or 0),
                uid=U64.unpack_from(decoded_body, marker + 13)[0],
                result_group=U32.unpack_from(decoded_body, marker + 4)[0],
                exact=exact,
                skill=wire_skill if exact else 0,
                assigned_skill=assigned,
                assigned_method=str(event.attribution_method or "unresolved"),
                blocking=_classification(event) == "blocking_unproven_direct_hit",
            )
        )
        return event

    decoder.feed = feed
    try:
        source.poll()
    finally:
        source.close()

    rows.sort(key=lambda item: (item.timestamp, item.packet))
    key_specs = {
        "source_target_uid": lambda row: (row.source, row.target, row.uid),
        "source_uid": lambda row: (row.source, row.uid),
        "source_target": lambda row: (row.source, row.target),
        "source_target_group": lambda row: (row.source, row.target, row.result_group),
        "source_uid_group": lambda row: (row.source, row.uid, row.result_group),
    }
    results: dict[str, Any] = {}
    for name, make_key in key_specs.items():
        for threshold in THRESHOLDS:
            latest: dict[tuple[int, ...], Row] = {}
            counts: Counter[str] = Counter()
            skills: Counter[int] = Counter()
            corrections: Counter[str] = Counter()
            examples: list[dict[str, Any]] = []
            for row in rows:
                key = make_key(row)
                previous = latest.get(key)
                delta = row.timestamp - previous.timestamp if previous else 1e30
                candidate = previous.skill if previous and delta >= 0 and delta <= threshold else 0
                if row.exact:
                    if candidate:
                        counts["validation"] += 1
                        if candidate == row.skill:
                            counts["support"] += 1
                        else:
                            counts["conflict"] += 1
                            if len(examples) < 6:
                                examples.append(
                                    {
                                        "timestamp": row.timestamp,
                                        "delta": delta,
                                        "previous": candidate,
                                        "truth": row.skill,
                                        "source": f"0x{row.source:08X}",
                                        "target": f"0x{row.target:08X}",
                                        "uid": f"0x{row.uid:016X}",
                                        "group": row.result_group,
                                    }
                                )
                    latest[key] = row
                    continue
                if not candidate:
                    continue
                counts["recoverable"] += 1
                counts["recoverable_blocking"] += int(row.blocking)
                skills[candidate] += 1
                if row.assigned_skill == candidate:
                    counts["old_match"] += 1
                elif row.assigned_skill > 0:
                    counts["old_different"] += 1
                    corrections[f"{row.assigned_skill}->{candidate}"] += 1
                else:
                    counts["old_unresolved"] += 1
            results[f"{name}|{threshold:.2f}"] = {
                **{key: int(value) for key, value in counts.items()},
                "skills": {str(key): value for key, value in skills.most_common()},
                "corrections": dict(corrections.most_common()),
                "conflicts": examples,
            }
    return {
        "capture": path.name,
        "path": str(path),
        "rows": len(rows),
        "exact": sum(row.exact for row in rows),
        "nonexact": sum(not row.exact for row in rows),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    report = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    paths = [row["capture_path"] for row in report["capture_manifest"]]
    captures: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        pending = {pool.submit(_capture, path): path for path in paths}
        for future in as_completed(pending):
            captures.append(future.result())
            print(f"done {Path(pending[future]).name}", flush=True)
    captures.sort(key=lambda row: row["capture"])

    aggregate: dict[str, Counter[str]] = defaultdict(Counter)
    skills: dict[str, Counter[str]] = defaultdict(Counter)
    corrections: dict[str, Counter[str]] = defaultdict(Counter)
    conflicts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for capture in captures:
        for key, row in capture["results"].items():
            for field in (
                "validation", "support", "conflict", "recoverable",
                "recoverable_blocking", "old_match", "old_different", "old_unresolved",
            ):
                aggregate[key][field] += int(row.get(field, 0))
            skills[key].update(row.get("skills", {}))
            corrections[key].update(row.get("corrections", {}))
            if len(conflicts[key]) < 12:
                conflicts[key].extend(row.get("conflicts", [])[: 12 - len(conflicts[key])])
    ranked = []
    for key, counts in aggregate.items():
        ranked.append(
            {
                "candidate": key,
                **dict(counts),
                "skills": dict(skills[key].most_common()),
                "corrections": dict(corrections[key].most_common()),
                "conflicts": conflicts[key],
            }
        )
    ranked.sort(key=lambda row: (row.get("conflict", 0), -row.get("recoverable_blocking", 0), -row.get("validation", 0)))
    output = {
        "policy": "A continuation state is admissible only after zero-conflict validation against later exact wire skill rows.",
        "captures": captures,
        "ranked": ranked,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(ranked[:12], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
