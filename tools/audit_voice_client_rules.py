"""Read-only client evidence for every configured voice skill (no wire-index guesses)."""
import json
import sys
from pathlib import Path
from hashlib import sha256
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_live_dps_monster_catalog import parse_dnt
from tools.build_attack_instance_catalog import parse_act

root = Path(r'D:\DragonNest\Reborn\Resource_unpacked_all')
ids = {34023,34849,734017,735017,325430,325431,325450,770009,50318,50317,50311,50313,325423,50411,50460}
templates = {503121,4181009,4237003,4237011,4237034,4237035,4237023,4235447,4235448,4237036}
def table(pattern):
    result = {}
    for path in root.glob('*/resource/ext/'+pattern):
        if path.stat().st_size > 10:
            for row in parse_dnt(path):
                result[row['RowId']] = row
    return result
levels = [r for r in table('skillleveltable_monster*.dnt').values() if r.get('_SkillIndex') in ids]
monsters = {i:r for i,r in table('monstertable*.dnt').items() if i in templates}
actors = table('actortable.dnt')
files = table('filetable.dnt')
skills = table('monsterskilltable.dnt')
paths = {}
for path in root.glob('*/resource/char/monster/**/*.act'):
    paths.setdefault(path.name.casefold(), []).append(path)
ai_paths = {}
for path in root.glob('*/resource/script/monsterai/**/*.lua'):
    ai_paths.setdefault(path.name.casefold(), []).append(path)
result = {'skill_rows': levels, 'templates': [], 'warnings': [
    'Client ACT ordinals are not assumed to equal network runtime action codes.',
    'No Nightmare Sea Dragon capture is available; static mappings do not verify announcement timing.',
    'End-based reminders still require a confirmed wire end event, not last damage or nominal animation length.'
]}
for tid, monster in monsters.items():
    actor = actors.get(monster.get('_ActorTableID'), {})
    filename = files.get(actor.get('_ActName'), {}).get('_FileName', '')
    skillrow = skills.get(monster.get('_SkillTable'), {})
    owned = {v for k,v in skillrow.items() if k.startswith('_SkillIndex')}
    entry = {'template':tid, 'ai':monster.get('_CustomAI'), 'actor':monster.get('_ActorTableID'),
        'act':filename,'configured_skills_in_monster_table':sorted(ids & owned),'resources':[]}
    entry['ai_skill_lines'] = [
        {'path':str(path),'line':number,'text':line.strip()}
        for path in ai_paths.get(str(monster.get('_CustomAI','')).casefold(), [])
        for number,line in enumerate(path.read_text(encoding='utf-8',errors='replace').splitlines(),1)
        if not line.strip().startswith('--') and 'skill_index' in line
        and any(str(skill) in line for skill in ids & owned)
    ]
    for path in paths.get(filename.replace('\\', '/').rsplit('/', 1)[-1].casefold(), []):
        if not path.stat().st_size:
            continue
        parsed = parse_act(path)
        desired = {r.get('_ProcessParam1') for r in levels if r['_SkillIndex'] in owned}
        entry['resources'].append({'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest(),
            'entries':[{'ordinal':i,'name':a.name,'animation':a.animation_name,'frames':a.length_frames,
                'next':a.next_action} for i,a in enumerate(parsed.actions) if a.name in desired]})
    result['templates'].append(entry)
out = Path('outputs/verification/voice_client_rule_audit_20261017.json')
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'report':str(out),'templates':result['templates'],
    'skills':[{'skill':r['_SkillIndex'],'entry':r.get('_ProcessParam1')} for r in levels]},ensure_ascii=False))
