"""Run a full corpus against an immutable runtime snapshot, including mixins."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.audit_damage_attribution_paired import BaselineFinder, snapshot_modules

ENVIRONMENT_KEY = 'DN_ATTRIBUTION_AUDIT_FROZEN_RUNTIME'


def install_snapshot(config):
    directory = Path(config['directory'])
    for filename, expected in config['hashes'].items():
        if hashlib.sha256((directory / filename).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Frozen runtime changed during audit: {filename}')
    sys.meta_path.insert(0, BaselineFinder(directory, modules=config['modules']))


# Windows spawn imports this module before the worker's audit function.
# Install the same frozen finder there, not only in the parent process.
if os.environ.get(ENVIRONMENT_KEY):
    install_snapshot(json.loads(os.environ[ENVIRONMENT_KEY]))


def frozen_audit_capture(path_text, professions):
    # This function runs in fresh Windows workers. Prove actual loaded code
    # paths as well as the snapshot file hashes, rather than trusting only
    # the parent report's claimed signature.
    from tools.audit_direct_damage_by_profession import _audit_capture
    config = json.loads(os.environ[ENVIRONMENT_KEY])
    paths = {}
    for name, filename in config['modules'].items():
        module = importlib.import_module(name)
        if name.endswith('.core_decoder'):
            path = Path(module.V576DecoderCoreMixin.feed_events.__code__.co_filename)
        elif name.endswith('.status_decoder'):
            path = Path(module.V576StatusDecoderMixin._supplemental_embedded_direct_damage_events.__code__.co_filename)
        elif name.endswith('.protocol_v576'):
            path = Path(module.decode_v576_payload.__code__.co_filename)
        elif name.endswith('.damage_object_origins'):
            path = Path(module.DamageObjectOriginLedger.observe_registration.__code__.co_filename)
        elif name.endswith('.actor_state'):
            path = Path(module.V576ActorStateMixin._reject_extreme_damage_outlier.__code__.co_filename)
        elif name.endswith('.replay_normalization'):
            path = Path(module._canonicalize_live_message.__code__.co_filename)
        else:
            path = Path(module.__file__)
        expected = Path(config['directory']) / filename
        if path.resolve() != expected.resolve():
            raise RuntimeError(f'Worker loaded non-snapshot runtime: {name}: {path}')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != config['hashes'][filename]:
            raise RuntimeError(f'Worker snapshot hash changed: {filename}')
        paths[name] = {'path': str(path.resolve()), 'sha256': digest}
    report = _audit_capture(path_text, professions)
    for filename, expected in config['hashes'].items():
        if hashlib.sha256((Path(config['directory']) / filename).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Frozen runtime changed during replay: {filename}')
    report['runtime_modules'] = paths
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--runtime-dir', required=True, type=Path)
    args, remaining = parser.parse_known_args()
    directory = args.runtime_dir.resolve()
    modules = snapshot_modules(directory)
    config = {'directory': str(directory), 'modules': modules, 'hashes': {
        filename: hashlib.sha256((directory / filename).read_bytes()).hexdigest()
        for filename in modules.values()}}
    os.environ[ENVIRONMENT_KEY] = json.dumps(config)
    install_snapshot(config)
    from tools import audit_direct_damage_by_profession as audit
    original_signature = audit._analysis_signature
    def frozen_signature():
        signature = original_signature()
        files = signature['files']
        for module, filename in modules.items():
            files[module.replace('.', '/') + '.py'] = config['hashes'][filename]
        own_relative = Path(__file__).resolve().relative_to(ROOT).as_posix()
        files[own_relative] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        digest = hashlib.sha256()
        for path, sha in sorted(files.items()):
            digest.update(path.encode('utf-8') + b'\0' + sha.encode('ascii') + b'\0')
        return {'sha256': digest.hexdigest(), 'files': files, 'frozen_runtime': config}
    audit._analysis_signature = frozen_signature
    audit._audit_capture = frozen_audit_capture
    sys.argv = [str(ROOT / 'tools/audit_direct_damage_by_profession.py'), *remaining]
    return audit.main()


if __name__ == '__main__':
    raise SystemExit(main())
