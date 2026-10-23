"""One-process, six-mini-fixture validation; no real capture/full replay."""
from __future__ import annotations
import hashlib,json,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from tools.audit_damage_attribution_paired import BaselineFinder,snapshot_modules
from tools.audit_remaining_registration_ffff_patch import VirtualProtocolFinder,OUT,EXPECTED
BASE=ROOT/'outputs/damage_attribution_candidate_v22_20260907'

class Results:
    def __init__(self):self.rows=[]
    def pytest_runtest_logreport(self,report):
        if report.when=='call' or report.failed:
            self.rows.append(dict(test=report.nodeid,outcome=report.outcome,seconds=report.duration,
                             failure=str(report.longrepr) if report.failed else ''))

def main():
    hashes={name:hashlib.sha256((BASE/file).read_bytes()).hexdigest() for name,file in snapshot_modules(BASE).items()}
    sys.meta_path.insert(0,BaselineFinder(BASE));sys.meta_path.insert(0,VirtualProtocolFinder())
    import pytest
    results=Results()
    code=pytest.main([str(ROOT/'tests/test_damage_object_captured_ffff_parent_chain.py'),'-q','--tb=short','-p','no:cacheprovider'],plugins=[results])
    result=dict(exit_code=int(code),counts=dict(Counter(r['outcome'] for r in results.rows)),results=results.rows,
        runtime_hashes=hashes,virtual_protocol_base_sha256=EXPECTED,
        frozen_files_unchanged=all(hashlib.sha256((BASE/file).read_bytes()).hexdigest()==hashes[name] for name,file in snapshot_modules(BASE).items()),
        fixture_sha256=hashlib.sha256((ROOT/'tests/data/damage_object_captured_ffff_parent_chain.json').read_bytes()).hexdigest(),
        scope='Six four-packet production-source positives, omitted real root/parent and actual wrong-root negatives; structural mutation tests. No mocks, scoring, full capture replay, runtime writes.',
        failure_interpretation='3 rootless FFFF cases require NEW scoped ledger guard. Not a claim that existing non-FFFF rootless exact-parent policy is incorrect.')
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('results','runtime_hashes')},ensure_ascii=False))
    return int(code)

if __name__=='__main__':raise SystemExit(main())
