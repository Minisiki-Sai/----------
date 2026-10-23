"""Read-only comparison of complete native-score benchmark bundles; no upload."""
from pathlib import Path
import argparse
import json
import pickle
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def summarize(path):
    with path.open('rb') as stream:
        data = pickle.load(stream)
    players = []
    for row in data['rows']:
        if row.job not in {'刀锋舞者', '灵魂舞者'}:
            continue
        plan = data['plans'][row.source_id]
        segments = [s for s in plan['segments'] if s.get('kind') in {'support', 'damage'}]
        openers = [i for i, s in enumerate(segments) if s.get('skill') == '九天之舞']
        pairs, orphans = [], []
        for i in openers:
            first = segments[i]
            second = segments[i + 1] if i + 1 < len(segments) else {}
            if second.get('skill') == '九天之舞2阶' and abs(first['end'] - second['start']) < 1e-6:
                pairs.append([first['start'], first['end'], second['end']])
            else:
                orphans.append(first['start'])
        players.append(dict(name=row.name, job=row.job, source_id=row.source_id,
            output_score=row.output_score, support_score=row.support_score,
            stage_one_count=len(openers), stage_two_count=sum(s.get('skill') == '九天之舞2阶' for s in segments),
            contiguous_pairs=pairs, orphan_starts=orphans,
            support=[dict(skill=d.skill_name, actual=d.actual_contribution, theory=d.theory_contribution,
                completion=100 * d.actual_contribution / d.theory_contribution if d.theory_contribution else None)
                for d in row.support_details]))
    return dict(capture=data['capture'], score_seconds=data['score_seconds'], players=players)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = dict(before=summarize(args.before), after=summarize(args.after), uploaded=False)
    for row in report['after']['players']:
        assert not row['orphan_starts'], row
        assert row['stage_one_count'] == row['stage_two_count'] == len(row['contiguous_pairs']), row
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    args.output.write_text(rendered, encoding='utf-8')
    print(rendered)
