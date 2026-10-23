"""Create a credential-free development-source snapshot, not a release build."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
from datetime import datetime, timezone, timedelta

SOURCE_ROOTS = ('cloudbase', 'docs', 'dragon_nest_builder', 'frontend',
                'supabase', 'team-score-benchmarks', 'telemetry_worker', 'tests', 'tools')
EXTRA_ROOTS = ('skill_timeline_viewer', 'skill_hit_tool', 'act_hit_timeline_comparator')
PRUNE = {'__pycache__', '.git', '.pytest_cache', '.mypy_cache', '.ruff_cache',
         '.venv', 'venv', 'node_modules', 'target', 'dist', 'gen', 'build',
         '_internal', 'outputs', 'screenshots', 'reports', '.tmp', '.codex_reports',
         '_v401_render', 'logs', 'tmp_profiles'}
DENIED_SUFFIXES = {'.exe', '.dll', '.pyd', '.pyc', '.pyo', '.zip', '.7z', '.tar',
                   '.tgz', '.log', '.tmp', '.dpscap', '.pcap', '.pcapng', '.db',
                   '.sqlite', '.sqlite3', '.dmp', '.pem', '.key', '.pfx', '.p12'}
ROOT_SUFFIXES = {'.py', '.ps1', '.bat', '.cmd', '.md', '.txt', '.toml', '.spec',
                 '.yml', '.yaml', '.ini', '.cfg', '.example'}
JWT = re.compile(rb'\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b')
TEXT_SUFFIXES = {'.py', '.js', '.mjs', '.cjs', '.ts', '.vue', '.rs', '.sql', '.json',
                 '.txt', '.md', '.ps1', '.bat', '.cmd', '.toml', '.yaml', '.yml',
                 '.html', '.xml', '.ini', '.cfg', '.spec', '.example', '.lock', '.nsh'}


def walk_files(directory: Path):
    """Prune generated/large directories before descending, not afterwards."""
    pending = [directory]
    while pending:
        current = pending.pop()
        for item in sorted(current.iterdir(), key=lambda value: value.name.lower()):
            if item.is_symlink() or item.is_junction():
                continue
            if item.is_dir():
                lower = item.name.lower()
                if lower not in PRUNE and not lower.startswith(('release_', 'build_', '.tmp')):
                    pending.append(item)
            elif item.is_file():
                yield item


def allowed(relative: str, *, extra=False):
    lower = relative.lower()
    name = Path(relative).name.lower()
    if Path(relative).suffix.lower() in DENIED_SUFFIXES:
        return False
    if lower.startswith(('cloudbase/releases/', 'frontend/screenshots/')):
        return False
    if name.startswith(('.env', 'tmp_', '~$', '.v420_backend_pid', 'build_hybrid_')):
        return name.endswith('.env.example') or name == '.env.example'
    if name in {'secrets.json', 'credentials.json', 'auth.json', '配装1.json', 'supabase_score_sync.json'}:
        return False
    if extra:
        # Do not ship historic per-player audit reports from the tool folders.
        if '/' not in relative and Path(relative).suffix.lower() in {'.json', '.csv'}:
            return False
        if Path(relative).suffix.lower() not in ROOT_SUFFIXES | {'.json', '.csv', '.svg', '.ico', '.png'}:
            return False
    return True


def package(project: Path, tool_project: Path | None, output: Path, dry_run=False):
    project = project.resolve(strict=True)
    version = json.loads((project/'frontend/package.json').read_text(encoding='utf-8'))['version']
    tauri_version = json.loads((project/'frontend/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))['version']
    if version != tauri_version:
        raise RuntimeError('Frontend and Tauri versions disagree')
    snapshot_date = datetime.now(timezone(timedelta(hours=8))).date()
    build_version = version.removesuffix('.0')
    package_root = f'DNACT{build_version}-source-dev-{snapshot_date:%Y%m%d}'
    candidates: dict[str, Path] = {}
    for name in SOURCE_ROOTS:
        directory = project/name
        if directory.is_dir():
            for source in walk_files(directory):
                relative = source.relative_to(project).as_posix()
                if allowed(relative):
                    candidates[relative] = source
    for source in project.iterdir():
        if source.is_file() and not source.is_symlink() and (
            source.suffix.lower() in ROOT_SUFFIXES
            or source.name in {'.gitignore', 'update_manifest.example.json'}
        ) and allowed(source.name):
            candidates[source.name] = source
    third_party_readme = project/'third_party/README.md'
    if third_party_readme.exists():
        candidates['third_party/README.md'] = third_party_readme
    if tool_project:
        tool_project = tool_project.resolve(strict=True)
        for name in EXTRA_ROOTS:
            for source in walk_files(tool_project/name):
                relative = source.relative_to(tool_project/name).as_posix()
                if allowed(relative, extra=True):
                    candidates[f'independent_tools/{name}/{relative}'] = source
    required = ('v420_backend.py', 'frontend/src-tauri/src/main.rs',
                'frontend/pnpm-lock.yaml', 'frontend/src-tauri/Cargo.lock',
                'requirements-lock.txt', 'build_v488_tauri.bat',
                'cloudbase/postgresql/038_online/04_activate_query.sql',
                'dragon_nest_builder/data/modular/supabase_score_sync.example.json')
    missing = [name for name in required if name not in candidates]
    if missing:
        raise RuntimeError('Missing required package files: '+repr(missing))
    total_size = sum(source.stat().st_size for source in candidates.values())
    print(json.dumps({'version':version,'files':len(candidates),
        'sourceBytes':total_size,'dryRun':dry_run},ensure_ascii=False),flush=True)
    if dry_run:
        return
    # Secrets are never emitted in logs. Production configuration is omitted;
    # catch a copy of its values accidentally embedded in another text file too.
    known_secrets = []
    production = project/'dragon_nest_builder/data/modular/supabase_score_sync.json'
    if production.exists():
        config = json.loads(production.read_text(encoding='utf-8-sig'))
        for key,value in config.items():
            if re.search('token|password|api.?key|secret',key,re.I) and isinstance(value,str) and len(value)>=12:
                known_secrets.append(value.encode('utf-8'))
    output = output.resolve()
    if output.exists() or output.with_suffix(output.suffix+'.partial').exists():
        raise RuntimeError('Refusing to overwrite an existing source archive')
    output.parent.mkdir(parents=True,exist_ok=True)
    partial = output.with_suffix(output.suffix+'.partial')
    digests = {}
    sanitized = []
    with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,
                         allowZip64=True) as archive:
        for ordinal,(relative,source) in enumerate(sorted(candidates.items()),1):
            before = source.stat()
            contents = source.read_bytes()
            after = source.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise RuntimeError('Source changed during snapshot: '+relative)
            original = contents
            if source.suffix.lower() in TEXT_SUFFIXES or source.name == '.gitignore':
                for secret in known_secrets:
                    contents=contents.replace(secret,b'<SOURCE_PACKAGE_SECRET_REMOVED>')
                contents=JWT.sub(b'<SOURCE_PACKAGE_JWT_REMOVED>',contents)
            if contents!=original:
                sanitized.append(relative)
            # Raw private-key blocks must not slip into any resource.
            if re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----',contents):
                raise RuntimeError('Private key block in source candidate: '+relative)
            archive.writestr(package_root+'/'+relative,contents)
            digests[relative]=hashlib.sha256(contents).hexdigest()
            if ordinal%300==0:
                print(f'Packed {ordinal}/{len(candidates)} source files',flush=True)
        metadata={'schema':'dnact-source-snapshot-v1','date':str(snapshot_date),
            'publicVersion':version,'snapshot':'latest-development-not-release-byte-copy',
            'sourceFiles':len(candidates),'sourceBytes':total_size,'credentialFilesExcluded':True,
            'archiveOnlyRedactedFiles':sanitized,'independentToolsIncluded':bool(tool_project),
            'knownPendingSkillAudits':'Retained as-is; source packaging does not certify outstanding skills.',
            'buildLimitations':['Bundled EXE/DLL runtimes, Npcap installer and installed dependencies omitted.',
                'Rebuild/copy third_party resources before producing a Tauri installer.',
                'Some audit/test scripts require external client resources/capture fixtures (not packaged).'],
            'filesSha256':digests}
        archive.writestr(package_root+'/SOURCE_MANIFEST.json',
            json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8'))
        readme=f'''DNACT {version} 最新开发源码快照（{snapshot_date}）

这不是已发布安装包的逐字节源码备份，也不是新的发布版本。
包含当前 Python 后端/评分/解析/模拟、Vue/TypeScript 前端、Rust/Tauri 宿主、
CloudBase/Supabase 云函数、数据表、测试和构建脚本。
包含 038_online 分批迁移、自动回填脚本及其回归测试。
独立技能时间轴和 Hit 对照器源码在 independent_tools/；保留当前数据与待核状态，
未完成的技能审核没有因本次打包被宣布完成。

不包含抓包、玩家审计输出、日志、密钥、生产同步配置、node_modules、target、
构建缓存、历史安装包、第三方 EXE/DLL 运行时。若发现已知凭据/JWT 在其他文本中
被复制，仅在压缩包内替换为占位符，原工作目录未改动；具体文件见 SOURCE_MANIFEST.json。

开发入口：安装 requirements-lock.txt 与 frontend/pnpm-lock.yaml 中的依赖；
需要云端同步时参考 dragon_nest_builder/data/modular/supabase_score_sync.example.json
创建本机配置（不要提交密钥）。前端/Tauri 的版本文件为 {version}；根 README 中
部分历史说明尚为旧版，不应用它推断实际版本。

完整安装器入口是 build_v{build_version.replace('.', '')}_tauri.bat，但打包前须另行恢复
third_party/npcap/npcap-1.88.exe，并重建/复制 third_party/skill_hit_tool 的运行时。
工具源代码在 independent_tools 中；机器相关路径及外部客户端证据可能需调整。
本次未在空白机器重新构建安装器，不保证无需补充外部依赖即可直接打包。

SOURCE_MANIFEST.json 记录每个实际入包文件的 SHA-256。
打包后校验了 ZIP CRC、文件摘要、敏感目录排除规则及关键版本/迁移文件。
'''
        archive.writestr(package_root+'/SOURCE_PACKAGE_README.txt',readme.encode('utf-8'))
    # Verify the actual ZIP bytes before the completed filename is exposed.
    with zipfile.ZipFile(partial) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP CRC failed')
        for relative,digest in digests.items():
            contents=archive.read(package_root+'/'+relative)
            if hashlib.sha256(contents).hexdigest()!=digest:
                raise RuntimeError('Archive digest failed: '+relative)
            if any(secret in contents for secret in known_secrets):
                raise RuntimeError('Known configuration credential found in package: '+relative)
        for required_name in required:
            archive.getinfo(package_root+'/'+required_name)
        for name in archive.namelist():
            if name.startswith('/') or '..' in Path(name).parts:
                raise RuntimeError('Unsafe archive path')
    partial.rename(output)
    archive_hash=hashlib.sha256()
    with output.open('rb') as reader:
        for chunk in iter(lambda:reader.read(1024*1024),b''):
            archive_hash.update(chunk)
    print(json.dumps({'archive':str(output),'sourceFiles':len(candidates),
        'archiveBytes':output.stat().st_size,'sha256':archive_hash.hexdigest(),
        'redactedFiles':sanitized,'verified':True},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--project-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--tool-project-root',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    package(args.project_root,args.tool_project_root,args.output,args.dry_run)
