"""Summarize completed frozen audits without replay, scoring or cloud writes."""
from pathlib import Path
import argparse
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / 'outputs/udp_integrity_v27_20260908'
CASES = ('0808竞速最佳', '0817黑典噬魂', '0830星期天', '0831黑硬曜')


def read(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not value.get('completed'):
        raise ValueError(f'Incomplete audit: {path}')
    return value


def main(revision):
    rows, player_changes, inputs = [], [], []
    for case in CASES:
        before_path = AUDIT / (('0808' if case == CASES[0] else case) + '_before_detailed.json')
        after_path = AUDIT / (case + '_' + revision + '.json')
        a, b = read(before_path), read(after_path)
        if a['capture_sha256'] != b['capture_sha256']:
            raise ValueError('Input capture changed')
        old, new = (v['aggregate']['categories']['outgoing:amount'] for v in (a,b))
        delta = new-old
        row = {'capture': case, 'before': old, 'after': new, 'delta': delta,
               'delta_percent': 100*delta/old,
               'before_hits': a['aggregate']['categories']['outgoing:hits'],
               'after_hits': b['aggregate']['categories']['outgoing:hits'],
               'owner_refinements': b['owner_refinements'],
               'stages': {s: {'before_categories': a[s]['categories'], 'after_categories': b[s]['categories']}
                          for s in ('raw_decoder','normalized','aggregate')},
               'independent_inventory': b.get('independent_inventory')}
        for stage in row['stages'].values():
            for field in ('healing:hits','healing:amount'):
                if stage['before_categories'].get(field,0) != stage['after_categories'].get(field,0):
                    raise AssertionError('Unexpected healing change')
        for pid in sorted(set(a['aggregate']['players']) | set(b['aggregate']['players'])):
            p, q = (v['aggregate']['players'].get(pid,{}) for v in (a,b))
            pa, qa = p.get('amount',0), q.get('amount',0)
            if pa == qa and p.get('hits') == q.get('hits'):
                continue
            percent = 100*(qa-pa)/pa if pa else None
            player_changes.append({'capture':case,'player_id':pid,'job':q.get('job') or p.get('job'),
                'before':pa,'after':qa,'delta':qa-pa,'delta_percent':percent,
                'before_hits':p.get('hits',0),'after_hits':q.get('hits',0),
                'investigate_10_percent':percent is None or abs(percent)>10})
        for path, value in ((before_path,a),(after_path,b)):
            inputs.append({'summary':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                           'runtime':value['runtime'],'manifest_sha256':value['manifest_sha256'],
                           'capture_sha256':value['capture_sha256']})
        rows.append(row)
    data = {'schema':'udp-integrity-v27-comparison','revision':revision,'captures':rows,
            'player_changes':player_changes,'inputs':inputs,
            'warning':'10% is an investigation threshold, never a damage clamp; these four captures do not prove all UDP/30 professions.'}
    target = AUDIT / (revision+'_comparison.json')
    with target.open('x',encoding='utf-8') as out:
        json.dump(data,out,ensure_ascii=False,indent=2)
    table = ['| 抓包 | 修改前总伤害 | 修改后总伤害 | 差额 | 变化 | Hit数变化 |',
             '|---|---:|---:|---:|---:|---:|']
    for r in rows:
        table.append(f"| {r['capture']} | {r['before']:,} | {r['after']:,} | {r['delta']:+,} | {r['delta_percent']:+.6f}% | {r['after_hits']-r['before_hits']:+} |")
    table += ['', '| 抓包 | 玩家运行ID | 职业 | 修改前 | 修改后 | 变化 |',
              '|---|---|---|---:|---:|---:|']
    for p in player_changes:
        percent = f"{p['delta_percent']:+.6f}%" if p['delta_percent'] is not None else '新增来源，须核查'
        table.append(f"| {p['capture']} | {p['player_id']} | {p['job']} | {p['before']:,} | {p['after']:,} | {percent} |")
    with (AUDIT/(revision+'_comparison_tables.md')).open('x',encoding='utf-8') as out:
        out.write('\n'.join(table)+'\n')
    print(json.dumps({'comparison':str(target),'capture_percent':[r['delta_percent'] for r in rows],
                      'players_exceeding_10_percent':[p for p in player_changes if p['investigate_10_percent']]},ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    main(parser.parse_args().revision)
