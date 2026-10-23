"""Build a local registration-only diff; never write a candidate/runtime file."""
from __future__ import annotations
import difflib
import hashlib
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'outputs/damage_attribution_candidate_v22_20260907'
OUT = ROOT/'outputs/damage_attribution/branch0_raw_windows_20260907'
EXPECTED_BASE_SHA = '0f36b49f20e747e10edc8c83189f24c08bac10c8eaa1cd8c5ca6064fac38b9f8'
HELPER = '''def _decode_v576_server_branch0_parent_tail(
    decoded: bytes, marker_offset: int,
) -> tuple[int, int, int, str] | None:
    """Audited 2022 -> 2023 branch0 observations; not the 3021 branch1 layout.

    Three real root/parent/child/Hit windows validate 0962 in single119 and
    double224 envelopes. Do not extend this to 0966 or mixed record lengths.
    The full owner and parent ID are wire evidence, not a nearest action or
    a current-skill slot. Current generation/typed edge/root proof is still
    mandatory in the ledger. A fingerprint is not a creation/expiry token.
    """
    m = marker_offset
    if len(decoded) == 224 and m in (18, 123) and decoded[5:9] == b"\\x6e\\xcc\\xdd\\x00":
        markers = (18, 123)
    elif len(decoded) == 119 and m == 18 and decoded[5:9] == b"\\x28\\x2a\\x74\\x00":
        markers = (18,)
    else:
        return None
    # Validate every complete 105-byte record, including the sibling. No
    # following record may supply a missing suffix, owner repeat, or parent.
    for at in markers:
        owner = _UINT32_LE.unpack_from(decoded, at - 4)[0]
        obj = _UINT32_LE.unpack_from(decoded, at + 8)[0]
        parent = _UINT32_LE.unpack_from(decoded, at + 49)[0]
        branch = _UINT32_LE.unpack_from(decoded, at + 53)[0]
        suffix = _UINT32_LE.unpack_from(decoded, at + 69)[0]
        if (
            decoded[at:at + 3] != b"\\x09\\x62\\x00"
            or not 0 < owner < 0xFFFFFFFF
            or owner != _UINT32_LE.unpack_from(decoded, at + 44)[0]
            or decoded[at + 3:at + 8] != bytes(5)
            or not 0 < obj <= 0xFFFFFF
            or _UINT32_LE.unpack_from(decoded, at + 12)[0] != 2023
            or decoded[at + 48] != 0
            or not 0 < parent <= 0xFFFFFF or parent == obj
            or branch & 0xFFFF != 0 or branch >> 16 > 5
            or decoded[at + 57:at + 65] != b"\\xff" * 8
            or not 0 < suffix < 0xFFFF
        ):
            return None
    parent = _UINT32_LE.unpack_from(decoded, m + 49)[0]
    suffix = _UINT32_LE.unpack_from(decoded, m + 69)[0]
    sequence = _UINT32_LE.unpack_from(decoded, m + 53)[0] >> 16
    fingerprint = (decoded[m - 4:m] + decoded[m + 3:m + 101]).hex()
    return parent, suffix, sequence, fingerprint
'''


def build_source():
    path=BASE/'protocol_v576.py'; raw=path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==EXPECTED_BASE_SHA, 'v22 base drift; do not absorb concurrent code'
    original=raw.decode('utf-8').replace('\r\n','\n')
    marker='def _decode_v576_server_parent_tail(\n'
    assert original.count(marker)==1
    changed=original.replace(marker,HELPER+'\n\n'+marker,1)
    start=changed.index(marker)
    at=changed.index('    m = marker_offset\n',start)
    changed=changed[:at]+'''    branch0 = _decode_v576_server_branch0_parent_tail(decoded, marker_offset)
    if branch0 is not None:
        return branch0
'''+changed[at:]
    compile(changed,str(OUT/'<protocol_branch0_in_memory>'),'exec')
    return original,changed


class PatchedProtocolLoader(importlib.abc.Loader):
    def create_module(self,spec): return None
    def exec_module(self,module):
        _,text=build_source()
        module.__file__=str(BASE/'protocol_v576.py')
        exec(compile(text,str(OUT/'<protocol_branch0_in_memory>'),'exec'),module.__dict__)


class PatchedProtocolFinder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='dragon_nest_builder.live_dps.protocol_v576':
            return importlib.util.spec_from_loader(fullname,PatchedProtocolLoader())
        return None


def isolated(name,patched=False):
    if patched:
        spec=importlib.util.spec_from_loader(name,PatchedProtocolLoader())
    else:
        spec=importlib.util.spec_from_file_location(name,BASE/'protocol_v576.py')
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def emit_patch():
    # The raw-chain validator must explicitly authorize generating this file.
    report=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    assert report['qualified_distinct_parent_cases']>=3
    assert report['raw_phase_and_record_checks_passed']
    original,changed=build_source()
    patch=''.join(difflib.unified_diff(original.splitlines(True),changed.splitlines(True),
        fromfile='a/protocol_v576.py',tofile='b/protocol_v576.py',n=4))
    patch_path=OUT/'protocol_0962_branch0_only.patch'
    patch_path.write_text(patch,encoding='utf-8',newline='\n')
    (OUT/'patch_manifest.json').write_text(json.dumps(dict(base_sha256=EXPECTED_BASE_SHA,
        patched_lf_sha256=hashlib.sha256(changed.encode()).hexdigest(),
        patch_sha256=hashlib.sha256(patch_path.read_bytes()).hexdigest(),
        changed_functions=['_decode_v576_server_branch0_parent_tail (new)','_decode_v576_server_parent_tail (dispatch only)'],
        scope='Only119/224,0962,template2023,branch0; v22 not edited; branch1 and0966 unchanged'),indent=2)+'\n',encoding='utf-8')


if __name__=='__main__': emit_patch()
