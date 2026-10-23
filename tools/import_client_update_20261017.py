"""Reproducible October client data import; keeps first-run source backups."""
from pathlib import Path
from collections import defaultdict
import hashlib
import html
import json
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_dungeon_map_catalog import parse_dnt, load_uistring

CLIENT = Path(r'D:\DragonNest\Newborn\Resource_unpacked_all_20260916_update')
DATA = ROOT / 'dragon_nest_builder/data/modular'
BACKUP = ROOT / 'work/update_20261017/before'
SOURCES = {}
CHANGES = []

def table(name):
    paths = list(CLIENT.glob(f'Resource*/resource/ext/{name}.dnt'))
    assert len(paths) == 1, paths
    SOURCES[str(paths[0])] = hashlib.sha256(paths[0].read_bytes()).hexdigest()
    return parse_dnt(paths[0])

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def save(path, value, compact=False):
    text = json.dumps(value, ensure_ascii=False, indent=None if compact else 2) + '\n'
    if path.exists() and read(path) == value:
        return
    if path.exists():
        backup = BACKUP / path.relative_to(ROOT)
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    CHANGES.append(str(path.relative_to(ROOT)))

STATE = {0:'力量',1:'敏捷',2:'智力',3:'体质',4:'最小物攻',5:'最大物攻',
         6:'最小魔攻',7:'最大魔攻',12:'致命',29:'最终',103:'致命伤害',
         50:'力量%',51:'敏捷%',52:'智力%',54:'物攻%',55:'物攻%',56:'魔攻%',57:'魔攻%'}

def attrs(row, prefix='_State', value_suffix='Value'):
    result = defaultdict(float)
    paired = {}
    for i in range(1, 11):
        state = row.get(f'{prefix}{i}', -1)
        value = float(row.get(f'{prefix}{i}{value_suffix}', 0) or 0)
        if not value:
            continue
        assert state in STATE, (state, value, row)
        name = STATE[state]
        value = round(float(value) * (100 if name.endswith('%') else 1), 8)
        if state in (54,55,56,57):
            # Paired min/max percentages apply to endpoints, not twice to attack.
            assert name not in paired or abs(paired[name]-value)<1e-6
            paired[name] = value
        else:
            result[name] += value
    result.update(paired)
    return dict(result)

def main():
    ui_path = CLIENT/'Resource15/resource/uistring/uistring.xml'
    ui = load_uistring(ui_path)
    SOURCES[str(ui_path)] = hashlib.sha256(ui_path.read_bytes()).hexdigest()
    rings = [r for r in table('itemtable_cash') if ui.get(r['_NameID']) == '梦魇主宰的戒指']
    assert len(rings) == 2
    base = attrs(rings[0], value_suffix='_Min')
    assert base == attrs(rings[1], value_suffix='_Min')
    assert base == {'物攻%':0.75,'魔攻%':0.75}
    enchant = {r['_EnchantLevel']:r for r in table('enchanttable') if r['_EnchantID']==rings[0]['_EnchantID']}
    assert set(enchant) == set(range(1,11))
    fashion_path = DATA/'tables/fashion.json'
    fashion = read(fashion_path)
    for level in range(11):
        values = dict(base)
        if level:
            for key, value in attrs(enchant[level]).items():
                values[key] = round(values.get(key,0)+value, 8)
        fashion['戒指'][f'梦魇主宰的戒指（+{level}）'] = values
    save(fashion_path, fashion)
    path = DATA/'lists/misc.json'
    misc = read(path)
    misc['fashion']['戒指'] = list(dict.fromkeys([*misc['fashion']['戒指'], *fashion['戒指']]))
    save(path, misc)
    resonance = [dict(id=r['RowId'], level=r['_EquipLevel'], quality=r['_EquipQuality'],
                      enhance=r['_NeedStrength'], attrs=attrs(r),
                      buff_name_code=r['_Buffname'], buff_description_code=r['_Buffdesc'])
                 for r in table('resonancetable') if r['_IsValid']]
    save(DATA/'tables/equipment_resonance.json', dict(version=20261017, tiers=resonance))

    path = DATA/'attribute_order.json'
    order = read(path)
    if '无属性%' not in order:
        order.insert(order.index('暗攻%')+1, '无属性%')
    save(path, order)
    # Separate always-on neutral bonuses from timed mastery triggers.
    passive_jobs = {'狙翎':'物理攻击掌握','魔羽':'魔法攻击掌握','重炮手':'摄魂怪置换',
        '刀锋舞者':'刀锋之力','灵魂舞者':'灵魂之力','剑皇':'体能暴涨','月之领主':'魔法爆发',
        '狂战士':'致命一击掌握','机械大师':'机械时刻','影舞者':'生理感觉',
        '风行者':'心理感觉','时空领主':'时间掌控者'}
    path = DATA/'tables/skill_buff.json'
    buffs = read(path)
    for job, skill in passive_jobs.items():
        label = f'{skill}（常驻无属性）'
        buffs[f'被动|{job}|{label}'] = dict(类别='被动',来源=job,技能=label,最大等级=1,等级={'1':{'无属性%':5}})
    # Destroyer variant is exclusive with normal Battle Howl, not additive.
    battle = json.loads(json.dumps(buffs['团队buff|通用|战吼']))
    battle.update(来源='毁灭者', 技能='战吼（力量掌握）')
    for row in table('skillleveltable_characterwarrior'):
        if row['_SkillIndex'] != 402 or row['_ApplyType'] != 0 or not 1 <= row['_SkillLevel'] <= 8:
            continue
        battle['等级'][str(row['_SkillLevel'])] = {
            '物攻':float(row['_EffectClassValue1']), '魔攻':float(row['_EffectClassValue4']),
            '致命':float(row['_EffectClassValue2']), '眩晕面板%':float(row['_EffectClassValue3'])*100,
            '无属性%':float(row['_EffectClassValue5'])*100}
    battle['最大等级'] = 8
    buffs['团队buff|毁灭者|战吼（力量掌握）'] = battle
    save(path, buffs)
    save(DATA/'tables/skills/neutral_passives.json', dict(version=20261017, jobs=passive_jobs))

    path = DATA/'tables/skills/job_mechanics.json'
    mechanics = read(path)
    fury = mechanics['jobs']['狂战士']['resources']['fury']
    fury['max'] = 8
    fury['consumers'].pop('滚动攻击',None)
    fury['consumers']['旋转冲击'] = [6, 5/1.5]
    # Unboosted EX still includes its ordinary 50% follow-up; boosted TOTAL is 500%.
    save(path, mechanics)
    path = DATA/'tables/skills/skill_sim_adjustments.json'
    adjustments = read(path)
    adjustments['狂战士']['滚动攻击']['multiplier'] = 1
    adjustments['狂战士']['旋转冲击']['multiplier'] = 5
    save(path, adjustments)
    path = DATA/'tables/skills/skill_cooldown_rules.json'
    cooldowns = read(path)
    rolling = cooldowns['jobs']['狂战士'].get('滚动攻击', {})
    rolling['sources'] = [r for r in rolling.get('sources',[]) if r.get('skill') not in ('践踏','屠戮盛宴')]
    save(path, cooldowns)

    # Full EX combo: follow-up input at frame 136, then generic skill unlock
    # at follow-up frame 60. Normal EX uses frames 70 + 65. ACT runs at 60fps.
    # Keep both routes so the live fury state selects the right action length.
    path = DATA/'tables/skills/skill_cast_times.json'
    timings = read(path)
    for row in timings['rows']:
        if row.get('job') == '狂战士' and row.get('skill') == '旋转冲击EX':
            row.update(cast_time=196/60, quick_time=None, fury_unboosted_cast_time=135/60,
                       notes='20261017客户端ACT：6怒气真旋转冲击136f接追加60f；无怒气EX为70f接追加65f；均含左键追击')
    save(path, timings)

    # Import current, linked PVE descriptions; never mix in the PvP row with
    # the same skill id/level. Preserve existing tree layout and curated damage.
    levels = {}
    levels_by_identity = {}
    ambiguous = set()
    for file in sorted(CLIENT.glob('Resource*/resource/ext/skillleveltable_character*.dnt')):
        if 'monster' in file.name.lower():
            continue
        for row in table(file.stem):
            if row.get('_ApplyType') != 0:
                continue
            key = (str(row['_SkillIndex']), int(row['_SkillLevel']))
            identity = (*key, int(row['RowId']))
            assert identity not in levels_by_identity or levels_by_identity[identity] == row, identity
            levels_by_identity[identity] = row
            if key in levels:
                if not all(levels[key][field] == row[field] for field in
                           ('_SkillExplanationID','_SkillExplanationIDParam')):
                    ambiguous.add(key)
            levels[key] = row
    for key in ambiguous:
        levels.pop(key, None)
    passive_ids = dict(zip(passive_jobs, (1307,1357,4307,5507,5607,307,357,507,4357,1507,1557,2507)))
    skill_rows = {r['RowId']:r for r in table('skilltable_character')}
    passive_evidence = {}
    for job, skill_id in passive_ids.items():
        slots = [i for i in range(1,11) if skill_rows[skill_id].get(f'_EffectClass{i}') == 507]
        assert len(slots) == 1, (job, slots)
        slot = slots[0]
        row = levels[(str(skill_id),1)]
        value = float(row[f'_EffectClassValue{slot}'])*100
        assert abs(value-5)<1e-7, (job,value)
        assert str(skill_rows[skill_id][f'_EffectClass{slot}UnlockSkill']) == '0'
        passive_evidence[job] = dict(skill_id=skill_id,effect_class=507,slot=slot,percent=value,unconditional=True)
    descriptions = []
    unresolved_descriptions = set()
    resolved_by_row_id = set()
    for path in (DATA/'tables/skills/dngamer_skill_sim_reborn_split/jobs').glob('job_*.json'):
        job = read(path)
        for skill_id, entries in job.get('skill_levels',{}).get('normal',{}).get('pve',{}).items():
            for entry in entries:
                key = (skill_id, int(entry['_SkillLevel']))
                # Some client tables repeat a displayed level with a different
                # required character level. Keep those distinct using RowId;
                # never overwrite one variant with whichever row was last.
                row = levels_by_identity.get((*key, int(entry.get('RowId', -1)))) or levels.get(key)
                if not row:
                    if key in ambiguous:
                        unresolved_descriptions.add(key)
                    continue
                if key in ambiguous:
                    resolved_by_row_id.add(key)
                template = ui.get(row.get('_SkillExplanationID'), '')
                if not template:
                    continue
                params = str(row.get('_SkillExplanationIDParam','')).split(',')
                rendered = re.sub(r'\{(\d+)\}', lambda m: params[int(m[1])] if int(m[1])<len(params) else m[0], template)
                # Plain escaped text preserves all content without client color codes.
                rendered = re.sub(r'#[ywbngr]', '', rendered)
                rendered = '<div class="uistring">'+html.escape(rendered).replace('\n','<br/>')+'</div>'
                if entry.get('desc') != rendered:
                    entry['desc'] = rendered
                    descriptions.append((path.name,skill_id,entry['_SkillLevel']))
        save(path, job, compact=True)
    changed_from_backup = 0
    for path in (DATA/'tables/skills/dngamer_skill_sim_reborn_split/jobs').glob('job_*.json'):
        backup = BACKUP/path.relative_to(ROOT)
        if not backup.exists():
            continue
        old = read(backup).get('skill_levels',{}).get('normal',{}).get('pve',{})
        new = read(path).get('skill_levels',{}).get('normal',{}).get('pve',{})
        for skill_id, entries in new.items():
            prior = {(r['_SkillLevel'],r.get('RowId')):r.get('desc') for r in old.get(skill_id,[])}
            changed_from_backup += sum(r.get('desc') != prior.get((r['_SkillLevel'],r.get('RowId'))) for r in entries)
    evidence = {'sources':SOURCES, 'files_changed_this_run':list(CHANGES),
                'descriptions_changed_from_backup':changed_from_backup, 'descriptions_updated_this_run':len(descriptions),
                'ring_base':base, 'ring_plus10':fashion['戒指']['梦魇主宰的戒指（+10）'],
                'resonance_tiers':len(resonance), 'passives':passive_jobs, 'passive_evidence':passive_evidence,
                'ambiguous_descriptions_resolved_by_row_id': sorted(resolved_by_row_id),
                'ambiguous_descriptions_preserved': sorted(unresolved_descriptions)}
    save(ROOT/'work/update_20261017/import-report.json', evidence)
    print(json.dumps(evidence,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
