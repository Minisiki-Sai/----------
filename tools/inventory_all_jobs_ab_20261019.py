"""Read-only candidate inventory; caches select samples, never supply A/B scores."""
from __future__ import annotations
import gc,hashlib,json,pickle,struct,sys,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT=ROOT/'outputs/verification/all_jobs_published_ab_20261019'
CAP=Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics')
CACHE=CAP.parent/'replay_cache'

def main():
    OUT.mkdir(exist_ok=True)
    jobs=[x['job_name'] for x in json.loads((ROOT/'dragon_nest_builder/data/modular/tables/skills/skill_damage_tiny_jobs/index.json').read_text(encoding='utf-8'))['jobs']]
    inventory=[];errors=[]
    for mf in sorted(CACHE.glob('*/manifest.json')):
        try:
            meta=json.loads(mf.read_text(encoding='utf-8'));source=CAP/meta['sourceName']
            if not source.exists() or source.stat().st_size!=meta['sourceSize']:continue
            paths=sorted(mf.parent.glob('*.replaycache'),key=lambda p:p.stat().st_mtime,reverse=True)
            if not paths:continue
            with paths[0].open('rb') as f:
                assert f.read(6)==b'DNRC2\0'
                header=json.loads(f.read(struct.unpack('<I',f.read(4))[0]));payload=zlib.decompress(f.read())
            assert hashlib.sha256(payload).hexdigest()==header['payload_sha256']
            agg=pickle.loads(payload)
            players=[]
            for sid,p in getattr(agg,'_players',{}).items():
                if p.damage<1e6:continue
                skill_count=sum(v.damage>0 for v in p.skills.values())
                if skill_count<3:continue
                players.append(dict(id=sid,name=p.name,job=p.job,damage=p.damage,hits=p.hits,skills=skill_count,
                                    active_seconds=(p.last_ms-p.first_ms)/1000))
            row=dict(file=source.name,path=str(source),size=source.stat().st_size,
                     metadata_source=str(paths[0]),players=players,cache_capture_hash=meta['captureHash'])
            inventory.append(row)
            print(source.name,','.join(p['job'] for p in players),flush=True)
            del agg,payload;gc.collect()
        except Exception as exc:errors.append(dict(file=str(mf),error=repr(exc)))
    covered={p['job'] for r in inventory for p in r['players'] if p['active_seconds']>=120}
    report=dict(jobs=jobs,capture_count=len(list(CAP.glob('*.dpscap'))),inventory=inventory,errors=errors,
                missing_from_cached_candidates=sorted(set(jobs)-covered),
                note='Cache identity/job evidence is for candidate discovery only. Both final scores must be freshly decoded by published versions.')
    (OUT/'candidate_inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('MISSING',report['missing_from_cached_candidates']);print('ERRORS',len(errors))

if __name__=='__main__':main()
