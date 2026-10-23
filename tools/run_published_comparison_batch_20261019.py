from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/verification/published_478_485_20261019'
CAP = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics')
CASES = [
    ('black_1011', '1011硬核bluemoon十字.dpscap'),
    ('black_0916', '0916黑硬冰.dpscap'),
    ('black_0911', '0911黑硬噬魂.dpscap'),
    ('sea_3', '1018海龙游侠第三把.dpscap'),
    ('sea_5', '1018海龙游侠第五把.dpscap'),
    ('sea_6', '1018海龙游侠第六把.dpscap'),
]
ENGINES = {
    '4.78': ROOT / 'release_v4.78/build-dist/DragonNestEngine/_internal',
    '4.85': ROOT / 'outputs/build_v485_20261019_004033/engine_dist/DragonNestEngine/_internal',
}

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):
            h.update(b)
    return h.hexdigest()

def provenance():
    result = {'cases': [{'id':key,'file':str(CAP/name),'sha256':sha(CAP/name)} for key,name in CASES], 'versions':{},
              'execution': 'At most two concurrent cases, four timeline workers per case; final two v4.85 cases overlap after v4.78 lane completes; offline; defaults from respective releases; no shared user state or cached scores.'}
    for version, engine in ENGINES.items():
        manifest_path = ROOT / ('release_v4.78/install-verification/runtime/install-manifest.json' if version=='4.78' else 'runtime/install-manifest.json')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        assert manifest['appVersion'].startswith(version), manifest['appVersion']
        checked = 0
        for row in manifest['files']:
            prefix = 'runtime/DragonNestEngine/_internal/'
            if not row['path'].startswith(prefix):
                continue
            relative = row['path'][len(prefix):]
            if not relative.startswith(('dragon_nest_builder/', 'tools/')):
                continue
            actual = sha(engine/relative)
            assert actual.lower()==row['sha256'].lower(), (version, relative, actual)
            checked += 1
        update = json.loads((ROOT/f'release_v{version}/update.json').read_text(encoding='utf-8-sig'))
        installer = ROOT / f'release_v{version}' / update['file_name']
        assert sha(installer).lower()==update['sha256'].lower()
        result['versions'][version]={'root':str(engine),'manifestVersion':manifest['appVersion'],'checkedCodeAndDataFiles':checked,
                                      'installerSha256':update['sha256'],'installManifestSha256':sha(manifest_path)}
    (OUT/'provenance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result['versions'],ensure_ascii=False),flush=True)

def lane(version):
    for key,name in CASES:
        ident=f'{key}_v{version}'
        result_path=OUT/f'{ident}.json'
        if result_path.exists() and json.loads(result_path.read_text(encoding='utf-8')).get('status')=='ok':
            print('SKIP completed '+ident,flush=True)
            continue
        env=os.environ.copy()
        env.update(AB_ENGINE_ROOT=str(ENGINES[version]),AB_STATE_ROOT=str(OUT/'state'/ident),DRAGON_NEST_TIMELINE_WORKERS='4')
        print('START '+ident,flush=True)
        started=time.monotonic()
        with (OUT/f'{ident}.log').open('w',encoding='utf-8') as log:
            proc=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'tools/compare_published_scores_20261019.py'),str(CAP/name),
                                 '--output',str(result_path),'--version',version],env=env,stdout=log,stderr=subprocess.STDOUT)
        print(f'END {ident} exit={proc.returncode} seconds={time.monotonic()-started:.1f}',flush=True)

if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    provenance()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lane, ENGINES))
