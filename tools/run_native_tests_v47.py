"""Pinned joint parser/attribution tests; no client execution or live replay."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/native_wire_v47_20260909'


def main():
    prior=ROOT/'outputs/projectile_gap_v46_20260909/regression.xml'
    prior_raw=prior.read_bytes();tree=ET.fromstring(prior_raw)
    files={'/'.join(c.attrib['classname'].split('.')[:2])+'.py' for c in tree.iter('testcase')}
    files.update(str(f.relative_to(ROOT)).replace('\\','/') for pattern in
        ('test_native_*v47.py','test_native_*v48.py','test_actor_action_namespace_v47.py','test_pak_reader_v47.py')
        for f in (ROOT/'tests').glob(pattern))
    paths=[ROOT/f for f in sorted(files)]
    paths.extend((ROOT/'dragon_nest_builder/live_dps').rglob('*.py'))
    paths.extend(p for p in (ROOT/'tools').glob('*.py'))
    before={str(p):sha256(p.read_bytes()).hexdigest() for p in paths}
    command=[sys.executable,'-B','-m','pytest','-p','no:cacheprovider','-q',*sorted(files),
        '--junitxml='+str(OUT/'regression.xml')]
    OUT.mkdir(parents=True,exist_ok=True)
    process=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    (OUT/'regression.log').write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
    changed=[p for p,h in before.items() if not Path(p).is_file() or sha256(Path(p).read_bytes()).hexdigest()!=h]
    xml=ET.parse(OUT/'regression.xml').getroot()
    suites=[r.attrib for r in xml.iter('testsuite')]
    result=dict(command=command,returncode=process.returncode,test_files=sorted(files),
        previous_test_scope_sha256=sha256(prior_raw).hexdigest(),sources_sha256=before,
        changed_sources=changed,suites=suites,
        validation_scope='Synthetic negative controls, hash-pinned saved raw records and existing offline attribution tests. Not full capture/score equivalence.',
        successful=process.returncode==0 and not changed)
    (OUT/'regression.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(process.stdout[-3500:]);print(process.stderr[-1000:]);print('changed_sources',changed)
    return 0 if result['successful'] else 1


if __name__=='__main__':sys.exit(main())
