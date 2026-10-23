"""Read-only CloudBase completion audit; no uploads or database mutations."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT/'outputs/verification/all_jobs_completion_20261019'
NODE = Path('C:/Users/gcg59/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
CLI = Path('C:/Users/gcg59/AppData/Local/pnpm/store/v11/links/@cloudbase/cli/3.8.5/1debc631389eb93a3f8a18251ef3aaa3493ef90f403579f14b4b812281a1d884/node_modules/@cloudbase/cli/dist/standalone/cli.js')


def query(sql):
    assert sql.lstrip().lower().startswith(('select ', 'with '))
    result = subprocess.run([str(NODE),str(CLI),'db','execute','-e',
        'dragonnest-d0gy32zvzd63b6c52','--sql',sql,'--json'],capture_output=True,encoding='utf8',timeout=90)
    if result.returncode:
        raise RuntimeError('Read-only CloudBase query failed: '+result.stdout[-1500:])
    data=json.loads(result.stdout)['data']
    rows=[]
    for raw in data.get('Rows') or []:
        vals=json.loads(raw)
        rows.append({k:json.loads(v) if isinstance(v,str) and v.startswith(('{','[')) else v
            for k,v in zip(data['Columns'],vals)})
    return rows


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--inventory',action='store_true');args=parser.parse_args()
    DEST.mkdir(parents=True,exist_ok=True)
    if args.inventory:
        sql="""select s.job,count(*) as records,min(s.id) as min_id,max(s.id) as max_id,
        count(*) filter(where jsonb_typeof(x.score_detail->'damage_overview'->'realizations')='array') as with_realizations,
        count(*) filter(where jsonb_typeof(x.score_detail->'skill_details')='array') as with_skills
        from public.team_scores s left join public.team_score_extended_details x on x.team_score_id=s.id
        group by s.job order by s.job"""
        rows=query(sql);(DEST/'inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(rows,ensure_ascii=False),flush=True)
        return
    high=int(max(int(r['max_id']) for r in json.loads((DEST/'inventory.json').read_text(encoding='utf8'))))
    ranges=[(low,low+5000) for low in range(0,min(high+1,30000),5000)]
    ranges += [(low,low+500) for low in range(30000,high+1,500)]
    for low,upper in ranges:
        path=DEST/f'cloud_{low:06d}.json'
        if path.exists():continue
        sql=f"""select s.id,s.job,s.nest,s.score_schema_version,s.recorded_at,s.output_score,
            x.score_detail->'skill_details' as skills,
            (select jsonb_agg(jsonb_build_object(
                'skill',z->'skill','valid_casts',z->'valid_casts','landed_casts',z->'landed_casts',
                'unknown_records',z->'unknown_records','total_records',z->'total_records',
                'estimated_casts',z->'estimated_casts','boss_records',z->'boss_records',
                'multiplier_boss_rate',z->'multiplier_boss_rate','multiplier_theory_rate',z->'multiplier_theory_rate',
                'hit_boss_rate',z->'hit_boss_rate','actual_weight',z->'actual_weight','expected_weight',z->'expected_weight'))
             from jsonb_array_elements(x.score_detail->'damage_overview'->'realizations') z) as realizations,
            x.score_detail->'notes' as notes
        from public.team_scores s join public.team_score_extended_details x on x.team_score_id=s.id
        where s.id>{low} and s.id<={upper} and s.id<={high}
        and jsonb_typeof(x.score_detail->'damage_overview'->'realizations')='array'
        order by s.id"""
        rows=query(sql);path.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf8')
        print(low,len(rows),flush=True)


if __name__=='__main__':main()
