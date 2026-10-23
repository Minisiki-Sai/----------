"""Seal v4.75 before v4.76 edits; never overwrite earlier snapshots."""
from pathlib import Path
import hashlib
import importlib.util
import json
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
DEST = PROJECT / 'archives/v4.75_20261011'


def main():
    assert json.loads((PROJECT/'frontend/package.json').read_text('utf8'))['version']=='4.75.0'
    assert not DEST.exists(), 'An existing sealed snapshot must not be replaced'
    spec=importlib.util.spec_from_file_location('source_snapshot',r'C:\Users\gcg59\Documents\Codex\2026-10-10\package_source.py')
    pack=importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)
    pack.DATE='2026-10-11';pack.HERE=DEST;pack.main()
    output=DEST/'DNACT4.75-published-files.zip'
    digests={}
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for source in sorted((PROJECT/'release_v4.75').iterdir()):
            if not source.is_file(): continue
            contents=source.read_bytes()
            digests[source.name]=hashlib.sha256(contents).hexdigest()
            archive.writestr('release_v4.75/'+source.name,contents)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name,digest in digests.items():
            assert hashlib.sha256(archive.read('release_v4.75/'+name)).hexdigest()==digest
    manifest=dict(version='4.75.0',sealedDate='2026-10-11',releaseFiles=digests,
                  sourceSnapshot='development-snapshot-credentials-and-generated-artifacts-excluded',
                  archives={p.relative_to(DEST).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in DEST.rglob('*.zip')})
    (DEST/'SEALED_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(sealed=str(DEST),archives=manifest['archives']),ensure_ascii=False))


if __name__=='__main__': main()
