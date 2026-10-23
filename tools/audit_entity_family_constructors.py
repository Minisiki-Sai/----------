"""Read-only exact SkillLevel -> player ACT entity-constructor evidence."""
import argparse,json,sys,collections,hashlib,re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.build_dungeon_map_catalog import parse_dnt
from tools.build_attack_instance_catalog import _iter_level_rows,_is_action_key,_unique_action_continuations,_is_player_combat_act_path,parse_act,_canonical_skill_id
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--resource-root',type=Path,default=Path('E:/ext0811.rar/Resource00/Resource00/resource'))
parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'outputs/entity_constructor_graph.json')
args=parser.parse_args()
R=args.resource_root;ext=R/'ext'
players={int(r['RowId']):r for p in ext.glob('skilltable_character*.dnt') if p.stat().st_size for r in parse_dnt(p)}
base={k:int(v.get('_BaseSkillID',0) or 0) for k,v in players.items()}
monsters={int(r['RowId']):r for r in parse_dnt(ext/'monstertable_quest.dnt')}
bykey=collections.defaultdict(set);proofs=collections.defaultdict(list);hashes={}
for p in [ext/'monstertable_quest.dnt',ext/'actortable.dnt',ext/'filetable.dnt',*ext.glob('skilltable_character*.dnt'),*ext.glob('skillleveltable_character*.dnt')]:
 hashes[str(p.relative_to(R))]=hashlib.sha256(p.read_bytes()).hexdigest()
for row in _iter_level_rows(ext):
 sid=int(row.get('_SkillIndex',0) or 0)
 if sid not in players or players[sid].get('_NeedJob',0)<=0:continue
 if int(row.get('_ApplyType',0) or 0):continue
 canon=_canonical_skill_id(sid,base)
 for i in range(1,11):
  for key in str(row.get(f'_ProcessParam{i}','') or '').split(';'):
   key=key.strip()
   if _is_action_key(key):bykey[key.casefold()].add(canon)
for p in sorted((R/'char/player').rglob('*.act')):
 if not _is_player_combat_act_path(p,R/'char/player'):continue
 hashes[str(p.relative_to(R))]=hashlib.sha256(p.read_bytes()).hexdigest()
 try:parsed=parse_act(p)
 except Exception:continue
 cont=_unique_action_continuations(parsed,bykey)
 for a in parsed.actions:
  owners=bykey.get(a.name.casefold(),set())
  if len(owners)!=1:continue
  for part in cont.get(a.name.casefold(),(a,)):
   for s in part.signals:
    if s.signal_type not in {48,126} or len(s.payload)<4:continue
    tpl=int.from_bytes(s.payload[:4],'little')
    if tpl not in monsters:continue
    hashes[str(p.relative_to(R))]=hashlib.sha256(p.read_bytes()).hexdigest()
    proofs[tpl].append({'canonical':next(iter(owners)),'entry':a.name,'action':part.name,'signal_type':s.signal_type,'signal_order':s.order,'path':str(p.relative_to(R)),'payload':s.payload.hex()})
summon_path = ext / 'summonskilltable.dnt'
hashes[str(summon_path.relative_to(R))] = hashlib.sha256(summon_path.read_bytes()).hexdigest()
summon_rows = parse_dnt(summon_path)
ai_parent_rows = {}
for template in (32026, 32029):
 ai_path = R / 'script/monsterai' / str(monsters[template]['_CustomAI']).lower()
 hashes[str(ai_path.relative_to(R))] = hashlib.sha256(ai_path.read_bytes()).hexdigest()
 called = sorted({int(v) for v in re.findall(r'skill_index\s*=\s*(\d+)', ai_path.read_text(encoding='utf8'))})
 edges = [row for row in summon_rows if str(row['_MonsterSkill']) in {str(v) for v in called}]
 candidates = sorted({int(row['_SkillIndex']) for row in edges})
 # Every private AI skill must have a parent-table edge. Adjacent templates,
 # resource names, the legacy source dict and missing monster level rows are
 # never sufficient to certify another variant.
 complete = bool(called) and all(any(str(row['_MonsterSkill']) == str(skill) for row in edges) for skill in called)
 ai_parent_rows[str(template)] = {'ai_path':str(ai_path.relative_to(R)), 'ai_skill_calls':called,
     'summon_table_edges':edges, 'canonical_candidates':candidates,
     'unique_complete_parent': candidates[0] if complete and len(candidates) == 1 else None}
output={'schema':'entity-constructor-static-proof-v1','resource_root':str(R.resolve()),'source_hashes':hashes,'templates':{str(k):{'monster':{f:monsters[k][f] for f in ['_ActorTableID','_CustomAI','_SkillTable']},'canonical_candidates':sorted({p['canonical'] for p in ps}),'proofs':ps} for k,ps in sorted(proofs.items())}, 'reviewed_ai_parent_rows':ai_parent_rows}
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'output':str(args.output.resolve()),'templates':len(proofs),'hashed_inputs':len(hashes)}))
