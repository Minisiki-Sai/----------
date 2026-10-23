"""Re-run current failing legacy tests with pre-fix functions in memory only."""
import ast
import inspect
import json
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.v576 import actor_state


def swap_code(function, source):
    namespace = dict(function.__globals__)
    exec(compile(source, '<pre-1013-fix-diagnostic>', 'exec'), namespace)
    function.__code__ = namespace[function.__name__].__code__


def main():
    source = inspect.getsource(protocol.decode_v576_payload)
    source = source.replace('    action_layout = action_layouts.get(payload_length)', '''    action_layouts.update({
        211: (b"\\x6e\\x2c\\xd0\\x00", 5, 14, 64, "s2c-udp-action-211-v576"),
        214: (b"\\xe5\\xa7\\xd3\\x00", 3, 14, 64, "s2c-udp-action-214-v576"),
    })
    action_layout = action_layouts.get(payload_length)''')
    swap_code(protocol.decode_v576_payload, source)
    cls = actor_state.V576ActorStateMixin
    source = textwrap.dedent(inspect.getsource(cls._update_actor_job))
    source = source.replace('else:\n            # A confirmed first/base-job branch',
                            'elif len(current_profiles) == 1:\n            # A confirmed first/base-job branch')
    source = source.replace('resolved_profiles = set(current_profiles)\n',
                            'resolved_profiles = set(current_profiles)\n        else:\n            resolved_profiles = profile_ids\n')
    swap_code(cls._update_actor_job, source)
    source = textwrap.dedent(inspect.getsource(cls._action_conflicts_with_confirmed_job))
    source = source.replace('        actor_profiles\n', '        len(actor_profiles) == 1\n')
    swap_code(cls._action_conflicts_with_confirmed_job, source)
    failed = json.loads((ROOT / '.pytest_cache/v/cache/lastfailed').read_text())
    defined = {node.name for node in ast.parse((ROOT / 'tests/test_live_dps.py').read_text(encoding='utf8')).body
               if isinstance(node, ast.FunctionDef)}
    tests = [name for name in failed if name.startswith('tests/test_live_dps.py::')
             and name.split('::')[1].split('[')[0] in defined]
    print('PRE_FIX_TEST_COUNT', len(tests), flush=True)
    import pytest
    return pytest.main([*tests, '-q', '--tb=line', '-p', 'no:cacheprovider',
                        '--junitxml=work/1013_identity_legacy_failures.xml'])


if __name__ == '__main__':
    raise SystemExit(main())
