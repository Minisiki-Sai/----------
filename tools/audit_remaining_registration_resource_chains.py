"""Re-read the DNT and ACT bytes behind the registration candidates.

No runtime/spec files are written. Names from damage attribution never vote.
"""
from __future__ import annotations
import json
import sys
import hashlib
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.build_attack_instance_catalog import _iter_level_rows, _resolve_projectile_act_path
from tools.build_dungeon_map_catalog import parse_dnt
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full

TARGETS={2002,11084,3023,3021,5708,5709,5737,5713,11088,5718,5717,5719,5712}


def main(targets=None, parent_templates=(5708,3023), output_path=None):
    sys.stdout.reconfigure(encoding='utf8')
    catalog_path=ROOT/'outputs/damage_attribution/attack_instance_catalog_v576.json'
    catalog=json.loads(catalog_path.read_text(encoding='utf8'))
    resources=Path(catalog['resource_root']);ext=resources/'ext'
    selected_targets=TARGETS if targets is None else set(targets)
    evidence=[r for r in catalog['projectile_evidence'] if r['projectile_template_id'] in selected_targets]
    skill_ids={s for r in evidence for s in r['typed_signal_candidate_skill_ids']}
    player_skills=[r for r in catalog['skills'] if r['skill_id'] in skill_ids]
    levels=defaultdict(list)
    for r in _iter_level_rows(ext):
        sid=int(r.get('_SkillIndex',0) or 0)
        if sid not in skill_ids or int(r.get('_ApplyType',0) or 0)!=0:continue
        params={k:str(r[k]) for k in r if str(k).startswith('_ProcessParam') and str(r[k])}
        levels[sid].append({'RowId':r['RowId'],'_SkillIndex':sid,'params':params})
    files={int(r['RowId']):r for r in parse_dnt(ext/'filetable.dnt')}
    projectiles={int(r['RowId']):r for r in parse_dnt(ext/'weapontable_projectile.dnt')}
    paths=defaultdict(list)
    for path in (resources/'weapon').rglob('*.act'):paths[path.name.casefold()].append(path)
    parsed={};checks=[]
    def read(path):
        if path not in parsed:parsed[path]=parse_full(path)
        return parsed[path]
    for skill in player_skills:
        for action in skill['matched_actions']:
            path=resources/action['file'];doc=read(path)
            binary=next(a for a in doc['actions'] if a['name']==action['action_name'])
            selected=[s for s in binary['signals'] if s['type']==15 and s['u32'][0] in selected_targets]
            if not selected:continue
            entry=action.get('entry_action_name',action['action_name'])
            matching_levels=[r for r in levels[skill['skill_id']] if any(entry in v.split(';') for v in r['params'].values())]
            assert matching_levels, (skill['skill_id'],entry)
            refs=[]
            if entry!=binary['name']:
                # Verify an explicit path, not a suffix/name similarity.
                byname={a['name']:a for a in doc['actions']};queue=[(entry,[])];seen=set()
                while queue:
                    name,edges=queue.pop(0)
                    if name in seen or name not in byname:continue
                    seen.add(name)
                    if name==binary['name']:refs=edges;break
                    parent=byname[name]
                    if parent['next_action'] in byname:
                        queue.append((parent['next_action'],edges+[{'from':name,'to':parent['next_action'],'field':'next_action','action_offset':parent['offset']}]))
                    for s in parent['signals']:
                        if s['type']==1:
                            for index,text in s['strings']:
                                if index==1 and text in byname:
                                    queue.append((text,edges+[{'from':name,'to':text,'field':'signal1.string[1]','signal_offset':s['offset']}]))
                assert refs,(entry,binary['name'])
            checks.append({'kind':'SkillLevel_exact_key_ACT_signal15','skill_id':skill['skill_id'],
                           'canonical_skill_id':skill['canonical_skill_id'],'skill_name':skill['name'],
                           'level_row_count':len(matching_levels),'level_examples':matching_levels[:2],
                           'act_path':str(path),'entry_action':entry,'action_name':binary['name'],'continuation':refs,
                           'signals':[{'offset':s['offset'],'payload_offset':s['offset']+16,
                                       'type':15,'template':s['u32'][0], 'payload0_le_hex':int(s['u32'][0]).to_bytes(4,'little').hex(),
                                       'start_frame':s['start'],'end_frame':s['end']} for s in selected]})
    for parent in parent_templates:
        row=projectiles[parent];file_id=int(row['_ActName']);filename=str(files[file_id]['_FileName'])
        path=_resolve_projectile_act_path(resources,filename,paths);doc=read(path)
        for a in doc['actions']:
            signals=[s for s in a['signals'] if s['type']==15 and s['u32'][0] in selected_targets]
            if signals:
                checks.append({'kind':'Projectile_numeric_file_ACT_child','parent_template':parent,
                               '_ActName_filetable_id':file_id,'filename':filename,'act_path':str(path),
                               'action_name':a['name'],'signals':[{'offset':s['offset'],'payload_offset':s['offset']+16,
                                 'type':15,'child_template':s['u32'][0],'start_frame':s['start'],'end_frame':s['end']} for s in signals]})
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in parsed}
    output=output_path or ROOT/'outputs/audit_remaining_registration_packet_20260907/resource_chains.json'
    output.write_text(json.dumps({'catalog_path':str(catalog_path),'catalog_sha256':hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
                                 'catalog_evidence':evidence,'ACT_file_hashes':hashes,'verified_checks':checks},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(output),'ACT_files':len(parsed),'verified_chains':len(checks)},ensure_ascii=False))


if __name__=='__main__':main()
