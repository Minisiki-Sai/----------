"""Emit independent protocol diffs; frozen candidate files are read-only.

This audit owns this script/output only. Importing it does not change runtime.
"""
from __future__ import annotations
import difflib
import hashlib
import importlib.abc
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'outputs/damage_attribution_candidate_v22_20260907/protocol_v576.py'
OUT=ROOT/'outputs/audit_remaining_registration_ffff_final_20260907'
EXPECTED='0f36b49f20e747e10edc8c83189f24c08bac10c8eaa1cd8c5ca6064fac38b9f8'

HELPER='''def _decode_v576_server_relay_parent_tail(
    decoded: bytes, marker_offset: int,
) -> tuple[int, int, int, str] | None:
    """Audited relay TLV envelopes; 0966 has a 102-byte body, not 98.

    FFFF and the last four 0966 bytes remain opaque. Neither is a parent
    reference or evidence of a new creation. No new template mapping here.
    """
    m = marker_offset
    if decoded[:1] == b"\\xfe":
        start = 21
    elif decoded[:4] == b"\\x00\\x00\\x00\\x01":
        start = 24
    else:
        return None
    if len(decoded) < start + 105 or not start + 4 <= m < len(decoded) - 100:
        return None
    observer = _UINT32_LE.unpack_from(decoded, start - 4)[0]
    if not 0x10000 <= observer < 0xFFFFFFFF:
        return None
    # A closed set of independently bounded companion records. No scan for
    # an 09 marker inside another record's opaque body is allowed.
    pairs = {(1, 32), (3, 41), (4, 17), (9, 98), (9, 102),
             (12, 4), (12, 8), (16, 21), (16, 25), (18, 32),
             (30, 30), (76, 9)}
    position, count, selected_end = start, 0, 0
    while position + 7 <= len(decoded) and count < 16:
        owner = _UINT32_LE.unpack_from(decoded, position)[0]
        op = decoded[position + 4]
        size = int.from_bytes(decoded[position + 5:position + 7], "little")
        end = position + 7 + size
        if not 0 < owner < 0xFFFFFFFF or (op, size) not in pairs or end > len(decoded):
            return None
        if position + 4 == m:
            if op != 9 or size not in (98, 102):
                return None
            selected_end = end
        position, count = end, count + 1
    if position != len(decoded) or not selected_end:
        return None
    owner = _UINT32_LE.unpack_from(decoded, m - 4)[0]
    obj = _UINT32_LE.unpack_from(decoded, m + 8)[0]
    tpl = _UINT32_LE.unpack_from(decoded, m + 12)[0]
    parent = _UINT32_LE.unpack_from(decoded, m + 49)[0]
    branch = _UINT32_LE.unpack_from(decoded, m + 53)[0]
    opaque_suffix = _UINT32_LE.unpack_from(decoded, m + 69)[0]
    if (
        owner != _UINT32_LE.unpack_from(decoded, m + 44)[0]
        or decoded[m + 3:m + 8] != bytes(5)
        or not 0 < obj <= 0xFFFFFF or not 0 < tpl <= 0x7FFFFFFF
        or decoded[m + 48] != 0
        or not 0 < parent <= 0xFFFFFF or parent == obj
        or branch & 0xFFFF != 1
        or decoded[m + 57:m + 65] != b"\\xff" * 8
        or not 0 < opaque_suffix <= 0xFFFF
    ):
        return None
    key = (decoded[m - 4:m] + decoded[m + 3:selected_end]).hex()
    return parent, opaque_suffix, branch >> 16, key


'''

def once(text,old,new):
    assert text.count(old)==1,(old,text.count(old))
    return text.replace(old,new,1)

def sources():
    raw=BASE.read_bytes();assert hashlib.sha256(raw).hexdigest()==EXPECTED
    base=raw.decode('utf-8').replace('\r\n','\n')
    # Only parent-shaped layouts: repeated resource register layouts untouched.
    a=once(base,'or not 0 < resource < 0xFFFF\n            ):','or not 0 < resource <= 0xFFFF\n            ):')
    a=once(a,'or not 0 < resource_suffix < 0xFFFF','or not 0 < resource_suffix <= 0xFFFF')
    b=once(a,'def _decode_v576_server_parent_tail(',HELPER+'def _decode_v576_server_parent_tail(')
    b=once(b,'    else:\n        return None\n    # Check the complete fixed boundaries',
             '    else:\n        return _decode_v576_server_relay_parent_tail(decoded, marker_offset)\n    # Check the complete fixed boundaries')
    b=once(b,'if parent_qualified_subtype and marker_bytes == b"\\x09\\x62\\x00"',
             'if parent_qualified_subtype and marker_bytes in (b"\\x09\\x62\\x00", b"\\x09\\x66\\x00")')
    b=once(b,'"s2c-udp-damage-object-parent-observation-0962-v576"',
             'f"s2c-udp-damage-object-parent-observation-{marker_bytes[:2].hex()}-v576"')
    return base,a,b

class VirtualProtocolLoader(importlib.abc.Loader):
    def create_module(self,spec):return None
    def exec_module(self,module):
        module.__file__=str(BASE)
        exec(compile(sources()[2],str(OUT/'virtual_protocol_ffff.py'),'exec'),module.__dict__)

class VirtualProtocolFinder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='dragon_nest_builder.live_dps.protocol_v576':
            return importlib.util.spec_from_loader(fullname,VirtualProtocolLoader())
        return None

def main():
    OUT.mkdir(exist_ok=True)
    base,a,b=sources();target='dragon_nest_builder/live_dps/protocol_v576.py'
    patches={}
    for name,old,new in [('01_parent_ffff_only.patch',base,a),('02_relay_0966_and_0962.patch',a,b),('combined.patch',base,b)]:
        patch=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+target,tofile='b/'+target))
        (OUT/name).write_text(patch,encoding='utf-8',newline='\n')
        patches[name]=hashlib.sha256(patch.encode()).hexdigest()
    (OUT/'patch_manifest.json').write_text(json.dumps(dict(base_sha256=EXPECTED,patches=patches,
        no_runtime_writes=True,phase_uniqueness='existing server thirteen-phase guard retained',
        integration='01 stands alone; 02 fallback must be merged with Plato newer envelope dispatch, not overwrite it',
        scope='parent-shaped FFFF only; no skill mappings, damage, expiry or direction changes'),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(patches))

if __name__=='__main__':main()
