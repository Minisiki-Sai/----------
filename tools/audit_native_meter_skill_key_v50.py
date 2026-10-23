"""Cross-check the client's native per-hit skill key against capture evidence.

This is an offline audit only.  It does not emit extra damage, relabel events,
or modify the production decoder.  Complete physical kind-8 records are joined
to already-admitted CombatEvents by their preserved wire identity.  Fixed
suffix values are then compared with independently deterministic object/entity
attribution, avoiding the circular proof of comparing a packet field with an
event that was named by the same packet field.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)


DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_OUTPUT = ROOT / "outputs/native_wire_v50_20260909/native_meter_skill_key.json"

# These methods are deterministic but do not independently prove the suffix
# key: they either consume that same key or are presentation aliases of it.
PACKET_DERIVED_METHODS = frozenset(
    {
        "packet_skill_id",
        "embedded_single_candidate",
        "compound_candidate_recent_cast",
        "compound_candidate_profile",
        "compound_candidate_ranked",
    }
)


def _capture_selection(path: Path) -> list[dict[str, str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "name": str(row["capture"]),
            "sha256": str(row["capture_sha256"]),
        }
        for row in value["sources"]["selected_results"]
    ]


def _find_captures(
    capture_root: Path, selection: Iterable[dict[str, str]], names: set[str]
) -> list[Path]:
    wanted = {
        row["name"]: row["sha256"]
        for row in selection
        if not names or row["name"] in names
    }
    found: dict[str, list[Path]] = defaultdict(list)
    for path in capture_root.rglob("*.dpscap"):
        if path.name in wanted:
            found[path.name].append(path)
    result: list[Path] = []
    for name, expected in wanted.items():
        matches = []
        for path in found.get(name, ()):
            if sha256(path.read_bytes()).hexdigest() == expected:
                matches.append(path)
        if len(matches) != 1:
            raise ValueError(f"expected one hash-matched capture for {name}, got {len(matches)}")
        result.append(matches[0])
    if names - set(wanted):
        raise ValueError(f"capture names absent from fixed selection: {sorted(names-set(wanted))}")
    return result


def _event_record(event: CombatEvent, packet: object):
    inventory = inspect_udp_damage_records(
        packet.payload, packet.inferred_direction()
    )
    if not inventory.complete:
        return None, inventory.rejection_reason
    family = "direct41" if "direct41" in event.wire_damage_layout else "direct37"
    rows = [
        row
        for row in inventory.damage_records
        if row.admissible_fields
        and row.family == family
        and row.record_offset == event.wire_record_offset
        and row.source_actor_id == event.wire_source_actor_id
        and row.target_actor_id == event.wire_target_actor_id
        and row.damage == event.amount
        and row.critical == bool(event.wire_critical)
    ]
    if len(rows) != 1:
        return None, f"physical_join_count:{len(rows)}"
    return rows[0], ""


def _u32_from_record(physical: object, marker_relative_offset: int) -> int:
    """Read a bounded native result field from one complete typed record.

    ``record_bytes`` starts four bytes before the type-08 marker.  This helper
    is intentionally used only by this audit so direct41's +118 fallback can
    be checked without changing the production physical inventory first.
    """
    start = 4 + int(marker_relative_offset)
    raw = physical.record_bytes
    if start < 0 or start + 4 > len(raw):
        return 0
    return int.from_bytes(raw[start : start + 4], "little")


def _native_result_fields(physical: object) -> dict[str, Any]:
    """Expose the two fields consumed by the client's meter path.

    The deserializer writes result+0x90 from direct37 +102 (direct41 +106)
    only for source-kind 1 with common_cc == 0.  It always reads the later
    result+0xA4 value at direct37 +114 (direct41 +118); the meter uses that as
    a bounded fallback on its matching result branches.
    """
    raw = physical.record_bytes
    source_kind = int.from_bytes(raw[11:12], "little", signed=True) if len(raw) > 11 else 0
    uid_branch = raw[16] if len(raw) > 16 else 0
    common_cc = raw[100] if len(raw) > 100 else -1
    primary_offset = 102 if physical.family == "direct37" else 106
    fallback_offset = 114 if physical.family == "direct37" else 118
    primary_raw = _u32_from_record(physical, primary_offset)
    fallback_raw = _u32_from_record(physical, fallback_offset)
    primary_read = source_kind == 1 and common_cc == 0
    primary_canonical = int(
        _LIVE_DAMAGE_SKILL_ALIASES.get(primary_raw, primary_raw)
    ) if primary_read else 0
    fallback_canonical = int(
        _LIVE_DAMAGE_SKILL_ALIASES.get(fallback_raw, fallback_raw)
    )
    return {
        "source_kind": source_kind,
        "uid_branch": uid_branch,
        "common_cc": common_cc,
        "primary_offset": primary_offset,
        "primary_raw": primary_raw,
        "primary_read": primary_read,
        "primary_canonical": primary_canonical,
        "fallback_offset": fallback_offset,
        "fallback_raw": fallback_raw,
        "fallback_canonical": fallback_canonical,
    }


def audit_capture(path: Path, *, include_rows: bool = False) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    catalog = frozenset(int(value) for value in decoder.damage_skill_ids)
    original = decoder.feed_events
    rows: list[dict[str, Any]] = []
    join_failures: Counter[str] = Counter()

    def feed_events(packet):
        events = original(packet)
        for event in events:
            if (
                not isinstance(event, CombatEvent)
                or event.healing
                or event.amount <= 0
                or event.wire_record_offset <= 0
                or "direct37" not in event.wire_damage_layout
                and "direct41" not in event.wire_damage_layout
            ):
                continue
            physical, reason = _event_record(event, packet)
            if physical is None:
                join_failures[reason] += 1
                continue
            slots = {int(offset): int(value) for offset, value in physical.raw_skill_slots}
            native = _native_result_fields(physical)
            valid = {offset: value for offset, value in slots.items() if value in catalog}
            try:
                assigned = int(str(event.skill_id))
            except ValueError:
                assigned = 0
            method = str(event.attribution_method or "")
            independent = bool(
                assigned > 0
                and is_deterministic_attribution(method)
                and method not in PACKET_DERIVED_METHODS
            )
            rows.append(
                {
                    "timestamp_ms": int(event.timestamp_ms),
                    "job": str(event.job),
                    "skill_id": assigned,
                    "skill_name": str(event.skill_name),
                    "method": method,
                    "independent_deterministic": independent,
                    "family": physical.family,
                    "source_actor_id": physical.source_actor_id,
                    "target_actor_id": physical.target_actor_id,
                    "damage_object_id": physical.damage_object_id,
                    "result_group_id": physical.damage_result_group_id,
                    "record_offset": physical.record_offset,
                    "record_sha256": physical.wire_record_sha256,
                    "slots": slots,
                    "valid_slots": valid,
                    "native": native,
                }
            )
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    by_family_offset: dict[str, Counter[str]] = defaultdict(Counter)
    inferred_shapes: dict[str, Counter[str]] = defaultdict(Counter)
    promotion_candidates: dict[str, Counter[str]] = defaultdict(Counter)
    native_field_stats: dict[str, Counter[str]] = defaultdict(Counter)
    native_promotions: dict[str, Counter[str]] = defaultdict(Counter)
    native_conflicts: dict[str, Counter[str]] = defaultdict(Counter)
    raw_field_correlations: dict[str, Counter[str]] = defaultdict(Counter)
    independent_rows = 0
    for row in rows:
        family = row["family"]
        assigned = row["skill_id"]
        native = row["native"]
        source_class = (
            "player" if int(row["source_actor_id"]) > 0x00FFFFFF else "entity"
        )
        primary = int(native["primary_canonical"])
        fallback = int(native["fallback_canonical"])
        primary_valid = bool(native["primary_read"] and native["primary_raw"] in catalog)
        fallback_valid = bool(native["fallback_raw"] in catalog)
        selected = primary if primary_valid else fallback if fallback_valid else 0
        native_key = f"{family}|{source_class}"
        stats = native_field_stats[native_key]
        stats["observed"] += 1
        stats["source_kind_1"] += int(native["source_kind"] == 1)
        stats["common_cc_0"] += int(native["common_cc"] == 0)
        stats["primary_read"] += int(native["primary_read"])
        stats["primary_catalogued"] += int(primary_valid)
        stats["fallback_catalogued"] += int(fallback_valid)
        stats["selected_catalogued"] += int(selected > 0)
        stats["both_catalogued"] += int(primary_valid and fallback_valid)
        stats["both_same_canonical"] += int(
            primary_valid and fallback_valid and primary == fallback
        )
        stats["both_conflict_canonical"] += int(
            primary_valid and fallback_valid and primary != fallback
        )
        stats["selected_matches_assigned"] += int(selected > 0 and selected == assigned)
        stats["selected_mismatches_assigned"] += int(
            selected > 0 and assigned > 0 and selected != assigned
        )
        if primary_valid and fallback_valid and primary != fallback:
            conflict_key = "|".join(
                (
                    family,
                    source_class,
                    str(row["job"]),
                    str(native["primary_raw"]),
                    str(primary),
                    str(native["fallback_raw"]),
                    str(fallback),
                    str(assigned),
                    str(row["method"]),
                )
            )
            native_conflicts[conflict_key]["observed"] += 1
        if assigned > 0:
            for field_name, raw_value, field_active in (
                ("primary90", int(native["primary_raw"]), bool(native["primary_read"])),
                ("fallbackA4", int(native["fallback_raw"]), True),
            ):
                if not field_active or raw_value in {0, 0xFFFFFFFF}:
                    continue
                correlation_key = "|".join(
                    (
                        family,
                        source_class,
                        field_name,
                        str(raw_value),
                        str(row["job"]),
                        str(assigned),
                        str(row["skill_name"]),
                        str(row["method"]),
                    )
                )
                correlation = raw_field_correlations[correlation_key]
                correlation["observed"] += 1
                correlation["independent_deterministic"] += int(
                    row["independent_deterministic"]
                )
                correlation["named_non_packet_derived"] += int(
                    not row["independent_deterministic"]
                    and row["method"] not in PACKET_DERIVED_METHODS
                )
        if row["independent_deterministic"]:
            independent_rows += 1
            stats["independent_observed"] += 1
            stats["independent_selected_match"] += int(selected == assigned and selected > 0)
            stats["independent_selected_mismatch"] += int(
                selected > 0 and selected != assigned
            )
            for offset, value in row["slots"].items():
                key = f"{family}|+{offset}"
                by_family_offset[key]["observed"] += 1
                by_family_offset[key]["catalogued"] += int(value in catalog)
                by_family_offset[key]["assigned_match"] += int(value == assigned)
                by_family_offset[key]["assigned_mismatch_catalogued"] += int(
                    value in catalog and value != assigned
                )
        if not row["independent_deterministic"] and row["method"] not in PACKET_DERIVED_METHODS:
            valid = row["valid_slots"]
            shape = (
                "none"
                if not valid
                else "one"
                if len(set(valid.values())) == 1
                else "conflict"
            )
            inferred_shapes[family][shape] += 1
            if assigned > 0:
                inferred_shapes[family]["named"] += 1
                inferred_shapes[family][f"named_{shape}"] += 1
                if shape == "one":
                    candidate = next(iter(set(valid.values())))
                    key = "|".join(
                        (
                            family,
                            str(row["job"]),
                            str(assigned),
                            str(row["skill_name"]),
                            str(row["method"]),
                            str(candidate),
                        )
                    )
                    promotion_candidates[key]["observed"] += 1
                    promotion_candidates[key]["assigned_match"] += int(
                        candidate == assigned
                    )
                    promotion_candidates[key]["assigned_mismatch"] += int(
                        candidate != assigned
                    )
        if (
            not row["independent_deterministic"]
            and row["method"] not in PACKET_DERIVED_METHODS
            and assigned > 0
            and selected > 0
        ):
            promotion_key = "|".join(
                (
                    str(row["job"]),
                    str(assigned),
                    str(row["skill_name"]),
                    str(row["method"]),
                    family,
                    source_class,
                    str(native["primary_raw"]),
                    str(native["fallback_raw"]),
                    str(selected),
                )
            )
            native_promotions[promotion_key]["observed"] += 1
            native_promotions[promotion_key]["assigned_match"] += int(selected == assigned)
            native_promotions[promotion_key]["assigned_mismatch"] += int(selected != assigned)

    return {
        "capture": str(path),
        "capture_sha256": sha256(path.read_bytes()).hexdigest(),
        "joined_rows": len(rows),
        "independent_deterministic_rows": independent_rows,
        "join_failures": dict(join_failures),
        "independent_slot_agreement": {
            key: dict(counter) for key, counter in sorted(by_family_offset.items())
        },
        "non_packet_derived_shapes": {
            key: dict(counter) for key, counter in sorted(inferred_shapes.items())
        },
        "promotion_candidates": {
            key: dict(counter) for key, counter in sorted(promotion_candidates.items())
        },
        "native_field_stats": {
            key: dict(counter) for key, counter in sorted(native_field_stats.items())
        },
        "native_promotions": {
            key: dict(counter) for key, counter in sorted(native_promotions.items())
        },
        "native_conflicts": {
            key: dict(counter) for key, counter in sorted(native_conflicts.items())
        },
        "raw_field_correlations": {
            key: dict(counter)
            for key, counter in sorted(raw_field_correlations.items())
        },
        **({"rows": rows} if include_rows else {}),
    }


def _aggregate(captures: list[dict[str, Any]]) -> dict[str, Any]:
    agreements: dict[str, Counter[str]] = defaultdict(Counter)
    shapes: dict[str, Counter[str]] = defaultdict(Counter)
    promotions: dict[str, Counter[str]] = defaultdict(Counter)
    failures: Counter[str] = Counter()
    native_stats: dict[str, Counter[str]] = defaultdict(Counter)
    native_promotions: dict[str, Counter[str]] = defaultdict(Counter)
    native_conflicts: dict[str, Counter[str]] = defaultdict(Counter)
    raw_correlations: dict[str, Counter[str]] = defaultdict(Counter)
    for capture in captures:
        failures.update(capture["join_failures"])
        for key, values in capture["independent_slot_agreement"].items():
            agreements[key].update(values)
        for key, values in capture["non_packet_derived_shapes"].items():
            shapes[key].update(values)
        for key, values in capture["promotion_candidates"].items():
            promotions[key].update(values)
        for key, values in capture.get("native_field_stats", {}).items():
            native_stats[key].update(values)
        for key, values in capture.get("native_promotions", {}).items():
            native_promotions[key].update(values)
        for key, values in capture.get("native_conflicts", {}).items():
            native_conflicts[key].update(values)
        for key, values in capture.get("raw_field_correlations", {}).items():
            raw_correlations[key].update(values)
    summary = {}
    for key, counter in sorted(agreements.items()):
        observed = counter["observed"]
        summary[key] = {
            **dict(counter),
            "assigned_match_percent": (
                100.0 * counter["assigned_match"] / observed if observed else 0.0
            ),
        }
    return {
        "capture_count": len(captures),
        "joined_rows": sum(row["joined_rows"] for row in captures),
        "independent_deterministic_rows": sum(
            row["independent_deterministic_rows"] for row in captures
        ),
        "join_failures": dict(failures),
        "independent_slot_agreement": summary,
        "non_packet_derived_shapes": {
            key: dict(counter) for key, counter in sorted(shapes.items())
        },
        "promotion_candidates": {
            key: dict(counter) for key, counter in sorted(promotions.items())
        },
        "native_field_stats": {
            key: dict(counter) for key, counter in sorted(native_stats.items())
        },
        "native_promotions": {
            key: dict(counter) for key, counter in sorted(native_promotions.items())
        },
        "native_conflicts": {
            key: dict(counter) for key, counter in sorted(native_conflicts.items())
        },
        "raw_field_correlations": {
            key: dict(counter) for key, counter in sorted(raw_correlations.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--capture", action="append", default=[])
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-count", type=int)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--include-rows", action="store_true")
    args = parser.parse_args()
    selected = _capture_selection(args.selection.resolve())
    if args.shard_index is not None or args.shard_count is not None:
        if (
            args.capture
            or args.shard_index is None
            or args.shard_count is None
            or args.shard_count <= 0
            or not 0 <= args.shard_index < args.shard_count
        ):
            raise ValueError("valid shard-index/shard-count require no explicit --capture")
        selected = [
            row
            for index, row in enumerate(selected)
            if index % args.shard_count == args.shard_index
        ]
    paths = _find_captures(
        args.capture_root.resolve(), selected, set(args.capture)
    )
    captures = [audit_capture(path, include_rows=args.include_rows) for path in paths]
    report = {
        "schema": "native-meter-skill-key-v50",
        "method": "offline capture replay; no client execution or production mutation",
        "selection": str(args.selection.resolve()),
        "summary": _aggregate(captures),
        "captures": captures,
        "production_changed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
