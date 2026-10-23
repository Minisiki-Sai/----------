"""Fresh, sequential off/on replay; immutable physical rows are hard gates."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import gc
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from tools.audit_direct_damage_by_profession import (
    _analysis_signature, _raw_damage_invariants, _scope_reason, _strict_classification,
)

PHYSICAL = ('timestamp_ms', 'source_id', 'target_id', 'amount', 'critical', 'healing',
    'has_real_hit_evidence', 'wire_result_group_id', 'wire_damage_object_id', 'wire_entity_id',
    'wire_source_actor_id', 'wire_target_actor_id', 'wire_record_offset', 'wire_damage_layout', 'wire_critical')


def stage(events):
    physical, rows, others = [], [], []
    for e in events:
        if not isinstance(e, CombatEvent):
            others.append((type(e).__name__, asdict(e)))
            continue
        key = tuple(getattr(e, k) for k in PHYSICAL)
        physical.append(key)
        rows.append((key, e.job, e.skill_id, e.skill_name, e.attribution_method,
                     _scope_reason(e), _strict_classification(e), e.cast_instance_id))
    return dict(invariants=_raw_damage_invariants(events),
        physical_sha256=hashlib.sha256(json.dumps(physical, sort_keys=True).encode()).hexdigest(),
        noncombat_sha256=hashlib.sha256(json.dumps(others, sort_keys=True, default=str).encode()).hexdigest(),
        rows=rows,
        skills=dict(Counter(f'{r[1]}|{r[2]}|{r[3]}|{r[6]}' for r in rows
                           if r[5] == 'included_positive_direct_hit')))


def run(capture, enabled):
    last = [-1]
    def progress(n, total):
        bucket = int(n * 10 / total)
        if bucket > last[0]:
            last[0] = bucket
            print(f'{capture.stem} evidence={enabled} replay={bucket*10}%', flush=True)
    source = V576DiagnosticReplaySource(capture, DATA_DIR, {},
        progress_callback=progress, duplicate_hit_evidence=enabled)
    started = time.perf_counter()
    events = source.poll()
    result = dict(enabled=enabled, source=stage(events), stats=source.duplicate_hit_evidence_stats)
    agg = CombatAggregator()
    for e in events:
        agg.ingest(e)
    result['aggregated'] = stage(agg.snapshot(include_events=True).combat_events)
    result['seconds'] = round(time.perf_counter()-started, 3)
    source.close()
    return result


def compare(before, after):
    result = {}
    for name in ('source', 'aggregated'):
        old, new = before[name], after[name]
        for gate in ('invariants', 'physical_sha256', 'noncombat_sha256'):
            if old[gate] != new[gate]:
                raise AssertionError((name, gate, old[gate], new[gate]))
        changed = [dict(physical=dict(zip(PHYSICAL, a[0])),
                       before=a[1:], after=b[1:])
                   for a, b in zip(old['rows'], new['rows'], strict=True) if a != b]
        result[name] = dict(invariants=old['invariants'], physical_equal=True,
            noncombat_equal=True, changed_rows=changed,
            before_skills=old['skills'], after_skills=new['skills'])
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('capture', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    signature = _analysis_signature()
    before = run(args.capture, False)
    gc.collect()
    after = run(args.capture, True)
    report = dict(schema='duplicate-hit-paired-v61', capture=str(args.capture),
        capture_sha256=hashlib.file_digest(args.capture.open('rb'), 'sha256').hexdigest(),
        analysis_signature=signature, before_seconds=before['seconds'], after_seconds=after['seconds'],
        stats=after['stats'], comparison=compare(before, after))
    assert signature == _analysis_signature(), 'production inputs changed during paired run'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(dict(stats=report['stats'], changed={k:len(v['changed_rows'])
        for k,v in report['comparison'].items()}, before_seconds=report['before_seconds'],
        after_seconds=report['after_seconds']), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
