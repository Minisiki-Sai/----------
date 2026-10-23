"""Selected-file follow-up to the target-complete census; no runtime mapping."""
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full
from tools.build_live_dps_monster_catalog import parse_dnt
RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
OUT = ROOT / 'outputs/magic_grass_census_v38_20260908'


def review():
    inputs = {}
    def mark(path):
        data = path.read_bytes()
        inputs[str(path)] = dict(bytes=len(data), sha256=sha256(data).hexdigest())
    cp = OUT/'census.json'
    mark(cp)
    census = json.loads(cp.read_text('utf-8'))
    assert len(census['manifest']) == 7776 and not census['errors']
    # Normalize the initial discovery artifact: name-only matches are NOT
    # canonical proof. Retain the original file and checksum for traceability.
    creators = []
    for r in census['creators']:
        x = dict(r)
        if 'families' in x:
            x['name_candidate_families'] = x.pop('families')
            x['unscoped_name_bindings'] = x.pop('bindings')
        creators.append(x)
    def table(name):
        p = RESOURCE/'ext'/name
        mark(p)
        return parse_dnt(p)
    files = {r['RowId']: r for r in table('filetable.dnt')}
    weapons = {r['RowId']: r for r in table('weapontable_projectile.dnt')}
    player = RESOURCE/'char/player/academic/academic_skill_1st_alchemist.act'
    physician = RESOURCE/'char/player/academic/academic_skill_2st_physician.act'
    mark(player); mark(physician)
    actions = {a['name']: a for a in parse_full(player)['actions']}
    routes = []
    for action_name, projectile_id, template in [('Skill_SummonSlime1',5055,32012),
                                                ('Skill_SummonSlime2',5062,32017)]:
        signals = [s for s in actions[action_name]['signals'] if s['type']==15 and s['u32'][0]==projectile_id]
        assert len(signals)==1
        weapon = weapons[projectile_id]
        file = files[weapon['_ActName']]
        path = RESOURCE/'weapon'/file['_FileName'].lower()
        mark(path)
        monster_creators = [dict(action=a['name'], offset=s['offset'], type=s['type'], template=s['u32'][0])
                            for a in parse_full(path)['actions'] for s in a['signals']
                            if s['type']==48 and s['u32'][0]==template]
        assert len(monster_creators)==1
        routes.append(dict(player_act=str(player), player_action=action_name,
            projectile_signal_offset=signals[0]['offset'], projectile_template=projectile_id,
            weapon_table_row=weapon['RowId'], file_table_row=file,
            child_act=str(path), creates=monster_creators))
    love = [dict(action=a['name'], signal=s) for a in parse_full(physician)['actions']
            for s in a['signals'] if any(v=='Skill_SummonSlime1' for _,v in s['strings'])]
    assert len(love)==1 and love[0]['signal']['type']==29
    # No broad name-based incoming/canonical promotion. These are new explicit
    # candidate routes, not proof that ALL births use the selected route.
    return dict(inputs=inputs, all_act_files=7776, literal_selected_parses=census['parsed_count'],
        typed_creator_count=len(creators), creators=creators, positive_projectile_routes=routes,
        additional_string_reference=love, runtime_changes=False, confirmed_hits_added=0,
        remaining=['type29 invocation semantics and canonical context for the LoveVirus->SummonSlime1 reference',
                   'target-complete incoming Projectile creation/call census for5055/5062; these selected positive edges are not exhaustive',
                   'birth-specific runtime path association if competing paths survive'],
        warning='The generic Hit action in weapon ACTs must not be assigned to skill20073 or any other skill by globally matching its name.')


if __name__=='__main__':
    d=review()
    (OUT/'projectile_review.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    lines=['# 魔法草创建链补查（尚未进入确定性归因）','',
        '本次对旧拆包的7776个ACT逐文件检查目标ID，命中7个文件并完整解析，无解析错误；另核对44份技能/父子关联表和10142份Lua脚本。',
        '找到6个实体创建点，不止此前列出的4个玩家动作。额外2个来自投射物命中后的创建：','',
        '|玩家动作|投射物模板|投射物ACT|生成实体|','|---|---:|---|---:|',
        '|Skill_SummonSlime1|5055|weapon/academic_slime.act|32012|',
        '|Skill_SummonSlime2|5062|weapon/academic_slime2.act|32017|','',
        '这两条正向链经过type15、weapontable_projectile、filetable、武器ACT中的type48逐项对应。',
        '但找到正向路径不代表排除了其他路径，因此还没有把它们升级为确认Hit。', '',
        '另外，爱情病毒动作中有type29记录：数值含4405，字符串指向Skill_SummonSlime1。它是需要进一步解释的引用，不能只凭字符串认定继承爱情病毒，也不能直接认定是完整的新技能调用。',
        '武器ACT的动作名Hit与大量怪物动作同名，初步名称匹配得到的20073等候选不是可靠技能归属。原始普查保留以便追溯，规范化候选见projectile_review.json。','',
        '接下来需补全5055/5062所有投射物入口、解释type29上下文，必要时补出生实例的链路证据。32018、FFFFFFFF空自身技能槽、牺牲指令仍未开放。',
        '本分支没有改运行时规则，没有新增确认Hit。当前已验收覆盖率仍见 ../magma_runtime_v37_20260908/REPORT.md。','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),'utf-8')
    print(json.dumps(dict(new_projectile_routes=len(d['positive_projectile_routes']),new_confirmed_hits=0),ensure_ascii=False))
