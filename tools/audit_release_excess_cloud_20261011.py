"""Read-only cross-profession audit of actual casts above uploaded theory.

Runs SELECT statements only. Saves public score facts locally; never changes
scores, captures, migrations, or cloud functions.
"""
import argparse
import json
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
NODE = Path('C:/Users/gcg59/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
CLI = Path('C:/Users/gcg59/AppData/Local/pnpm/store/v11/links/@cloudbase/cli/3.8.5/1debc631389eb93a3f8a18251ef3aaa3493ef90f403579f14b4b812281a1d884/node_modules/@cloudbase/cli/dist/standalone/cli.js')
ENV = 'dragonnest-d0gy32zvzd63b6c52'
PERIOD = "recorded_at >= '2026-09-28T00:00:00+08:00' and recorded_at < '2026-10-12T00:00:00+08:00'"


def query(sql):
    assert sql.lstrip().lower().startswith(('select ', 'with '))
    process = subprocess.run([str(NODE), str(CLI), 'db', 'execute', '-e', ENV,
                              '--sql', sql, '--json'], capture_output=True, encoding='utf-8', timeout=65)
    if process.returncode:
        raise RuntimeError('Read-only query failed; no cloud state changed: ' + process.stderr[-500:])
    response = json.loads(process.stdout)
    payload = response.get('data', {})
    if 'Columns' not in payload:
        raise RuntimeError('Read-only query returned no tabular payload: ' + str(response)[:400])
    result = []
    for raw in payload.get('Rows') or []:
        raw = json.loads(raw) if isinstance(raw, str) else raw
        row = dict(zip(payload['Columns'], raw))
        for key, value in row.items():
            if isinstance(value, str) and value.startswith(('{', '[')):
                row[key] = json.loads(value)
        result.append(row)
    return result


def save(filename, rows):
    target = ROOT / 'work' / filename
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'saved': str(target), 'rows': len(rows)}, ensure_ascii=False), flush=True)


def summary():
    coverage = query(f"select job,count(*) as score_records,count(distinct capture_hash) as captures,"
                     f"min(score_schema_version) as oldest_schema,max(score_schema_version) as newest_schema "
                     f"from public.team_scores where {PERIOD} group by job order by job")
    save('release_excess_coverage_20261011.json', coverage)
    print(json.dumps(coverage, ensure_ascii=False), flush=True)
    sql = f"""with facts as (
      select d.job,d.skill_name,d.high_value,d.excluded_from_score,d.actual_count,d.theory_count,
             d.actual_power,d.theory_power,d.capture_hash,d.team_score_id,s.score_schema_version,
             (d.theory_count>=5 and d.actual_count>=15 and d.actual_count>=d.theory_count*1.20
              and d.actual_count-d.theory_count>=5) as excess
      from public.team_score_skill_details d join public.team_scores s on s.id=d.team_score_id
      where d.{PERIOD} and d.theory_count>0
    ) select job,skill_name,count(*) as samples,count(distinct capture_hash) as captures,
      count(*) filter(where excess) as excess_samples,
      count(distinct capture_hash) filter(where excess) as excess_captures,
      count(*) filter(where high_value) as high_value_samples,
      count(*) filter(where excluded_from_score) as excluded_samples,
      sum(actual_count) as total_actual,sum(theory_count) as total_theory,
      percentile_cont(0.5) within group(order by actual_count::float/theory_count) as median_ratio,
      max(actual_count::float/theory_count) as max_ratio,
      max(actual_count-theory_count) as max_delta,
      min(score_schema_version) as oldest_schema,max(score_schema_version) as newest_schema,
      count(*) filter(where excess and score_schema_version>=20261006) as recent_schema_excess,
      count(*) filter(where score_schema_version>=20261006) as recent_schema_samples
      from facts group by job,skill_name having bool_or(excess)
      order by excess_samples desc,median_ratio desc"""
    # Prefix both timestamp predicates in the joined fact query.
    sql = sql.replace("and recorded_at <", "and d.recorded_at <")
    groups = query(sql)
    save('release_excess_groups_20261011.json', groups)
    print(json.dumps(groups[:35], ensure_ascii=False, indent=2), flush=True)


def examples():
    groups = json.loads((ROOT / 'work/release_excess_groups_20261011.json').read_text(encoding='utf-8'))
    jobs = sorted({row['job'] for row in groups})
    quoted = ','.join("'" + job.replace("'", "''") + "'" for job in jobs)
    rows = query(f"""with ranked as (
      select d.job,d.skill_name,d.actual_count,d.theory_count,d.actual_power,d.theory_power,
       d.high_value,d.excluded_from_score,d.recorded_at,d.nest,d.row_fingerprint,d.capture_hash,
       s.output_score,s.score_schema_version,
       row_number() over(partition by d.job,d.skill_name order by
          s.score_schema_version desc,d.actual_count::float/d.theory_count desc,d.recorded_at desc) as rn
      from public.team_score_skill_details d join public.team_scores s on s.id=d.team_score_id
      where d.{PERIOD} and d.job in ({quoted}) and d.theory_count>=5 and d.actual_count>=15
       and d.actual_count>=d.theory_count*1.20 and d.actual_count-d.theory_count>=5
    ) select * from ranked where rn<=3 order by job,skill_name,rn""".replace('and recorded_at <', 'and d.recorded_at <'))
    save('release_excess_examples_20261011.json', rows)
    print(json.dumps({'jobs': len(jobs), 'examples': len(rows)}, ensure_ascii=False), flush=True)


def details():
    groups = json.loads((ROOT / 'work/release_excess_groups_20261011.json').read_text(encoding='utf-8'))
    rows = json.loads((ROOT / 'work/release_excess_examples_20261011.json').read_text(encoding='utf-8'))
    repeated = {(row['job'], row['skill_name']) for row in groups
                if int(row['recent_schema_excess']) >= 3}
    chosen = [row for row in rows if (row['job'], row['skill_name']) in repeated
              and int(row['rn']) == 1 and int(row['score_schema_version']) >= 20261006]
    fingerprints = list(dict.fromkeys(row['row_fingerprint'] for row in chosen))
    assert all(len(value.split(':')[0]) == 64 and set(value.split(':')[0]) <= set('0123456789abcdef')
               for value in fingerprints)

    def batch(ids):
        quoted = ','.join("'" + value + "'" for value in ids)
        return query(f"""select s.row_fingerprint,s.job,s.nest,s.recorded_at,s.score_schema_version,
          s.output_score,x.score_detail->'skill_details' as skill_details,
          x.score_detail->'compact_timeline' as compact_timeline,
          x.score_detail->'scoring_equipment'->'build'->'skill_glyphs' as skill_glyphs,
          x.score_detail->'output_seconds' as output_seconds
          from public.team_scores s left join public.team_score_extended_details x on x.team_score_id=s.id
          where s.row_fingerprint in ({quoted})""")
    batches = [fingerprints[i:i+3] for i in range(0, len(fingerprints), 3)]
    result = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for values in pool.map(batch, batches):
            result.extend(values)
            print(json.dumps({'details_loaded': len(result), 'target': len(fingerprints)}), flush=True)
    save('release_excess_details_20261011.json', result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['summary', 'examples', 'details'])
    args = parser.parse_args()
    {'summary': summary, 'examples': examples, 'details': details}[args.phase]()
