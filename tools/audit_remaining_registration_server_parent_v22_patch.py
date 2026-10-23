"""Export a registration-only patch from frozen v21 to private v22."""
import ast
import difflib
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/audit_remaining_registration_server_parent_v22_20260907'
BASE=ROOT/'outputs/damage_attribution_candidate_v21_20260907/protocol_v576.py'
NEW=ROOT/'outputs/damage_attribution_candidate_v22_20260907/protocol_v576.py'


def main():
    before=BASE.read_text(encoding='utf-8');after=NEW.read_text(encoding='utf-8')
    def nodes(s):
        return {getattr(n,'name',f'node:{i}'):ast.dump(n,include_attributes=False)
                for i,n in enumerate(ast.parse(s).body)}
    # Compare named top-level functions/classes; separately remove the single
    # inserted helper and compare every other AST node in source order.
    b=ast.parse(before);a=ast.parse(after)
    allowed={'_decode_v576_server_parent_tail','decode_v576_server_damage_object_registrations'}
    clean=lambda tree:[ast.dump(n,include_attributes=False) for n in tree.body if getattr(n,'name',None) not in allowed]
    assert clean(b)==clean(a),'Unexpected change outside server registration functions'
    patch=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
                 fromfile='a/dragon_nest_builder/live_dps/protocol_v576.py',
                 tofile='b/dragon_nest_builder/live_dps/protocol_v576.py'))
    (OUT/'server_parent_registration_only.patch').write_text(patch,encoding='utf-8')
    data=dict(base_sha256=hashlib.sha256(BASE.read_bytes()).hexdigest(),
              candidate_sha256=hashlib.sha256(NEW.read_bytes()).hexdigest(),
              allowed_functions=sorted(allowed),other_top_level_ast_unchanged=True,
              shared_file_read_or_written=False,
              note='Apply only this local diff after reviewing latest shared code. Do not copy candidate protocol over shared.')
    (OUT/'patch_manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False))


if __name__=='__main__':main()
