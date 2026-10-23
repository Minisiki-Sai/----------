"""Test in-memory protocol patch plus the frozen v22 candidate modules."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from tools.audit_damage_attribution_paired import BaselineFinder, MODULES, OPTIONAL_MODULES
from tools.audit_remaining_lineage_branch0_patch import BASE, OUT, PatchedProtocolFinder


def main():
    data=json.loads((OUT/'real_cases.json').read_text(encoding='utf-8'))
    target=ROOT/'tests/data/damage_object_branch0_real_windows.json'
    # Generated fixture export: original bytes and metadata are unchanged.
    compact={'cases':[{k:c[k] for k in ('name','packets','expected')} for c in data['cases']],
             'capture_hashes':data['capture_hashes']}
    text=json.dumps(compact,ensure_ascii=False,indent=2)+'\n'
    if target.exists():assert target.read_text(encoding='utf-8')==text
    else:target.write_text(text,encoding='utf-8')
    modules={**MODULES,'dragon_nest_builder.live_dps.v576.status_decoder':'status_decoder.py'}
    # Intentionally do not absorb the concurrently added shared direction tool.
    if (BASE/'udp_direction.py').exists(): modules['dragon_nest_builder.live_dps.udp_direction']='udp_direction.py'
    before={f:hashlib.sha256((BASE/f).read_bytes()).hexdigest() for f in modules.values()}
    sys.meta_path.insert(0,BaselineFinder(BASE,modules))
    sys.meta_path.insert(0,PatchedProtocolFinder())
    import pytest
    args=sys.argv[1:] or ['tests/test_damage_object_branch0_real_windows.py','-q','-p','no:cacheprovider']
    code=pytest.main(args)
    after={f:hashlib.sha256((BASE/f).read_bytes()).hexdigest() for f in modules.values()}
    assert before==after
    (OUT/'test_run.json').write_text(json.dumps(dict(exit_code=int(code),args=args,
        candidate_sha256=before,frozen_unchanged=True,fixtures_sha256=hashlib.sha256(target.read_bytes()).hexdigest()),indent=2)+'\n',encoding='utf-8')
    raise SystemExit(code)


if __name__=='__main__':main()
