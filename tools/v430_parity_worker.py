from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def _normalise(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _normalise(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _normalise(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, (set, frozenset)):
        rows = [_normalise(item) for item in value]
        return sorted(
            rows,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return round(value, 9)
    if isinstance(value, Path):
        return str(value)
    return value


def _digest(value: Any) -> str:
    rendered = json.dumps(
        _normalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _section_report(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(key): {
            "count": len(item)
            if isinstance(item, (dict, list, tuple, set, frozenset))
            else None,
            "sha256": _digest(item),
        }
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _stable_replay(value: dict[str, Any]) -> dict[str, Any]:
    # Cache provenance is deliberately excluded. All decoded and projected
    # combat fields remain in the comparison.
    return {
        key: item
        for key, item in value.items()
        if key not in {"versionCache"}
    }


def _stable_score(value: dict[str, Any]) -> dict[str, Any]:
    # Runtime durations, process-pool timings and remote upload state are not
    # scoring results. The rows, axes, glyphs, Boss scope and planner output
    # are retained and must match byte-for-byte after normalisation.
    return {
        key: item
        for key, item in value.items()
        if key
        not in {
            "diagnostics",
            "theoryDiagnostics",
            "scoreSync",
            # This is a cache identity and deliberately includes the source
            # tree fingerprint. Moving unchanged code between v4.25 and
            # v4.30 changes it even when every scoring field is identical.
            "scoreKey",
        }
    }


def _stable_theory_plans(core: Any) -> dict[str, Any]:
    return {
        str(source_id): {
            "total": plan.get("total"),
            "raw_total": plan.get("raw_total"),
            "average": plan.get("average"),
            "dps": plan.get("dps"),
            "_score_skill_damages": plan.get("_score_skill_damages", {}),
            "_score_skill_powers": plan.get("_score_skill_powers", {}),
            "_score_skill_components": plan.get("_score_skill_components", {}),
            "_score_glyphs": plan.get("_score_glyphs", []),
            "summary": plan.get("summary", {}),
            "segments": [
                {
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "skill": row.get("skill"),
                    "kind": row.get("kind"),
                    "type": row.get("type"),
                    "raw_effective_score": row.get("raw_effective_score"),
                    "effective_score": row.get("effective_score"),
                    "score_actual_damage": row.get("_score_actual_damage"),
                    "detail": row.get("detail"),
                }
                for row in plan.get("segments", ())
                if isinstance(row, dict)
            ],
            "effect_windows": plan.get("effect_windows", []),
            "external_windows": plan.get("external_windows", []),
        }
        for source_id, plan in core.score_theory_planner._team_score_theory_plans.items()
        if isinstance(plan, dict)
    }


def _overlay_samples(
    events: list[Any],
    *,
    self_source_id: str,
    core: Any,
    aggregator_type: Any,
) -> list[dict[str, Any]]:
    extent_ms = max((int(row.timestamp_ms) for row in events), default=0)
    fractions = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
    sample_ms = sorted({round(extent_ms * fraction) for fraction in fractions})
    aggregator = aggregator_type()
    event_index = 0
    results: list[dict[str, Any]] = []
    for current_ms in sample_ms:
        while event_index < len(events) and events[event_index].timestamp_ms <= current_ms:
            aggregator.ingest(events[event_index])
            event_index += 1
        snapshot = aggregator.overlay_snapshot(current_ms)
        snapshot["selfSourceId"] = self_source_id
        state = {
            "state": "stopped",
            "status": "v4.30 parity",
            "overlaySnapshot": snapshot,
            "overlaySnapshotPublishedMonotonic": 0.0,
            "overlaySnapshotRevision": int(snapshot.get("revision") or current_ms),
            "overlayLatency": {},
        }
        core.live_capture = SimpleNamespace(overlay_status=lambda state=state: state)
        with core._overlay_response_cache_lock:
            core._overlay_response_cache_key = None
            core._overlay_response_cache = None
            core._overlay_json_cache_key = None
            core._overlay_json_cache = b""
        projected = core.buff_overlay_status()
        projected.pop("status", None)
        projected.pop("latency", None)
        results.append({"sampleMs": current_ms, "payload": projected})
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--boss-preset", default="全部 Boss")
    parser.add_argument("--skip-score", action="store_true")
    parser.add_argument("--include-profile-details", action="store_true")
    parser.add_argument("--include-score-details", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    project = args.project.resolve()
    capture = args.capture.resolve()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("DRAGON_NEST_NATIVE_NO_QT", "1")
    sys.path.insert(0, str(project))

    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.data_loader import DATA_DIR, DataStore
    from dragon_nest_builder.live_dps.aggregator import CombatAggregator
    from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
    try:
        from dragon_nest_builder.live_dps.event_platform import event_platform_for
    except ModuleNotFoundError:
        # Allows this verifier to execute against a packaged pre-platform
        # runtime for an independent before/after shadow comparison.
        event_platform_for = None
    from dragon_nest_builder.live_dps.profile import ClientProfile
    from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
    from dragon_nest_builder.v410_native_core import NativeSimulatorCore

    profile_path = DATA_DIR / "live_dps_profiles" / "v576.json"
    try:
        known_players = ClientProfile.load(profile_path).known_players
    except (OSError, ValueError, KeyError):
        known_players = {}

    source = V576DiagnosticReplaySource(capture, DATA_DIR, known_players)
    try:
        events = sorted(source.poll(), key=lambda row: row.timestamp_ms)
        decoder = getattr(source, "_decoder", None)
        actor_id = int(getattr(decoder, "current_self_actor_id", 0) or 0)
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    platform = event_platform_for(snapshot) if event_platform_for is not None else None
    if platform is not None:
        platform.assert_lossless()
    # Compare the pre-migration direct-input Boss route with the unified
    # platform route in the same interpreter.  Some legacy replay payloads
    # serialise frozenset target IDs in hash order, so comparing two separate
    # Python processes can report a false mismatch even though both routes
    # consumed the exact same events.  This same-process shadow comparison is
    # the authoritative migration check.
    direct_boss_encounters = BossRecognizer().recognize(
        target_rows=snapshot.damage_targets,
        combat_events=snapshot.combat_events,
        boss_effects=(*snapshot.boss_effects, *snapshot.boss_effect_events),
        source_ids=(player.source_id for player in snapshot.players),
    )
    platform_boss_encounters = (
        BossRecognizer().recognize_snapshot(snapshot)
        if platform is not None
        else direct_boss_encounters
    )
    boss_shadow_equal = (
        platform is None
        or _normalise(direct_boss_encounters)
        == _normalise(platform_boss_encounters)
    )
    if platform is not None and not boss_shadow_equal:
        raise AssertionError(
            "unified event platform changed Boss recognition in shadow mode"
        )
    self_source_id = f"0x{actor_id:08X}" if actor_id else ""

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    original_live_capture = core.live_capture
    try:
        core._score_sync.enqueue = lambda *_args, **_kwargs: None
        # A parity run must compare current decoding code, not an aggregator
        # restored from an older cache container that happens to share the
        # same historical fingerprint.
        core._replay_disk_cache.load = lambda *_args, **_kwargs: None
        core._replay_disk_cache.save = lambda *_args, **_kwargs: None
        core._team_score_disk_cache.load = lambda *_args, **_kwargs: None
        core._team_score_disk_cache.save = lambda *_args, **_kwargs: None
        overlays = _overlay_samples(
            events,
            self_source_id=self_source_id,
            core=core,
            aggregator_type=CombatAggregator,
        )
        core.live_capture = original_live_capture
        replay = core.load_replay(str(capture), lambda *_args: None)
        stable_replay = _stable_replay(replay)
        score: dict[str, Any] = {}
        score_rows_detailed: list[dict[str, Any]] = []
        theory_plans: dict[str, Any] = {}
        if not args.skip_score:
            score = core.score_replay(
                {
                    "sourceHash": replay["sourceHash"],
                    "sourceFile": str(capture),
                    "bossPreset": args.boss_preset,
                },
                lambda *_args: None,
            )
            score_rows_detailed = [
                dataclasses.asdict(row)
                for row in core._score_results.get(score.get("scoreKey", ""), ())
            ]
            theory_plans = _stable_theory_plans(core)
        stable_score = _stable_score(score)
    finally:
        core.live_capture = original_live_capture
        core.shutdown()

    report = {
        "captureSha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "decodedEventCount": len(events),
        "decodedEventTypes": dict(
            sorted(Counter(type(event).__name__ for event in events).items())
        ),
        "decodedEventsSha256": _digest(events),
        "snapshotSha256": _digest(snapshot),
        "snapshotFields": _section_report(dataclasses.asdict(snapshot)),
        "eventPlatform": {
            "available": platform is not None,
            "currentMs": platform.current_ms if platform is not None else None,
            "recordingEndMs": (
                platform.recording_end_ms if platform is not None else None
            ),
            "rawSnapshotSha256": (
                _digest(platform.snapshot) if platform is not None else _digest(snapshot)
            ),
            "bossRecognizerShadow": {
                "equal": boss_shadow_equal,
                "directSha256": _digest(direct_boss_encounters),
                "platformSha256": _digest(platform_boss_encounters),
            },
            "streams": {
                name: {
                    "count": len(rows),
                    "sha256": _digest(rows),
                }
                for name, rows in (
                    sorted(platform.streams.items()) if platform is not None else ()
                )
            },
        },
        "replaySha256": _digest(stable_replay),
        "replayFields": _section_report(stable_replay),
        "replaySelected": _normalise(
            {
                "boss_encounters": stable_replay.get("boss_encounters", []),
                "boss_effect_views": stable_replay.get("boss_effect_views", []),
            }
        ),
        "overlaySha256": _digest(overlays),
        "overlaySamples": [
            {
                "sampleMs": row["sampleMs"],
                "sha256": _digest(row["payload"]),
                "buffCount": len(row["payload"].get("buffs", [])),
                "debuffCount": len(row["payload"].get("debuffs", [])),
                "clearCount": len(row["payload"].get("buffClearEvents", [])),
            }
            for row in overlays
        ],
        "scoreSha256": _digest(stable_score),
        "scoreFields": _section_report(stable_score),
        "scoreDetailsSha256": _digest(score_rows_detailed),
        "theoryPlansSha256": _digest(theory_plans),
    }
    if args.include_profile_details:
        report["profileDetails"] = _normalise(
            {
                "events": [
                    event
                    for event in events
                    if type(event).__name__ in {"PlayerInspectEvent", "PartyMemberEvent"}
                ],
                "snapshot": dataclasses.asdict(snapshot).get("player_profiles", []),
                "replayProfiles": stable_replay.get("player_profiles", []),
            }
        )
    if args.include_score_details:
        report["scoreDetails"] = _normalise(stable_score)
        report["scoreRowsDetailed"] = _normalise(score_rows_detailed)
        report["theoryPlans"] = _normalise(theory_plans)
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "decodedEventCount": report["decodedEventCount"],
                    "decodedEventsSha256": report["decodedEventsSha256"],
                    "snapshotSha256": report["snapshotSha256"],
                    "replaySha256": report["replaySha256"],
                    "overlaySha256": report["overlaySha256"],
                    "scoreSha256": report["scoreSha256"],
                    "scoreDetailsSha256": report["scoreDetailsSha256"],
                    "theoryPlansSha256": report["theoryPlansSha256"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
