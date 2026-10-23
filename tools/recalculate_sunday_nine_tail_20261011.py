"""Local A/B formal scoring axes from an authenticated cloud capture.

Never uploads scores. Legacy and fixed runs differ only in the Nine Tail rule.
The saved input spec is local and trusted; the .dpscap SHA-256 is verified.
"""
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.editor import BuildEditor
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.ui.live_dps_page import LiveDpsPage
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService

app = QApplication.instance() or QApplication([])
store = DataStore.load()
calculator = BuildCalculator(store)
original = ROOT / "work/sunday_20260928_cloud_original.dpscap"
assert hashlib.sha256(original.read_bytes()).hexdigest() == "e0dfa2936f5edbdb90caa1e2b6099969b7cd652a456bf40e5d2f2d1f723e64b0"
spec_path = ROOT / "work/sunday_20260928_scoring_spec.pkl"
if not spec_path.exists():
    print("Decoding verified cloud capture", flush=True)
    events = V576DiagnosticReplaySource(original, ROOT / "dragon_nest_builder/data").poll()
    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    print(json.dumps({"decoded_events": len(events), "players": len(snapshot.players)}, ensure_ascii=False), flush=True)
    editor = BuildEditor("nine-tail-audit", store, calculator, copy.deepcopy(store.defaults["build1"]), standardize_default=True)
    page = LiveDpsPage(store, timeline_editor=editor)
    captured = []
    class Captured(RuntimeError):
        pass
    def capture(specs, *args, **kwargs):
        captured.extend(copy.deepcopy(specs))
        raise Captured("Local audit captured formal scoring inputs")
    editor._extreme_timeline_tasks_parallel = capture
    targets = page._sync_team_score_boss_selector(snapshot)
    try:
        page._build_team_score_theory_plans(snapshot, targets, page._team_score_resolved_boss_preset_name())
    except Captured:
        pass
    matching = [spec for spec in captured if spec.get("job") == "时空领主"]
    assert matching, "No Time Lord formal scoring spec was captured"
    spec = matching[0]
    spec_path.write_bytes(pickle.dumps(spec, protocol=pickle.HIGHEST_PROTOCOL))
    print(json.dumps({"captured_spec_keys": list(spec), "window": spec["window"]}, ensure_ascii=False), flush=True)
    page.deleteLater()
    editor.deleteLater()
    del snapshot, aggregator, events
else:
    spec = pickle.loads(spec_path.read_bytes())

fixed_rules = copy.deepcopy(store.skill_cooldown_rules())
legacy_rules = copy.deepcopy(fixed_rules)
legacy_rules["jobs"]["时空领主"]["九尾激光"]["sources"][0]["type"] = "reset"
results = []
for label, rules in [("legacy_reset", legacy_rules), ("fixed_one_charge", fixed_rules)]:
    store.skill_cooldown_rules = lambda rules=rules: rules
    service = LegacyTimelineService(store, calculator)
    runtime = service.extreme_runtime(copy.deepcopy(store.defaults["build1"]))
    accepted = inspect.signature(runtime._extreme_team_timeline_result).parameters
    inputs = {k: copy.deepcopy(v) for k, v in spec.items() if k in accepted}
    print("Recalculating formal scoring axis: " + label, flush=True)
    started = time.perf_counter()
    result = runtime._extreme_team_timeline_result(**inputs)
    segments = result.get("segments", [])
    counts = {}
    for row in segments:
        if row.get("timeline_hidden") or row.get("kind") in {"invalid", "wait"}:
            continue
        skill = runtime._skill_burst_skill_key(row.get("skill"))
        counts[skill] = counts.get(skill, 0) + 1
    entry = {"label": label, "seconds": time.perf_counter() - started,
             "counts": counts, "window": result.get("window"), "dps": result.get("dps"),
             "segments": segments}
    results.append(entry)
    print(json.dumps({k: entry[k] for k in ("label", "seconds", "counts", "window", "dps")}, ensure_ascii=False), flush=True)
    output = ROOT / "deliverables/九尾充能修复_星期天黑龙经典重算_20261011.json"
    output.write_text(json.dumps({"capture_sha256": "e0dfa2936f5edbdb90caa1e2b6099969b7cd652a456bf40e5d2f2d1f723e64b0",
                                   "local_only": True, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
print("Saved local comparison: " + str(output), flush=True)
