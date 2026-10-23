"""Offline identity audit; no uploads or changes to input captures."""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import pickle
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.v576.actor_state import V576ActorStateMixin


def main():
    p = argparse.ArgumentParser()
    p.add_argument('capture', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    changes, rejected = [], Counter()
    original = V576ActorStateMixin._update_actor_job
    conflict = V576ActorStateMixin._action_conflicts_with_confirmed_job

    def update(self, actor, skill):
        before = self.actor_jobs.get(actor)
        original(self, actor, skill)
        after = self.actor_jobs.get(actor)
        if before != after:
            import inspect
            changes.append(dict(actor=f'0x{actor:08X}', skill=skill,
                                catalog=self.skill_catalog.get(skill), before=before, after=after,
                                caller=inspect.currentframe().f_back.f_code.co_name))

    def check(self, actor, skill):
        result = conflict(self, actor, skill)
        if result:
            rejected[(f'0x{actor:08X}', skill, str(self.skill_catalog.get(skill)))] += 1
        return result

    V576ActorStateMixin._update_actor_job = update
    V576ActorStateMixin._action_conflicts_with_confirmed_job = check
    start = time.perf_counter()
    source = V576DiagnosticReplaySource(args.capture, ROOT / 'dragon_nest_builder/data')
    try:
        events = source.poll()
        aggregator = CombatAggregator()
        for event in events:
            aggregator.ingest(event)
        snapshot = aggregator.snapshot(include_events=True)
        report = dict(capture=str(args.capture), seconds=time.perf_counter()-start,
                      players=[asdict(row) for row in snapshot.players], changes=changes,
                      rejected=[dict(actor=k[0], skill=k[1], catalog=k[2], count=v)
                                for k,v in rejected.most_common()])
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        args.output.with_suffix('.pickle').write_bytes(pickle.dumps(events))
        print(json.dumps(dict(seconds=report['seconds'], players=[{k:r.get(k) for k in
              ('source_id','name','job','damage')} for r in report['players']],
              changes=changes[:60], rejected=report['rejected'][:25]), ensure_ascii=False), flush=True)
    finally:
        source.close()
        V576ActorStateMixin._update_actor_job = original
        V576ActorStateMixin._action_conflicts_with_confirmed_job = conflict


if __name__ == '__main__':
    main()
