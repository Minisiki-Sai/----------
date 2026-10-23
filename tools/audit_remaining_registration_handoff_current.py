"""Render this subtask's evidence/handoff without touching production files."""
from __future__ import annotations
import json
import sys
import importlib.util
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.audit_remaining_registration_packet_decoder import (
    validate_fixtures,decode_extended_resource_registration,ExactTailReplicaTracker,
    decode_candidate_registration,_decode_v576_body,
)
from dataclasses import replace


def load(path):return json.loads((ROOT/path).read_text(encoding='utf8'))


def main():
    sys.stdout.reconfigure(encoding='utf8')
    out=ROOT/'outputs/audit_remaining_registration_packet_20260907'
    resource=load('outputs/audit_remaining_registration_packet_20260907/resource_chains.json')
    sniper_before=load('outputs/audit_remaining_registration_exact_parent_20260907_v1/20260831YEP黑典狙翎.json')
    sniper=load('outputs/audit_remaining_registration_exact_parent_20260907_v2/20260831YEP黑典狙翎.json')
    shadow=load('outputs/audit_remaining_registration_exact_parent_20260907_v3/影识别错误.json')
    ice=load('outputs/audit_remaining_registration_exact_parent_20260907_v3/0905黑龙经典冰灵.json')
    survey=load('outputs/audit_remaining_registration_packet_20260907/sniper_variable_envelopes.json')
    wire=load('outputs/audit_remaining_registration_packet_20260907/ice_parent_conflict_wire.json')
    fixtures=validate_fixtures(ROOT/'outputs/audit_remaining_registration_shifted_20260907')
    extension_tests=Counter();seed=None
    private_protocol=ROOT/'outputs/damage_attribution_candidate_v15_20260907/protocol_v576.py'
    spec=importlib.util.spec_from_file_location('remaining_registration_private_protocol_probe',private_protocol)
    private=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=private;spec.loader.exec_module(private)
    for r in survey['rows']:
        if r['length']!=120:continue
        p=bytes.fromhex(r['wire_hex'])
        assert private.decode_v576_damage_object_registration(p,r['direction']) is None
        extension_tests['private_v15_rejects_real_120_packet']+=1
        row=decode_extended_resource_registration(p,allow_missing_resource=True)
        assert row is not None and (row.source_actor_id,row.damage_object_id,row.projectile_template_id)==(r['owner'],r['obj'],r['template'])
        extension_tests['all_120_decoded_identity_equal']+=1
        strict=decode_extended_resource_registration(p)
        extension_tests['strict_resource_equal' if strict else 'missing_resource_sentinel_opt_in']+=1
        if seed is None:seed=(p,row)
    p,row=seed;b=_decode_v576_body(p,row.xor_phase);o=row.owner_offset
    for name,at in [('five_zero',o+4),('owner_repeat',o+45),('eight_ff',o+53),
                    ('four_ff_after_extension',o+70),('repeat_resource',o+78)]:
        bad=bytearray(b);bad[at]^=1
        assert decode_extended_resource_registration(_decode_v576_body(bytes(bad),row.xor_phase)) is None
        extension_tests['negative_'+name]+=1
    track=ExactTailReplicaTracker()
    assert track.observe(row,scene=0,timestamp=1)==(1,False)
    different=replace(row,normalized_tail_hex='different-registration-same-slot')
    assert track.observe(different,scene=0,timestamp=1.05)==(2,False)
    assert track.observe(row,scene=0,timestamp=.9999)==(1,True)
    assert track.slots[(0,row.source_actor_id,row.damage_object_id)][2]==2
    extension_tests['late_old_copy_no_slot_revive']=1
    conflicts=[c for c in ice['parent_checks'] if c['independent_child_conflicts']]
    representatives=[]
    for doc in (shadow,ice):
        byid={r['id']:r for r in doc['generations']}
        selected=(doc['parent_checks'][:2] if doc is shadow else conflicts)
        for c in selected:
            representatives.append({'capture':doc['capture'],'binding':c,
                                    'parent_registration':byid[c['parent_generation_id']],
                                    'child_registration':byid[c['child_generation_id']]})
    paired={'capture':sniper['capture'],'raw_direct37_before':sniper_before['stats']['raw_direct37_rows'],
            'raw_direct37_after':sniper['stats']['raw_direct37_rows'],
            'raw_event_digest_identical':sniper_before['raw_direct_digest']==sniper['raw_direct_digest'],
            'runtime_sources_unchanged':all(d['core_unchanged'] for d in (sniper_before,sniper,shadow,ice)),
            'note':'Same-input diagnostic candidate on/off only, NOT a replacement for main production paired scoring.'}
    assert paired['raw_event_digest_identical'] and paired['runtime_sources_unchanged']
    summary={'pure_decoder':str(ROOT/'tools/audit_remaining_registration_packet_decoder.py'),
             'fixtures':fixtures,'extended120_tests':extension_tests,'paired':paired,
             'shadow_parent_generations':len(shadow['parent_checks']),
             'shadow_parent_status':shadow['parent_status'],
             'shadow_parent_raw_hits':sum(r['child_raw_hits'] for r in shadow['parent_checks']),
             'shadow_parent_report_dedup_hits':sum(r['child_unique_hits'] for r in shadow['parent_checks']),
             'ice_parent_generations':len(ice['parent_checks']),'ice_conflicts':conflicts,
             'representatives':representatives,'conflict_raw_packets':wire['selected_packets'],
             'sniper_groups':{k:v for k,v in sniper['groups'].items() if k.startswith('resource_extra13')},
             'static_ACT_chain_report':str(out/'resource_chains.json'),
             'decisions':{'six_layouts':'decode structural identity; no per-template fixed anchors required when complete unique typed ACT chain is independently established',
                          'parent_field':'no full-ID/owner/current-generation counterexample in 739 deduplicated parent records; not an unconditional skill inheritance proof',
                          '3021':'two independent fixed-slot conflicts with inherited2353; reject universal parent skill inheritance',
                          '5708_children':'603 exact generations/874 raw direct37, full ACT edge verified; no fixed-slot anchors, conditional integration only with exact parent and conflict invalidation',
                          '120':'2603 identity registrations; strict repeat-resource subset2528; 75 sentinel variants require separate opt-in; known2002/11084 map via typed ACT to1207',
                          '92_5000':'still no named skill assignment'}}
    (out/'handoff.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    lines=['# 注册布局审计交接：最终阶段结论','',
           '只改本子任务 tools/audit_remaining_registration_* 和 outputs；共享 runtime 未改。所有重放串行、最多一个进程。',
           '', '## 1. 最重要的新发现：狙翎不是114/117，而是120字节','',
           '`20260831YEP黑典狙翎.dpscap`：O=10，phase=12，wire signature=0c 6d 75 00。',
           '', '已直接调用私有v15解码器验证：2603条真实120字节注册全部返回None；新诊断分支全部解出且owner/object/template完全一致。v15入口支持107和114/117/112/119/122，不包含120，所以65.82%不变并不能排除这个结构漏注册根因。',
           '', '| 模板 | 注册条数 | 同代 exact-object 原始direct37 | fixed-slot | 静态链 |',
           '|---|---:|---:|---:|---|',
           '|2002|2087|3362|0|1207/1306 → typed15 → 2002 → canonical1207|',
           '|11084|516|554|0|1306 → explicit ACT continuation → typed15 → 11084 → canonical1207|',
           '', '合计3916条为原始direct37诊断影响，**不是主线新增确定Hit计数**，不能直接加到65.82%。同输入开/关120诊断后33319条原始Hit及逐条摘要完全不变。主线after.json里2935条是按原猜测技能分桶，口径不同。',
           '', '独立重新读取7个ACT文件、DNT SkillLevel、Projectile和FileTable，验证22条链；完整字节偏移和文件哈希见resource_chains.json。不是靠当前伤害名称倒推。',
           '', '120-byte在O+61插入13字节：9字节语义未定 + O+70..73的4FF；原float移到O+74、resource_repeat移到O+78。2528条重复资源相等，另75条11084为0xFFFF哨兵（对应112原始Hit）。默认严格解码不接受哨兵；需显式allow_missing_resource=True，且绝不能把哨兵说成资源相等。',
           '', '## 2. 六种布局及字段','',
           '|长度|owner O|唯一phase|分支|', '|---:|---:|---:|---|',
           '|107|10|12|resource|','|114|17|6|resource|','|117|20|9|resource|',
           '|112|10|11|parent|','|119|17|5|parent|','|122|20|8|parent|',
           '', '以下偏移相对O，均little-endian；完整包全13相位只允许一个通过，且必须符合上表相位。',
           '', '|字段|resource 107/114/117|parent 112/119/122|',
           '|---|---|---|',
           '|owner|+0 u32，非0/FFFFFFFF|相同|',
           '|五零|+4..8|相同|',
           '|object_id|+9 u32，1..FFFFFF|相同|',
           '|Projectile|+13 u32；DNT存在性独立可选守卫|相同|',
           '|不赋语义区|+17..44，共28bytes|相同|',
           '|重复owner|+45 u32 = +0|相同|',
           '|资源/父|+49 u32：低16资源、高16序号|+49 byte=0；+50 u32完整parent ID|',
           '|父分支word|无|+54 u32：低16=1、高16序号；不冒充资源号|',
           '|8FF|+53..60|+58..65|',
           '|标量原字节|+61..64|+66..69|',
           '|资源尾字段|+65 u32 = 资源且非0|+70 u32，1..FFFF，与分支word不要求相等|',
           '|尾部总长|97字节|102字节|',
           '', '留存4226条全部owner/object/template保持一致；负向字段破坏、错误长度、相位、未知模板拒绝测试通过。此数仅指本子任务留存fixture，不与主线4256口径混算。',
           '', '## 3. 重复副本与代际：必须容许时间微幅倒退','',
           '影包117/114分别650条，122/119分别614条；完全相同尾部跨封装复制，但文件包序后到的副本捕获时间可略早。例：119 p17870 t1788695892.935182，122 p17879 t1788695892.935088，是同一尾部而非新对象。',
           '', '修正为abs(副本时间差)<=250ms、同scene+owner+完整object+相同尾部后：resource638代、parent603代（5709:300、5737:303）。旧884父记录数包含时间倒退造成的重复建代，不能再作为独立实例数。',
           '', '每个新不同注册无论模板是否已知都使旧代失效；迟到的旧尾部复制只记到历史代，不能复活当前slot。包序用于先后判断，时间仅作存活/副本拒绝边界，不选择最近模板。',
           '', '## 4. 完整父ID的结论及独立反例','',
           '去重后影603/603、冰灵136/136均有同owner、完整parentObjectId、当前代际、已确定父模板、ACT child边。**这739条未发现完整父ID字段匹配反例。**',
           '', '但是“父技能自动继承”有两条独立反例。如下均为0905黑龙经典冰灵，owner=1337770664：',
           '', '|父包/ID/代|子注册包/ID|父模板/技能|子Hit包|固定槽skill|伤害|',
           '|---|---|---|---:|---:|---:|',
           '|218071 / 101918 / 3|219009 / 102046|3023 / 2353|219027|2207|91136|',
           '|230355 / 102809 / 2|232251 / 102941|3023 / 2353|232261|2207|116736|',
           '', '两父龄分别5.401412s和5.169191s，均未过8s且无中间同槽替代；child模板3021。独立槽位+102的LE字节9f080000=2207，父静态链是冰柱喷射2353；2207为火鸟，非同canonical。原始包及完整generation证据见handoff.json。父本体在这两代没有fixed-slot Hit，因此不能宣称父也有fixed双重佐证。',
           '', '影5708→5709/5737有603代、874原始Hit（本报告中继身份去重468），fixed-slot0。完整资源链已独立验证，可按用户授权以已确定父代+exactID+静态边作为候选集成依据，但仍须保留固定槽优先与冲突作废；不声称有独立fixed样本。',
           '', '## 5. 集成建议','',
           '1. 优先接入120-byte已确定2002/11084的严格重复资源分支，这是新狙翎控制包的主要缺口。75条FFFF哨兵另开显式分支审查。',
           '2. 114/117可以提取完整注册身份，已完整确定ACT链的模板无需逐个等待fixed-slot；未知模板只作生命周期失效，不赋技能。',
           '3. 112/119/122可提取full parent ID；3021不准仅因父ID/ACT边成立就一律继承2353。两个冲突应加入私有candidate paired反例。',
           '4. 同封装/跨封装副本要容许微幅倒退，并禁止迟到旧副本复活旧代。',
           '5. 92和5000维持未知具名技能；本次没有改变此结论。',
           '', '本子任务未修改或重算正式评分；主线v15 paired才决定最终上线后的确定Hit增量。']
    (out/'HANDOFF.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps({'report':str(out/'HANDOFF.md'),'parent_generations':len(shadow['parent_checks'])+len(ice['parent_checks']),
                      'fixed_conflicts':len(conflicts),'120tests':extension_tests,'paired':paired},ensure_ascii=False))


if __name__=='__main__':main()
