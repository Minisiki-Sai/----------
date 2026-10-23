from __future__ import annotations

import argparse
import multiprocessing


def main() -> None:
    # In a frozen Windows build this must run before importing the backend.
    # Otherwise every ProcessPool worker imports the complete HTTP backend
    # first and creates a misleading backend_*.log before freeze_support()
    # redirects it to the worker entry point.
    multiprocessing.freeze_support()
    from v420_backend import DEFAULT_PORT, HOST, VERSION, serve

    parser = argparse.ArgumentParser(description=f"Dragon Nest ACT V{VERSION} engine")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument(
        "--verify-runtime",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.verify_runtime:
        from dragon_nest_builder.live_dps.score_hit_evidence import repository, evidence_version, decode_version
        from dragon_nest_builder.live_dps.score_v2 import rule_version
        if rule_version({'scoreRuleVersion': 'v1'}) != 'v2':
            raise RuntimeError('Legacy scoring is still enabled')
        repository()
        # Exercise the comparator path too: its frozen module lives outside
        # the bundled addon data tree, unlike the source checkout.
        from act_hit_timeline_comparator.app import timeline_path
        from skill_timeline_viewer.timeline_viewer import default_data_path
        from skill_hit_tool.lifecycle_completion import _client_durations
        if timeline_path() != default_data_path():
            raise RuntimeError('Timeline consumers resolved different resources')
        _client_durations()
        evidence_version()
        decode_version()
        from dragon_nest_builder.live_dps.theory_planner import _load_live_page_type
        from dragon_nest_builder.live_dps.v410_score_sync import _bundled_defaults
        from v420_backend import BACKEND_API_CAPABILITIES, BACKEND_API_REVISION

        live_page_type = _load_live_page_type()
        required = (
            "_build_team_score_theory_plans",
            "_team_score_support_replan_metadata",
            "_team_score_cdr_cast_schedule",
        )
        if not all(hasattr(live_page_type, name) for name in required):
            raise RuntimeError(f"The frozen V{VERSION} scoring planner is incomplete")
        score_sync = _bundled_defaults()
        if score_sync.get("enabled") is not True:
            raise RuntimeError("Anonymous team-score upload is not enabled by default")
        if not str(score_sync.get("endpoint", "")).strip():
            raise RuntimeError("The bundled Supabase endpoint is missing")
        if len(str(score_sync.get("upload_token", "")).strip()) < 32:
            raise RuntimeError("The bundled team-score upload token is missing")
        required_api = {
            "character-score-history",
            "profession-score-history",
            "profession-score-history-detail",
            "score-history-cursor-v2",
        }
        if not required_api.issubset(BACKEND_API_CAPABILITIES):
            raise RuntimeError(f"The frozen V{VERSION} score-history API is incomplete")
        if not str(BACKEND_API_REVISION).strip():
            raise RuntimeError(f"The frozen V{VERSION} API revision is missing")
        from dragon_nest_builder.live_dps.score_equipment_defaults import (
            automatic_s_extra, standard_score_talismans,
        )
        from dragon_nest_builder.v410_native_core import NativeSimulatorCore

        if not all(callable(value) for value in (automatic_s_extra, standard_score_talismans)):
            raise RuntimeError("The frozen scoring equipment defaults are missing")
        if not all(hasattr(NativeSimulatorCore, name) for name in ("score_equipment", "score_equipment_batch")):
            raise RuntimeError("The frozen scoring equipment API is incomplete")
        # A frozen Windows engine can import every scoring module yet still
        # fail when ProcessPoolExecutor starts its spawned children.  Verify
        # the exact production initializer here so a package can never ship
        # with a UI that says 4/8 while silently recalculating on one process.
        from dragon_nest_builder.timeline_parallel import (
            _timeline_worker_warmup,
            formal_score_worker_budget,
            reset_timeline_process_pool,
            timeline_process_pool,
        )

        try:
            with formal_score_worker_budget(4):
                pool = timeline_process_pool(4)
                if pool is None:
                    raise RuntimeError("The frozen scoring process pool is disabled")
                futures = [
                    pool.submit(_timeline_worker_warmup, 0.5)
                    for _index in range(4)
                ]
                worker_pids = {
                    future.result(timeout=120) for future in futures
                }
                if len(worker_pids) < 4:
                    raise RuntimeError(
                        "The frozen scoring process pool did not start four workers"
                    )
        finally:
            reset_timeline_process_pool()
        return
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
