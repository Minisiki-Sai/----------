from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pickle
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.editor import BuildEditor
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.ui.live_dps_page import LiveDpsPage


class _SpecsCaptured(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    parser.add_argument("job")
    parser.add_argument("--spec-cache", type=Path)
    parser.add_argument("--snapshot-cache", type=Path)
    parser.add_argument("--parallel-copies", type=int, default=0)
    parser.add_argument("--audit-timing", action="store_true")
    parser.add_argument("--timing-summary", action="store_true")
    parser.add_argument("--remove-timing", nargs=2, metavar=("PROVIDER", "SKILL"))
    parser.add_argument("--audit-cache-order", action="store_true")
    parser.add_argument("--audit-cache-growth", action="store_true")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    editor = BuildEditor(
        "single-axis-benchmark",
        store,
        calculator,
        store.defaults["build1"],
        standardize_default=True,
    )
    page = LiveDpsPage(store, timeline_editor=editor)
    spec = None
    if args.spec_cache and args.spec_cache.exists():
        with args.spec_cache.open("rb") as handle:
            cached = pickle.load(handle)
        if isinstance(cached, dict) and cached.get("job") == args.job:
            spec = cached
    if spec is None:
        if args.snapshot_cache and args.snapshot_cache.exists():
            with args.snapshot_cache.open("rb") as handle:
                snapshot = pickle.load(handle)
        else:
            events = V576DiagnosticReplaySource(
                args.record,
                Path("dragon_nest_builder/data"),
            ).poll()
            aggregator = CombatAggregator()
            for event in events:
                aggregator.ingest(event)
            snapshot = aggregator.snapshot(include_events=True)
        target_ids = page._sync_team_score_boss_selector(snapshot)
        preset = page._team_score_resolved_boss_preset_name()

        captured: list[dict] = []
        original_parallel = editor._extreme_timeline_tasks_parallel

        def capture_specs(specs, *unused_args, **unused_kwargs):
            captured.extend(copy.deepcopy(specs))
            raise _SpecsCaptured

        editor._extreme_timeline_tasks_parallel = capture_specs
        try:
            page._build_team_score_theory_plans(snapshot, target_ids, preset)
        finally:
            editor._extreme_timeline_tasks_parallel = original_parallel
        if args.audit_timing:
            from dragon_nest_builder.live_dps.theory_plan_builder import (
                _team_score_support_effect_group,
            )
            from dragon_nest_builder.live_dps.scoring import canonical_skill
            from dragon_nest_builder.live_dps.timing_ablation import (
                timing_attribute,
                without_timing_unit,
            )

            groups = {
                canonical_skill(name)
                for name in ("鸡尾酒", "精神爆发", "灼热火焰", "时间加速")
            }
            units = sorted({
                (
                    str(effect.get("provider") or "").strip(),
                    _team_score_support_effect_group(
                        editor._team_effect_skill_name(effect.get("skill"))
                    ),
                )
                for spec_row in captured
                for effect in spec_row.get("effects", ())
                if _team_score_support_effect_group(
                    editor._team_effect_skill_name(effect.get("skill"))
                ) in groups
                and any(timing_attribute(key) for key in effect.get("attrs", {}))
            })
            no_change = [
                (index, unit)
                for index, spec_row in enumerate(captured)
                for unit in units
                if without_timing_unit(
                    spec_row,
                    unit,
                    skill_name=editor._team_effect_skill_name,
                    skill_group=_team_score_support_effect_group,
                ) is None
            ]
            print(f"timing_units={units!r}|unchanged_removals={no_change!r}", flush=True)
        spec = next(
            (row for row in captured if str(row.get("job") or "") == args.job),
            None,
        )
        if spec is not None and args.spec_cache:
            args.spec_cache.parent.mkdir(parents=True, exist_ok=True)
            with args.spec_cache.open("wb") as handle:
                pickle.dump(spec, handle, protocol=pickle.HIGHEST_PROTOCOL)
    if spec is None:
        print(f"job not found: {args.job}; available={[row.get('job') for row in captured]}")
        return 2
    if args.timing_summary:
        spec = dict(spec, timing_ablation_glyphs=[])
    if args.remove_timing:
        from dragon_nest_builder.live_dps.theory_plan_builder import (
            _team_score_support_effect_group,
        )
        from dragon_nest_builder.live_dps.timing_ablation import without_timing_unit

        spec = without_timing_unit(
            spec,
            tuple(args.remove_timing),
            skill_name=editor._team_effect_skill_name,
            skill_group=_team_score_support_effect_group,
        ) or spec
    if args.audit_cache_order:
        from dragon_nest_builder.live_dps.theory_plan_builder import (
            _team_score_support_effect_group,
        )
        from dragon_nest_builder.live_dps.timing_ablation import (
            timing_attribute,
            without_timing_unit,
        )

        unit = next((
            (
                str(effect.get("provider") or "").strip(),
                _team_score_support_effect_group(
                    editor._team_effect_skill_name(effect.get("skill"))
                ),
            )
            for effect in spec.get("effects", ())
            if any(timing_attribute(key) for key in effect.get("attrs", {}))
        ), None)
        changed = (
            without_timing_unit(
                spec, unit,
                skill_name=editor._team_effect_skill_name,
                skill_group=_team_score_support_effect_group,
            )
            if unit else None
        )
        if changed is None:
            print("cache order audit: no changed timing axis")
            return 2
        fresh_editor = BuildEditor(
            "cache-order-fresh", store, calculator,
            store.defaults["build1"], standardize_default=True,
        )
        def digest(value):
            comparable = {
                key: item for key, item in value.items()
                if key not in {"_planner_settings", "planner_refinement"}
            }
            return hashlib.sha256(json.dumps(
                comparable, ensure_ascii=True, sort_keys=True, default=str
            ).encode("utf-8")).hexdigest()
        fresh = fresh_editor._extreme_timeline_result_from_spec(changed)
        editor._extreme_timeline_result_from_spec(spec)
        warmed = editor._extreme_timeline_result_from_spec(changed)
        print(
            f"cache_order_unit={unit!r}|same={digest(fresh)==digest(warmed)}|"
            f"fresh_dps={fresh.get('dps')}|warmed_dps={warmed.get('dps')}|"
            f"fresh_digest={digest(fresh)}|warmed_digest={digest(warmed)}"
        )
        if args.audit_cache_growth:
            other_units = dict.fromkeys(
                (
                    str(effect.get("provider") or "").strip(),
                    _team_score_support_effect_group(
                        editor._team_effect_skill_name(effect.get("skill"))
                    ))
                for effect in spec.get("effects", ())
                if any(timing_attribute(key) for key in effect.get("attrs", {}))
            )
            for other_unit in other_units:
                if other_unit == unit:
                    continue
                other_spec = without_timing_unit(
                    spec, other_unit,
                    skill_name=editor._team_effect_skill_name,
                    skill_group=_team_score_support_effect_group,
                )
                if other_spec is None:
                    continue
                other_started = time.perf_counter()
                other_result = editor._extreme_timeline_result_from_spec(other_spec)
                print(
                    f"other_unit={other_unit!r}|seconds={time.perf_counter()-other_started:.3f}|"
                    f"dps={other_result.get('dps')}|digest={digest(other_result)}"
                )
                break
        if os.getenv("DRAGON_NEST_BENCH_CACHE_STATS") == "1":
            print({
                name: len(value)
                for namespace in getattr(
                    editor, "_extreme_timeline_panel_cache_pool", {}
                ).values()
                for name, value in namespace.items()
                if isinstance(value, dict)
            })
        fresh_editor.deleteLater()
        return 0
    started = time.perf_counter()
    if args.parallel_copies > 0:
        from dragon_nest_builder.timeline_parallel import (
            formal_score_worker_budget,
        )

        copies = max(1, int(args.parallel_copies))
        with formal_score_worker_budget(copies):
            results = editor._extreme_timeline_tasks_parallel(
                [copy.deepcopy(spec) for _index in range(copies)]
            )
        result = results[0] if results else {}
        fallback = getattr(editor, "_extreme_parallel_last_fallback", "")
        print(f"parallel={copies}|results={len(results)}|fallback={fallback!r}")
    else:
        result = editor._extreme_timeline_result_from_spec(spec)
    elapsed = time.perf_counter() - started
    print(
        f"job={args.job}|seconds={elapsed:.6f}|dps={float(result.get('dps') or 0.0):.12f}|"
        f"window={float(result.get('window') or 0.0):.6f}|segments={len(result.get('segments') or ())}"
    )
    if os.getenv("DRAGON_NEST_BENCH_QUALITY") == "1":
        print(repr(result.get("_planner_settings", {})))
    if os.getenv("DRAGON_NEST_BENCH_DIGEST") == "1":
        comparable = {
            key: value
            for key, value in result.items()
            if key not in {"_planner_settings", "planner_refinement"}
        }
        payload = json.dumps(
            comparable, ensure_ascii=True, sort_keys=True, default=str
        ).encode("utf-8")
        print(f"result_digest={hashlib.sha256(payload).hexdigest()}")
    page.deleteLater()
    editor.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
