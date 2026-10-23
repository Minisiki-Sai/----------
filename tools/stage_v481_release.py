"""Prepare the already-tested v4.81 installer for explicit publication."""
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
release = root / 'release_v4.81'
installer = release / 'DNACT4.81.exe'
sha = hashlib.sha256(installer.read_bytes()).hexdigest()
assert sha == '745ec36a0588c45f20419bd4821e551277be7ca267e0d3df594f1121141e32e2'
notes = (release / 'RELEASE_NOTES_V4.81.txt').read_text(encoding='utf-8')
assert notes == (root / 'RELEASE_NOTES_V4.81.txt').read_text(encoding='utf-8')
archive = release / 'DNACT4.81.zip'
shutil.copy2(archive, release / 'DNACT4.81-local-test.zip')
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for name in ('DNACT4.81.exe', 'RELEASE_NOTES_V4.81.txt', '安装说明.txt'):
        z.write(release / name, name)
with zipfile.ZipFile(archive) as z:
    assert hashlib.sha256(z.read(installer.name)).hexdigest() == sha
stage = root / 'cloudbase/releases/v481-update-hosting'
relative = f'releases/v4.81/artifacts/{sha}'
destination = stage / relative
destination.mkdir(parents=True, exist_ok=True)
for name in ('DNACT4.81.exe', 'DNACT4.81.zip', 'RELEASE_NOTES_V4.81.txt'):
    shutil.copy2(release / name, destination / name)
manifest = dict(schema='dragon-nest-update-manifest-v1', version='4.81',
    display_version='4.81', file_name=installer.name,
    download_url=f'https://dragonnest-d0gy32zvzd63b6c52-1473474041.ap-shanghai.app.tcloudbase.com/updates/{relative}/{installer.name}',
    artifact_id=sha, sha256=sha, file_size=installer.stat().st_size,
    published_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    baidu_pan_url='', baidu_pan_code='', notes=notes)
for path in (stage / 'update.json', release / 'update.json'):
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print('Staged v4.81; original tested EXE hash preserved:', sha)
