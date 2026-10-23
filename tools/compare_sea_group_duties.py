"""Local fixed-axis A/B: only group duty scope changes, never uploaded."""
import argparse
import json
import pickle
import time
from dataclasses import asdict, replace
from pathlib import Path

from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine, _intersect_intervals
from dragon_nest_builder.live_dps.duty_groups import DutyGroups, build_duty_groups
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
from dragon_nest_builder.native.replay_score_service import NativeReplayScoreMixin


def compare(path, store):
    payload = pickle.loads(path.read_bytes())  # Only our own local audit artifacts.
    snapshot,plans,rows = (payload[k] for k in ('snapshot','plans','rows'))
    encounters = tuple(BossRecognizer().recognize_snapshot(snapshot))
    targets = frozenset(t for e in encounters for t in e.target_ids)
    specialty = NativeReplayScoreMixin._score_specialty_windows_from_encounters(encounters, targets)
    groups = build_duty_groups(snapshot)
    result = dict(source=payload['source'], split_seconds=sum(b-a for a,b,_ in groups.segments),
                  segments=groups.segments, players=[], encounters=[dict(name=e.name,targets=e.target_ids,
                    windows=e.phase_windows) for e in encounters])
    for row in rows:
        actor = next(p for p in snapshot.players if p.source_id == row.source_id)
        item = dict(name=row.name,job=row.job,role=row.role,source_id=row.source_id,
                    output_score=row.output_score,support_score=row.support_score,
                    old_full_score_specialty=row.specialty_score,
                    output_seconds=sum(b-a for a,b in row.output_windows),
                    skills=[asdict(s) for s in row.skill_details],
                    supports=[asdict(s) for s in row.support_details],
                    hit_realization=row.hit_realization, notes=row.notes)
        if row.role in ('T/辅助','奶/辅助'):
            for label,scope in [('before',DutyGroups()),('after',groups)]:
                engine = TeamScoreEngine(store)
                engine._duty_groups_cache = (snapshot,scope)
                start = time.perf_counter()
                if row.role == 'T/辅助':
                    taunt, shell, attack = {}, {}, {}
                    values = engine._tank_completion(snapshot,actor,row.job,row.output_windows,targets,
                        theory_plan=plans.get(row.source_id), taunt_detail=taunt,
                        shell_detail=shell, attack_down_detail=attack)
                    detail = dict(score=values[0],attack_completion=values[1],taunt_completion=values[2],
                                  shell_completion=values[3],taunt=taunt,shell=shell,attack=attack)
                else:
                    detail = asdict(engine._healing_completion(snapshot,actor,row.job,specialty or row.output_windows))
                detail['seconds'] = time.perf_counter()-start
                detail['contribution_40'] = min(100.,max(0.,detail['score']))*.4
                item[label] = detail
            print(path.stem,row.name,row.job,'duty',round(item['before']['score'],3),
                  '->',round(item['after']['score'],3),'full',row.specialty_score,flush=True)
        result['players'].append(item)
    destination = path.with_suffix('.comparison.json')
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print('SAVED',destination,flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('artifacts',nargs='+',type=Path)
    args = parser.parse_args()
    store = DataStore.load()
    for path in args.artifacts:
        compare(path,store)


if __name__=='__main__':
    main()
