"""Stage v4.82 artifacts; publishing the live manifest is a separate step."""
import hashlib
import argparse
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--version', default='4.82', choices=['4.82','4.83','4.84','4.85','4.86','4.87','4.88'])
version = parser.parse_args().version
release = root / f'release_v{version}'
release.mkdir(exist_ok=True)
source = root / f'frontend/src-tauri/target/release/bundle/nsis/龙之谷ACT（高级战斗追踪器）工具及模拟工具_{version}.0_x64-setup.exe'
installer = release / f'DNACT{version}.exe'
assert source.is_file()
shutil.copy2(source, installer)
notes_file = f'RELEASE_NOTES_V{version}.txt'
shutil.copy2(root / notes_file, release)
notes = (release / notes_file).read_text(encoding='utf-8')
sha = hashlib.sha256(installer.read_bytes()).hexdigest()
archive = release / f'DNACT{version}.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for file in (installer, release / notes_file):
        z.write(file,file.name)
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read(installer.name)).hexdigest() == sha
stage = root / f'cloudbase/releases/v{version.replace(".","")}-update-hosting'
relative = f'releases/v{version}/artifacts/{sha}'
destination = stage / relative
destination.mkdir(parents=True,exist_ok=True)
for file in (installer,archive,release / notes_file):
    shutil.copy2(file,destination / file.name)
manifest = dict(schema='dragon-nest-update-manifest-v1',version=version,display_version=version,
    file_name=installer.name,download_url=f'https://dragonnest-d0gy32zvzd63b6c52-1473474041.ap-shanghai.app.tcloudbase.com/updates/{relative}/{installer.name}',
    artifact_id=sha,sha256=sha,file_size=installer.stat().st_size,
    published_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),baidu_pan_url='',baidu_pan_code='',notes=notes)
for path in (stage / 'update.json',release / 'update.json'):
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=False,indent=2))
