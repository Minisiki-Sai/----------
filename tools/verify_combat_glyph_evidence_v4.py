"""Offline comparison against exact unpacked SkillLevel rows. No runtime edits."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.extract_skill_glyphs import parse_dnt, RESOURCE_EXT
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.combat_glyph_inference import _candidate_rows


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):h.update(block)
    return h.hexdigest()


def scalar(value):
    try:
        x=float(value)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):return None


def near(a,b,tolerance=.0005):
    return a is not None and b is not None and abs(a-b)<=tolerance


def slots(skill,level,effect):
    return [(i,level.get(f'_EffectClassValue{i}'),level.get(f'_EffectClassValue{i}Duration',0))
            for i in range(1,11) if skill.get(f'_EffectClass{i}')==effect]


def duration_match(group,candidate,skill,level):
    """Never substitute a max learnable level or an unrelated effect duration."""
    if group['permanent'] or level.get('_SkillIndex')!=int(group['raw_skill_id']):return []
    found=[]
    for slot,value,duration in slots(skill,level,group['effect_class']):
        if duration<=0 or not near(scalar(value),scalar(group['parameter'])):continue
        expected=duration*(1+candidate['value']/100)
        # Server duration is millisecond evidence, not a rounded UI counter.
        if abs(group['duration_ms']-expected)<=2:
            found.append({'slot':slot,'base_duration_ms':duration,'observed_duration_ms':group['duration_ms'],
                          'base_parameter':value,'observed_parameter':group['parameter']})
    return found


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('inputs',nargs='+',type=Path)
    ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    paths=[RESOURCE_EXT/'skilltable_character.dnt',RESOURCE_EXT/'glyphskilltable.dnt',RESOURCE_EXT/'itemtable_glyph.dnt']
    paths+=sorted(RESOURCE_EXT.glob('skillleveltable_character*.dnt'))
    hashes={str(p):sha(p) for p in paths}
    skills={r['RowId']:r for r in parse_dnt(paths[0])}
    glyphs=defaultdict(list)
    active_items={r['RowId'] for r in parse_dnt(paths[2]) if r.get('_Type')==38}
    for r in parse_dnt(paths[1]):
        if r['RowId'] in active_items:
            for sid in re.findall(r'\d+',str(r.get('_SkillID',''))):glyphs[int(sid)].append(r)
    levels={}
    for p in paths[3:]:
        for r in parse_dnt(p):
            versions=levels.setdefault(r['RowId'],[])
            if r not in versions:versions.append(r)
    store=DataStore.load()
    reports=[]
    for path in args.inputs:
        audit=json.loads(path.read_text(encoding='utf-8'))
        result={'capture':audit['capture'],'capture_sha256':audit['capture_sha256'],'input_sha256':sha(path),'players':[]}
        for player in audit['players']:
            source=player['source_id']
            candidates=_candidate_rows(store,player['job'])
            source_groups=[r for r in audit['parameter_groups'] if r['source_id']==source]
            confirmed=[]
            for c in candidates:
                sid=int(c['skill_id'])
                if c['effect'] not in {'伤害增加','动作速度增加','增益持续时间增加'}:continue
                skill=skills.get(sid,{})
                proof=[]
                configured=[]
                for plate in glyphs[sid]:
                    if c['effect']=='增益持续时间增加':
                        if plate.get('_GlyphSkillEffect')==4 and near(plate.get('_GlyphSkillEffectValue'),c['value']/100):configured.append(plate['RowId'])
                    else:
                        effect=50 if c['effect']=='伤害增加' else 25
                        if any(plate.get(f'_EffectClass{i}')==effect and near(scalar(plate.get(f'_EffectClassValue{i}')),c['value']/100) for i in range(1,6)):configured.append(plate['RowId'])
                if not configured:continue
                for group in source_groups:
                    if group['raw_skill_id']!=str(sid):continue
                    level_matches=[r for r in levels.get(group['level_row'],[]) if r.get('_SkillIndex')==sid]
                    if len(level_matches)!=1:continue
                    level=level_matches[0]
                    if c['effect']=='增益持续时间增加':
                        matches=duration_match(group,c,skill,level)
                    else:
                        effect=50 if c['effect']=='伤害增加' else 25
                        # An intrinsic class with the same value is not a plate.
                        matches=[{'extra_effect_class':effect,'observed_parameter':group['parameter']}] if (
                            group['effect_class']==effect and near(scalar(group['parameter']),c['value']/100)
                            and not slots(skill,level,effect)) else []
                    if matches:
                        proof.append({'level_row':group['level_row'],'skill_level':level['_SkillLevel'],'effect_class':group['effect_class'],
                                      'code':group['code'],'responses':group['count'],'timestamps_ms':group['timestamps_ms'],
                                      'targets':group['targets'],'matches':matches})
                if not proof:continue
                times=sorted({t for p in proof for t in p['timestamps_ms']})
                episodes=[]
                for t in times:
                    if not episodes or t-episodes[-1]>100:episodes.append(t)
                old=any(r['skill_id']==str(sid) and r['effect']==c['effect'] and r['value']==c['value'] for r in player['glyphs'])
                confirmed.append({'skill':c['skill'],'skill_id':str(sid),'effect':c['effect'],'value':c['value'],'already_in_runtime_result':old,
                                  'glyph_table_rows':configured,'response_episodes_100ms':len(episodes),
                                  'evidence_responses':sum(p['responses'] for p in proof),'proof':proof})
            signatures=defaultdict(list)
            for r in confirmed:signatures[r['skill_id']].append((r['effect'],r['value']))
            conflicts={sid:rows for sid,rows in signatures.items() if len(rows)>1}
            old_unverified=[r for r in player['glyphs'] if not any(
                p['skill_id']==r['skill_id'] and p['effect']==r['effect'] and p['value']==r['value'] for p in confirmed)]
            result['players'].append({'source_id':source,'name':player['name'],'job':player['job'],
                                      'strong_parameter_evidence':confirmed,'conflicts':conflicts,'old_not_independently_verified':old_unverified,
                                      'remaining_slots':'unknown; no forced fill to four or seven'})
        reports.append(result)
    assert hashes=={str(p):sha(p) for p in paths},'DNT changed'
    payload={'scope':'Offline proof review only; runtime/scoring unchanged. Response counts are not independent casts or accuracy probabilities.',
             'dnt_sha256':hashes,'captures':reports}
    with args.output.open('x',encoding='utf-8') as out:json.dump(payload,out,ensure_ascii=False,indent=2)
    print(json.dumps([{'capture':Path(c['capture']).stem,'players':[{'name':p['name'],'job':p['job'],
          'strong':[r['skill']+' '+r['effect']+' '+str(r['value'])+(' NEW' if not r['already_in_runtime_result'] else '') for r in p['strong_parameter_evidence']],
          'old_unverified':[r['skill']+' '+r['effect']+' '+str(r['value']) for r in p['old_not_independently_verified']],
          'conflicts':p['conflicts']} for p in c['players']]} for c in reports],ensure_ascii=False,indent=1))


if __name__=='__main__':main()
