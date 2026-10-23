"""Revalidate source DNT/ACT bytes for cross-profession registration shapes."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.audit_remaining_registration_resource_chains import main

if __name__=='__main__':
    main(targets={3022,3025,3030,60070,5515,5516,5517,11075,11076,5713,5714,4004,63},
         parent_templates=(3022,11075,5713),
         output_path=ROOT/'outputs/audit_remaining_registration_cross_profession_20260907_v1/resource_chains.json')
