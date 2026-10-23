"""Reproduce the exact RuneResonate -> ACT capsule -> mutated Tower family.

No existing decoder skill label or template-family constant participates.
The private AI skill's level rows live in SkillLevelTable_Rune, which is
independent of the ordinary character and monster level tables.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_dungeon_map_catalog import parse_dnt
from tools.build_attack_instance_catalog import parse_act


def audit(root):
    hashes = {}
    def read(path):
        hashes[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
        return path
    def table(name): return parse_dnt(read(root/'ext'/name))
    def signal(row):
        return {'type':row.signal_type,'order':row.order,'payload_hex':row.payload.hex()}

    resonance = table('runeresonatetable.dnt')
    parent_edges = []
    for row in resonance:
        for index in range(1, 6):
            if int(row.get(f'_changeSkill{index}', 0)) == 293149:
                parent_edges.append({'row':row, 'parent':int(row[f'_baseSkill{index}'])})
    assert {row['parent'] for row in parent_edges} == {4351}
    skills = {row['RowId']:row for row in table('skilltable_rune.dnt')}
    assert skills[293149]['_NeedJob'] == 48
    levels = table('skillleveltable_rune.dnt')
    summon_levels = [row for row in levels if row['_SkillIndex'] == 293149]
    assert {row['_ProcessParam1'] for row in summon_levels} == {'Skill_Summon_ElectricSawTower_Mutation'}
    player_path = root/'char/player/academic/academic_skill_2st_gearmaster.act'
    player = parse_act(read(player_path))
    entry = next(row for row in player.actions if row.name == summon_levels[0]['_ProcessParam1'])
    capsule_signals = [row for row in entry.signals if row.signal_type == 15]
    assert {int.from_bytes(row.payload[:4], 'little') for row in capsule_signals} == {11050}
    projectiles = {row['RowId']:row for row in table('weapontable_projectile.dnt')}
    files = {row['RowId']:row for row in table('filetable.dnt')}
    capsule_file = files[projectiles[11050]['_ActName']]
    capsule_path = root/'weapon'/capsule_file['_FileName'].lower()
    capsule = parse_act(read(capsule_path))
    constructors = [{'action':action.name,'signal':signal(row)}
        for action in capsule.actions for row in action.signals if row.signal_type in (48,126)]
    assert {int.from_bytes(bytes.fromhex(row['signal']['payload_hex'])[:4], 'little') for row in constructors} == {32029}
    monsters = {row['RowId']:row for row in table('monstertable_quest.dnt')}
    monster = monsters[32029]
    ai_path = root/'script/monsterai'/monster['_CustomAI'].lower()
    ai = read(ai_path).read_text(encoding='utf8')
    calls = sorted({int(row) for row in re.findall(r'skill_index\s*=\s*(\d+)', ai)})
    assert calls == [293166]
    private_levels = [row for row in levels if row['_SkillIndex'] == 293166]
    assert {row['_ProcessParam1'] for row in private_levels} == {'Attack_Fire'}
    actors = {row['RowId']:row for row in table('actortable.dnt')}
    actor = actors[monster['_ActorTableID']]
    monster_file = files[actor['_ActName']]
    monster_path = root/'char/monster/academic_tower'/monster_file['_FileName'].lower()
    monster_act = parse_act(read(monster_path))
    attack = next(row for row in monster_act.actions if row.name == 'Attack_Fire')
    damaging = [signal(row) for row in attack.signals if row.signal_type in (4,15)]
    assert damaging
    return {'schema':'mutated-tower-parent-chain-v1','resource_root':str(root.resolve()),
        'source_hashes':hashes,'unique_canonical_parent':4351,'template':32029,
        'rune_parent_edges':parent_edges,'rune_summon_skill':skills[293149],
        'rune_summon_levels':summon_levels,'player_act':player_path.relative_to(root).as_posix(),
        'player_entry':entry.name,'capsule_signals':[signal(row) for row in capsule_signals],
        'capsule_projectile_row':projectiles[11050],'capsule_filetable_row':capsule_file,
        'capsule_act':capsule_path.relative_to(root).as_posix(),'exact_entity_constructors':constructors,
        'entity_monster_row':monster,'entity_ai':ai_path.relative_to(root).as_posix(),
        'entity_ai_called_skills':calls,'entity_private_skill':skills[293166],
        'entity_private_levels':private_levels,'entity_actor_row':actor,
        'entity_act':monster_path.relative_to(root).as_posix(),'exact_private_action':'Attack_Fire',
        'private_action_damage_signals':damaging}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource-root',type=Path,default=Path('E:/ext0811.rar/Resource00/Resource00/resource'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=audit(args.resource_root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'parent':report['unique_canonical_parent'],'template':report['template'],
        'hashed_inputs':len(report['source_hashes']),'output':str(args.output.resolve())},ensure_ascii=True))


if __name__ == '__main__': main()
