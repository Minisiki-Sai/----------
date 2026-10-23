from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_v576_embedded_direct_damage_events,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _canonical_live_skill_id,
)


ALTERNATE_MARKERS = (
    b"\x09\x62\x00",
    b"\x09\x66\x00",
    b"\x09\x6a\x00",
    b"\x09\x6f\x00",
    b"\x09\x73\x00",
)
WINDOWS = (0.25, 0.5, 1.0, 2.5, 5.0, 14.0)
DEFAULT_CANDIDATE_LIFETIME_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class CandidateRegistration:
    marker: str
    owner_actor_id: int
    damage_object_id: int
    projectile_template_id: int
    timestamp: float
    packet_index: int
    record_offset: int


def _actor_id(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _skill_id(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _scan_candidate_registrations(
    payload: bytes,
    *,
    timestamp: float,
    packet_index: int,
) -> tuple[CandidateRegistration, ...]:
    found: list[CandidateRegistration] = []
    seen: set[tuple[int, int, int, int]] = set()
    for phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, phase)
        for marker in ALTERNATE_MARKERS:
            offset = body.find(marker, 9)
            while offset >= 0:
                if offset >= 4 and offset + 48 <= len(body):
                    owner = int.from_bytes(body[offset - 4 : offset], "little")
                    repeated_owner = int.from_bytes(
                        body[offset + 44 : offset + 48], "little"
                    )
                    zero_guard = body[offset + 3 : offset + 8]
                    object_id = int.from_bytes(
                        body[offset + 8 : offset + 12], "little"
                    )
                    template_id = int.from_bytes(
                        body[offset + 12 : offset + 16], "little"
                    )
                    identity = (owner, object_id, template_id, offset)
                    if (
                        owner > 0
                        and repeated_owner == owner
                        and zero_guard == b"\0" * 5
                        and 0 < object_id <= 0x00FFFFFF
                        and 0 < template_id <= 1_000_000
                        and identity not in seen
                    ):
                        seen.add(identity)
                        found.append(
                            CandidateRegistration(
                                marker=marker[:2].hex(" ").upper(),
                                owner_actor_id=owner,
                                damage_object_id=object_id,
                                projectile_template_id=template_id,
                                timestamp=timestamp,
                                packet_index=packet_index,
                                record_offset=offset,
                            )
                        )
                offset = body.find(marker, offset + 1)
    return tuple(found)


def _audit_one(path_text: str) -> dict[str, object]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed_events
    original_observe_action = decoder.damage_object_origins.observe_action
    damage_skill_ids = frozenset(int(value) for value in decoder.damage_skill_ids)
    actions: dict[int, list[tuple[float, int, int]]] = defaultdict(list)
    active: dict[tuple[int, int], CandidateRegistration] = {}
    rows: Counter[tuple[object, ...]] = Counter()
    packet_index = 0

    def observe_action(source_actor_id: int, skill_id: int, timestamp: float) -> None:
        canonical = int(_canonical_live_skill_id(skill_id) or skill_id)
        action_rows = actions[int(source_actor_id)]
        if not action_rows or not (
            action_rows[-1][1] == canonical
            and abs(action_rows[-1][0] - float(timestamp)) <= 0.12
        ):
            action_rows.append((float(timestamp), canonical, int(skill_id)))
        original_observe_action(source_actor_id, skill_id, timestamp)

    def feed_events(packet: object) -> Iterable[object]:
        nonlocal packet_index
        packet_index += 1
        payload = bytes(getattr(packet, "payload", b""))
        direction = packet.inferred_direction()
        timestamp = float(getattr(packet, "timestamp_seconds", 0.0))
        registrations = (
            _scan_candidate_registrations(
                payload,
                timestamp=timestamp,
                packet_index=packet_index,
            )
            if direction == "server_to_client"
            else ()
        )
        for registration in registrations:
            active[
                (registration.owner_actor_id, registration.damage_object_id)
            ] = registration

        exact_by_key: Counter[tuple[int, int, int]] = Counter()
        if direction == "server_to_client":
            for hit in decode_v576_embedded_direct_damage_events(
                payload,
                direction,
                damage_skill_ids,
            ):
                if hit.skill_id_exact and hit.skill_id is not None:
                    exact_by_key[
                        (
                            int(hit.source_actor_id or 0),
                            int(hit.damage_object_id or 0),
                            int(_canonical_live_skill_id(hit.skill_id) or hit.skill_id),
                        )
                    ] += 1

        emitted = original_feed(packet)
        for event in emitted:
            if (
                not isinstance(event, CombatEvent)
                or event.healing
                or event.has_real_hit_evidence is not True
            ):
                continue
            owner = _actor_id(event.source_id)
            object_id = int(event.wire_damage_object_id or 0)
            registration = active.get((owner, object_id))
            if registration is None:
                continue
            spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(
                registration.projectile_template_id
            )
            lifetime_seconds = (
                float(spec.lifetime_seconds)
                if spec is not None
                else DEFAULT_CANDIDATE_LIFETIME_SECONDS
            )
            registration_age = timestamp - registration.timestamp
            if registration_age < -0.02 or registration_age > lifetime_seconds:
                continue
            action_candidates = [
                action
                for action in actions.get(owner, ())
                if -0.02 <= registration.timestamp - action[0] <= 14.0
            ]
            latest_action = action_candidates[-1] if action_candidates else None
            action_delta = (
                registration.timestamp - latest_action[0]
                if latest_action is not None
                else None
            )
            action_skill = latest_action[1] if latest_action is not None else 0
            current_skill = _skill_id(event.skill_id)
            exact_skills = [
                skill_id
                for (row_owner, row_object, skill_id), count
                in exact_by_key.items()
                if row_owner == owner and row_object == object_id and count > 0
            ]
            exact_skill = exact_skills[0] if len(exact_skills) == 1 else 0
            window_bits = tuple(
                int(action_delta is not None and action_delta <= window)
                for window in WINDOWS
            )
            rows[
                (
                    registration.marker,
                    registration.projectile_template_id,
                    action_skill,
                    current_skill,
                    exact_skill,
                    str(event.attribution_method or ""),
                    *window_bits,
                )
            ] += 1
        return ()

    decoder.damage_object_origins.observe_action = observe_action
    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    return {
        "capture": str(path),
        "rows": [
            {
                "marker": key[0],
                "projectile_template_id": key[1],
                "action_skill_id": key[2],
                "current_skill_id": key[3],
                "exact_packet_skill_id": key[4],
                "current_method": key[5],
                "within_025": key[6],
                "within_050": key[7],
                "within_100": key[8],
                "within_250": key[9],
                "within_500": key[10],
                "within_1400": key[11],
                "hits": count,
            }
            for key, count in rows.items()
        ],
    }


def _capture_paths(inputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            paths.extend(path.rglob("*.dpscap"))
        elif path.suffix.casefold() == ".dpscap" and path.exists():
            paths.append(path)
    unique: dict[str, Path] = {}
    for path in paths:
        unique[str(path.resolve()).casefold()] = path.resolve()
    return sorted(unique.values(), key=lambda value: str(value).casefold())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = _capture_paths(args.captures)
    reports: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(_audit_one, str(path)): path for path in paths}
        for index, future in enumerate(as_completed(futures), start=1):
            path = futures[future]
            try:
                reports.append(future.result())
                print(f"[{index}/{len(paths)}] {path.name}", flush=True)
            except Exception as exc:  # pragma: no cover - diagnostic boundary
                failures.append({"capture": str(path), "error": repr(exc)})

    aggregate: Counter[tuple[object, ...]] = Counter()
    for report in reports:
        for row in report["rows"]:
            key = tuple(row[name] for name in (
                "marker",
                "projectile_template_id",
                "action_skill_id",
                "current_skill_id",
                "exact_packet_skill_id",
                "current_method",
                "within_025",
                "within_050",
                "within_100",
                "within_250",
                "within_500",
                "within_1400",
            ))
            aggregate[key] += int(row["hits"])
    rows = [
        {
            "marker": key[0],
            "projectile_template_id": key[1],
            "action_skill_id": key[2],
            "current_skill_id": key[3],
            "exact_packet_skill_id": key[4],
            "current_method": key[5],
            "within_025": key[6],
            "within_050": key[7],
            "within_100": key[8],
            "within_250": key[9],
            "within_500": key[10],
            "within_1400": key[11],
            "hits": count,
        }
        for key, count in aggregate.most_common()
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captures": len(reports),
                "failures": failures,
                "windows_seconds": WINDOWS,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["hits"])
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"captures": len(reports), "failures": len(failures), "rows": len(rows), "output": str(args.output)}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
