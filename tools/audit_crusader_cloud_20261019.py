"""Read-only retrieval of the two user-reported completion discrepancies."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = Path('C:/Users/gcg59/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
CLI = Path('C:/Users/gcg59/AppData/Local/pnpm/store/v11/links/@cloudbase/cli/3.8.5/1debc631389eb93a3f8a18251ef3aaa3493ef90f403579f14b4b812281a1d884/node_modules/@cloudbase/cli/dist/standalone/cli.js')
SQL = """select row_to_json(s) as summary, x.score_detail
from public.team_scores s
left join public.team_score_extended_details x on x.team_score_id=s.id
where job='十字军' and (
recorded_at between '2026-10-11 21:25:30+08' and '2026-10-11 21:25:49+08'
or recorded_at between '2026-10-02 16:27:20+08' and '2026-10-02 16:27:26+08') limit 10"""
result = subprocess.run([str(NODE), str(CLI), 'db', 'execute', '-e',
    'dragonnest-d0gy32zvzd63b6c52', '--sql', SQL, '--json'],
    capture_output=True, encoding='utf-8', timeout=60)
if result.returncode:
    raise RuntimeError('Read-only query failed; no database writes were attempted')
data = json.loads(result.stdout)['data']
rows = []
for raw in data.get('Rows') or []:
    values = json.loads(raw)
    rows.append({key: json.loads(value) if isinstance(value, str) and value.startswith(('{', '[')) else value
                 for key, value in zip(data['Columns'], values)})
dest = ROOT / 'outputs/verification/crusader_completion_20261019/cloud.json'
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
print('records', len(rows), 'saved', dest)
for row in rows:
    print(json.dumps(row['summary'], ensure_ascii=False))
    print('detail keys', list(row.get('score_detail') or {}))
