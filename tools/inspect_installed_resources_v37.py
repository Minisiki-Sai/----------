"""Read PAK directory entries and only selected diagnostic resources.

Never executes game code, changes installed PAKs or substitutes runtime data.
All duplicate names remain separate; archive priority is not guessed.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
CLIENT = Path('D:/DragonNest/Reborn')
OLD = Path('E:/ext0811.rar/Resource00/Resource00/resource')
OUT = ROOT / 'outputs/client_resources_v37_20260908'
NAMES = frozenset(('academic_skill_2st_adept.act', 'academic_magmawall_ex.act',
    'academic_magmawall.act', 'mechaduck.act', 'academic_magmawall.lua',
    'skilltable_character.dnt', 'skillleveltable_characteracademic.dnt',
    'monstertable_quest.dnt', 'monsterskilltable.dnt', 'monsterskilltable_transform.dnt',
    'actortable.dnt', 'filetable.dnt'))


def index(path):
    size = path.stat().st_size
    with path.open('rb') as f:
        header = f.read(272)
        assert header.startswith(b'EyedentityGames Packing File 0.1\0')
        version, count, start = struct.unpack_from('<III', header, 256)
        assert version == 11 and 0 < count < 1000000
        assert 272 <= start <= size and start + 316 * count == size
        f.seek(start)
        raw = f.read(316 * count)
    rows = []
    for i in range(count):
        r = raw[i*316:(i+1)*316]
        assert b'\0' in r[:256]
        name_bytes = r[:256].split(b'\0', 1)[0]
        # Non-ASCII auxiliary names can use a local code page. Exact selected
        # resource names are ASCII, so preserve all others reversibly rather
        # than guessing which Windows locale generated the archive.
        name = name_bytes.decode('utf-8', errors='surrogateescape').replace('\\', '/').lstrip('/')
        assert name and '..' not in name.split('/')
        first_size, length, compressed, offset, flags = struct.unpack_from('<IIIII', r, 256)
        # Some entries omit both lengths, including newer resource files.
        # Preserve unknown lengths; do not guess uncompressed lengths.
        special = first_size == length == 0
        # +256 and +264 can differ. Preserve both opaque size fields rather
        # than assuming that encryption/wrapping leaves their values equal.
        assert special or (0 < first_size < 200*1024*1024 and 0 < length < 200*1024*1024), (i, name, first_size, length, compressed, flags)
        assert 0 <= offset < start and 0 < compressed and offset + compressed <= start, (i, name, offset)
        rows.append(dict(name=name, compressed=compressed, length=length, indexed_size=first_size, flags=flags, special_metadata=special,
                         offset=offset, directory_offset=start+i*316))
    return rows, dict(archive=path.name, archive_size=size, version=version,
        entries=count, directory_offset=start, directory_sha256=sha256(raw).hexdigest())


def materialize(path, row):
    assert not row['special_metadata'], 'entry has no declared uncompressed length'
    with path.open('rb') as f:
        f.seek(row['offset'])
        compressed = f.read(row['compressed'])
    assert len(compressed) == row['compressed']
    codec = 'zlib'
    try:
        stream = zlib.decompressobj()
        raw = stream.decompress(compressed, row['length'] + 1)
        assert stream.eof and not stream.unused_data and not stream.unconsumed_tail
    except zlib.error as exc:
        # Equal compressed/raw lengths cannot prove a stored-entry codec.
        # The current game's selected bodies have opaque 16-byte prefixes.
        raise ValueError('body is not a validated zlib resource; wrapper/codec unresolved') from exc
    assert len(raw) == row['length']
    return raw, codec


def main():
    manifest, selected, errors, selected_directory = [], [], [], []
    kinds = Counter()
    for archive in sorted(CLIENT.glob('Resource[0-9][0-9].pak')):
        try:
            rows, meta = index(archive)
            manifest.append(meta)
            kinds.update(Path(r['name']).suffix.lower() for r in rows)
            for row in rows:
                if Path(row['name']).name.lower() not in NAMES:
                    continue
                selected_directory.append(dict(archive=archive.name, **row))
                try:
                    raw, codec = materialize(archive, row)
                except Exception as exc:
                    with archive.open('rb') as source:
                        source.seek(row['offset'])
                        prefix = source.read(min(32, row['compressed']))
                    errors.append(dict(archive=archive.name, entry=row['name'],
                        error=repr(exc), prefix_hex=prefix.hex(), length=row['length']))
                    continue
                # Fixed basename plus archive basename only, no archive paths
                # can choose an output location outside this diagnostic folder.
                target = OUT / 'selected' / (archive.stem + '_' + str(row['directory_offset']) + '_' + Path(row['name']).name.lower())
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                rel = row['name'].lower().removeprefix('resource/')
                previous = OLD / rel
                old_hash = sha256(previous.read_bytes()).hexdigest() if previous.is_file() else None
                selected.append(dict(archive=archive.name, **row, codec=codec,
                    sha256=sha256(raw).hexdigest(), old_sha256=old_hash,
                    same_as_extracted=old_hash == sha256(raw).hexdigest(), output=str(target)))
        except Exception as exc:
            errors.append(dict(archive=archive.name, error=repr(exc)))
    OUT.mkdir(exist_ok=True)
    result = dict(manifest=manifest, selected=selected, selected_directory=selected_directory, errors=errors, suffix_counts=dict(kinds),
        scope='Directory census and selected bytes only. No archive precedence assumed; no runtime replacement.')
    (OUT / 'index_review.json').write_text(json.dumps(result, ensure_ascii=True, indent=2), 'utf-8')
    lines = ['# 已安装客户端资源包只读检查', '',
        f"读取 {len(manifest)} 个 PAK 的目录，合计 {sum(r['entries'] for r in manifest):,} 项。",
        f"定位到 {len(selected_directory)} 个选定技能/实体/数据表文件，成功得到经长度及完整解压校验的明文 {len(selected)} 个。", '',
        '目录名称不等于已解密内容。当前选定正文有不透明封装，未通过标准zlib解压校验；没有假设具体加密算法或将密文按明文解释。',
        '没有启动、注入或修改游戏，没有读取游戏进程内存，也没有将安装目录中的资源替换到ACT工具。',
        '这次检查没有新增确认Hit；不能将检索不到关联字段当成协议没有该字段。', '',
        '读取范围是目录和选中的正文，不是全量解压；归档覆盖顺序未知时保留各自路径，不猜测覆盖关系。', '',
        '|归档|资源路径|声明原始长度|', '|---|---|---:|']
    lines.extend(f"|{r['archive']}|{r['name']}|{r['length']}|" for r in selected_directory)
    lines += ['', '完整目录哈希、条目偏移、正文前缀和逐项失败原因见同目录 index_review.json。', '']
    (OUT / 'REPORT.md').write_text('\n'.join(lines), 'utf-8')
    print(json.dumps(dict(archives=len(manifest), entries=sum(r['entries'] for r in manifest),
        selected=len(selected), selected_directory=len(selected_directory), errors=errors), ensure_ascii=False))


if __name__ == '__main__':
    main()
