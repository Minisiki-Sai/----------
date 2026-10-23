"""Sequential additional full-score A/B captures. Offline, no uploads."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics')
CASES = (
    ('1009', '1009竞速通关噬魂', '噬魂者'),
    ('an1004', '1004安小呆丢包01', '刀锋舞者'),
    ('sand1005', '1005bluemmon沙龙硬核', '噬魂者'),
    ('hard0801', '0801硬核第二把', '刀锋舞者'),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', choices=[r[0] for r in CASES])
    args = parser.parse_args()
    for key, capture, job in CASES:
        if args.only and args.only != key:
            continue
        prefix = 'cohort5_' + key
        before = ROOT/f'work/{prefix}_before_20261013.json'
        after = ROOT/f'work/{prefix}_after_20261013.json'
        report = after.with_name(after.stem+'_comparison.json')
        if report.exists():
            print('ALREADY_COMPLETE', report, flush=True)
            continue
        print('START', capture, job, flush=True)
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B',
            str(ROOT/'tools/bench_joint5_support_20261013.py'),
            str(CAPTURES/(capture+'.dpscap')), '--before', str(before),
            '--output', str(after), '--job', job], cwd=ROOT)
        if result.returncode:
            raise RuntimeError(f'{capture} benchmark failed: {result.returncode}')
        data = json.loads(report.read_text(encoding='utf8'))
        print('CASE_DONE', json.dumps({
            'capture':capture,'job':job,
            'before_seconds':data['before_score_seconds'],
            'after_seconds':data['after_score_seconds'],
            'optimizer_seconds':data['experiments'][0]['elapsed_seconds'],
            'players':[(p['name'],p['before_support'],p['after_support'])
                       for p in data['players'] if p['job']==job],
        },ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
