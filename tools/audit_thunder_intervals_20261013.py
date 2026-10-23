"""Offline regression measurements; no uploads or changes to saved scores."""
import argparse
import json
import pickle
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    data = pickle.loads((ROOT/'work/joint5_1004_before_20261013.pickle').read_bytes())
    engine = TeamScoreEngine(DataStore.load())
    for k, v in data['engine_config'].items():
        setattr(engine, k, v)
    context = engine._build_support_counterfactual_context(
        data['snapshot'], data['windows'], data['targets'], data['plans'])
    results = []
    for player in data['snapshot'].players:
        if player.job != '雷神':
            continue
        plan = data['plans'][player.source_id]
        details = engine._support_scores(data['snapshot'], player, player.job,
            data['windows'], theory_plan=plan, boss_target_ids=data['targets'],
            counterfactual_context=context)[2]
        for d in details:
            if d.skill_name not in {'精神爆破', '精神爆破EX', '神圣大地'}:
                continue
            row = asdict(d)
            row['source'] = player.source_id
            results.append(row)
            print(json.dumps({k:v for k,v in row.items() if 'interval' not in k}, ensure_ascii=False),flush=True)
        selected = [s for s in plan['segments'] if s.get('skill') in {'精神爆破','神圣大地'}]
        display = [s for s in plan.get('effect_windows',[]) if any(x in str(s.get('source','')) for x in ('精神爆破','神圣大地'))]
        print('SEGMENTS', json.dumps(selected[:6],ensure_ascii=False),flush=True)
        print('DISPLAY', json.dumps(display[:6],ensure_ascii=False),flush=True)
    (ROOT/f'work/thunder_intervals_{args.label}_20261013.json').write_text(
        json.dumps(results,ensure_ascii=False,indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
