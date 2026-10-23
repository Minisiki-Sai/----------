"""Render the read-only registration evidence handoff; no replay."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SERVER=ROOT/'outputs/audit_remaining_registration_server_20260907_v2'
SHIFTED=ROOT/'outputs/audit_remaining_registration_shifted_20260907'
OUT=ROOT/'outputs/audit_remaining_registration_handoff_20260907'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    reports=[load(p) for p in SERVER.glob('*.json') if p.name!='summary.json']
    summary=load(SERVER/'summary.json')
    shifted=load(SHIFTED/'影识别错误.json')
    control=load(SHIFTED/'0905黑龙经典冰灵.json')
    compact=[]
    for r in summary['candidate_mappings']:
        if r['gap_hits']:
            fresh=sum(i['gap_hits'] for rep in reports for i in rep['strict_instances']
                      if i['marker']==r['marker'] and i['template']==r['template']
                      and i['max_gap_age_ms']<=8000)
            compact.append({k:v for k,v in r.items() if not k.startswith('representative')} | {'fresh_8s_gap_hit_lower_bound':fresh})
    shape_rows=[]
    for kind in ('repeated_resource','extended_parent_candidate'):
        rs=[r for r in shifted['instances'] if r['kind']==kind]
        exact={ (r['gap_example']['p'],r['owner'],r['obj'],r['gap_example']['damage'])
                for r in rs if r['gap_example_fresh_exact_match']}
        shape_rows.append({'kind':kind,'registrations_after_replica_collapse':len(rs),
                           'fresh_raw_hit_rows':sum(r['fresh_raw_hit_rows'] for r in rs),
                           'confirmed_gap_event_examples':len(exact),
                           'fixed_slot_anchors':sum(sum(r['fixed_slot_skill_counts'].values()) for r in rs),
                           'example':next(r for r in rs if r['gap_example_fresh_exact_match'] and r['replicas'])})
    paired_parent=sum(bool(r['parent_records']) for r in shifted['parent_checks'])
    common_hashes=reports[0]['core_hashes_before']
    drift=[{'capture':Path(r['capture']).name,
            'files_changed_during_replay':[k for k,v in r['core_hashes_before'].items() if r['core_hashes_after'][k]!=v],
            'same_disk_start_as_first':r['core_hashes_before']==common_hashes}
           for r in reports]
    age_checks=[]
    for marker in ('09 6E 00','09 6A 00'):
        rs=[(rep,r) for rep in reports for r in rep['strict_instances']
            if r['marker']==marker and r['template']==4024 and r['gap_hits']]
        examples=[{'capture':Path(rep['capture']).name,'registration_packet':r['p'],
                   'hit_packet':g['p'],'owner':r['owner'],'object':r['obj'],
                   'registration_age_ms':round((g['t']-r['t'])*1000)}
                  for rep,r in rs for g in r['gap_examples']]
        assert len(examples)==sum(r['gap_hits'] for _,r in rs), '4024 must be checked hit-by-hit'
        age_checks.append({'marker':marker,'template':4024,'already_supported':True,
                           'gap_hits':len(examples),'every_hit_age_over_120s':all(e['registration_age_ms']>120000 for e in examples),
                           'new_safe_hit_gain':0,'examples':examples})
    data={'scope':'read-only registration structure, not a production patch or scoring benchmark',
          'new_safe_server_skill_mappings':[], 'reports':len(reports),
          'server_candidate_rows':compact,'shifted_layout_rows':shape_rows,
          '4024_individual_hit_recheck':age_checks,
          'weapon_normal_attack_parent_evidence':{'92':[268465691],'5000':[11017,11024,31381,31382],
              'source':'mainline supplied ACT resource identities; numeric edges verified in generated_attack_instance_specs_v576.py',
              'forbidden':'do not map to a named skill or choose a skill by profession'},
          'parent_records':len(shifted['parent_checks']),'parent_records_with_prior_same_owner_object':paired_parent,
          'core_disk_signature_observations':drift,
          'same_input_paired_skill_or_score_gain':'NOT MEASURED: no production rule changed',
          'note':'v5-v12 metadata and mixed runtime baselines are never compared as a gain metric'}
    (OUT/'handoff.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=[
        '# 未解析对象注册变体审计交接', '',
        '**可直接集成的新服务端 skill 映射：0。发现可复核的客户端注册封装结构与候选父对象字段；须补独立技能真值后再决定归因接入。**','',
        '本轮完成11份代表抓包的注册/缺口审计，另对其中6份进行原始包结构复核。所有重放串行，最多一个本分支重放进程。只新增指定命名的tools与outputs；protocol/core_decoder/damage_object_origins未由本分支编辑。','',
        '独立真值只来自同一capture、完整owner+object的direct37固定技能结果槽（D+102/D+114，并检查下一条direct37边界）。当前显示技能名、最近动作、result-group不作真值。raw Hit数不代表去重后的全队评分Hit；不根据v5-v12 metadata宣称提升。','',
        '## 1. 服务端布局复核','',
        '以解码payload中marker起点M为基准，整数小端，偏移含payload头：','',
        '| 字段 | 偏移 | 守卫 |','|---|---|---|',
        '| owner | M-4，u32 | 完整匹配direct37 D+8 |',
        '| marker | M，3字节 | 09 xx 00 |',
        '| zero guard | M+3，5字节 | 全0 |',
        '| object | M+8，u32 | 完整匹配同owner direct37 D+13 |',
        '| template | M+12，u32 | Projectile.RowId |',
        '| repeated owner | M+44，u32 | 与M-4相同 |','',
        '本轮反查未确认新的服务端偏移布局。放宽重复owner守卫的扫描，也未产生与本轮目标缺口或独立真值相连的有效候选。此结论限定在本轮输入与扫描范围，不是证明所有协议变体均已找到。','',
        '| marker / template | 资源 | 本轮关联缺口Hit | ≤8s保守下界 | 本轮fixed槽真值 | 结论 |',
        '|---|---|---:|---:|---|---|',
    ]
    chosen={("09 6A 00",92),("09 66 00",92),("09 6A 00",5000),("09 66 00",5000),("09 6A 00",11056),('09 5D 00',4018),('09 6E 00',4024)}
    for r in compact:
        if (r['marker'],r['template']) not in chosen:continue
        verdict=('多技能冲突，拒绝静态单技能映射' if r['template']==92 else
                 '缺少独立技能真值' if r['template'] in (5000,11056) else
                 '已有布局；陈旧关联不算新增')
        lines.append(f"| {r['marker']} / {r['template']} | {r['act']} | {r['gap_hits']} | {r['fresh_8s_gap_hit_lower_bound']} | {r['fixed_slot_truth'] or '0'} | {verdict} |")
    lines.extend([
        '', '≤8s仅是排除陈旧owner+object复用的保守守卫，不依据相邻动作推断技能。对同一注册记录混合新鲜/陈旧Hit的组全部从下界剔除，所以不将此数当作完整新增覆盖。','',
        '- 魔羽：09 6A/66 +92分别有1285/68个缺口关联。本轮09 6A+92的7个固定槽锚点为1353，但既有205包审计有197行、8种skill；09 66+92有19行、7种skill。不能因为本轮魔羽只观察到1353就跨职业泛化。历史审计仅作冲突反例，不作为当前coverage基线。',
        '- 药剂师：5000资源为Academic_NormalBubble.act，两个marker共839个关联缺口，836个进入≤8s保守下界，fixed槽锚点0。当前喷射/疾病/爱情病毒等显示名称不能用来给这个模板投票。11056也缺独立固定槽真值。',
        '- 雷神：本轮所有已关联到严格09布局的“缺模板”记录最大Hit年龄均超过206秒，最高1827.615秒。已知模板的古老注册不能充当新注册证据，不应扩大生命周期。未关联的其余缺口继续保留未知。',
        '- 影：影木桩测试补查方向未知后得到192行raw direct37、固定槽锚点0、新严格注册0；没有证据按当前显示名称补技能。',
        '- 暗：本轮代表样本未得到新的服务端注册候选。v12中暗的主要缺口有已解析template5826，属于模板/技能溯源问题，而不是本分支优先的“object存在、template未解析”类型；不宣称暗已解决。','',
        '## 2. 新发现：107字节客户端注册的封装形态','',
        '“影识别错误”存在114/117字节记录（client_to_server/方向未知）。定义O为owner起点：114字节时O=17；117字节时O=20。两种封装可解出逐字节相同的O至包尾内容。**这是客户端/中继封装发现，不冒充服务端marker授权。**','',
        '| 字段 | 相对O偏移 | 守卫/说明 |','|---|---|---|',
        '| owner | +0，u32 | 非零且重复相同 |',
        '| zero guard | +4..+8 | 5个0 |',
        '| object | +9，u32 | 1..0xFFFFFF |',
        '| template | +13，u32 | 命中Projectile目录 |',
        '| repeated owner | +45，u32 | 等于O+0 |',
        '| resource word | +49，u32 | 低16位resource非0，高16位sequence |',
        '| repeated resource | +65，u32 | 等于resource word低16位 |',
        '| payload长度 | O+97 | 与原107字节owner=10结构相同 |','',
        '对应原始107字节注册，114/117外层分别多7/10字节。不能仅凭长度剪包；必须验证重复owner、resource、零区、template和运行期object范围，且对相同解码尾部的中继副本去重。','',
        '## 3. 扩展形态：119/122字节与候选父对象','',
        '定义O同上（17/20），共同字段owner/object/template/repeated owner仍为+0/+9/+13/+45。长度为O+102。','',
        '| 扩展字段 | 相对O偏移 | 本轮观测 |','|---|---|---|',
        '| 分支字节 | +49，u8 | 0 |',
        '| 候选父object | +50，u32 | 同owner的先前5708对象 |',
        '| 分支后续字 | +54，u32 | 保留原始值，未赋予确定语义 |',
        '| sentinel | +58..+65 | 8个FF |','',
        f'在“影识别错误”884条扩展实例中，{paired_parent}条找到≤8s内先前同owner的候选父object记录，父模板均为5708，子模板为5709或5737。33条未连接到父记录。不能把没有父记录的实例归到最近动作。parent关联本身也没有fixed技能锚点，因此目前是对象链结构证据，非已确定skill。',
        '独立控制样本“0905黑龙经典冰灵”在未封装112字节记录中也出现同布局：136条扩展实例、112条连接到3023→3021；仅2条子对象fixed槽2207、父对象fixed槽0，仍不足以单独确定整个共享子模板的技能。','',
        '## 4. 影响Hit与独立真值边界','',
        '| “影识别错误”布局 | 去重后注册实例 | ≤8s raw Hit行 | 精确匹配缺口事件的代表Hit下界 | fixed技能锚点 |',
        '|---|---:|---:|---:|---:|',
    ])
    for row in shape_rows:
        lines.append(f"| {row['kind']} | {row['registrations_after_replica_collapse']} | {row['fresh_raw_hit_rows']} | {row['confirmed_gap_event_examples']} | {row['fixed_slot_anchors']} |")
    lines.extend([
        '', '806条代表缺口Hit逐条验证packet+owner+object+damage一致，且位于候选对象新鲜区间；每个缺口对象只取一条证据。它们是本轮可定位的模板识别问题下界，**不是806条已恢复确定性技能归因**。2485行raw Hit含中继重复方向/相同Hit副本，不能与v12全队Hit相加或直接计算提升。',
        '936+884条实例是在相同解码尾部、250ms内的封装副本合并后计数；仍可能包含对象刷新或跨阶段重复，不能解释成1820次独立施放。','',
        '## 5. 代表包（可直接核对）','',
        '包号是1-based逻辑decoder.feed_events调用序号，不是.dpscap原始帧号；偏移是0-based解码payload。每个实例JSON同时保存wire_payload_hex与decoded_payload_hex。','',
    ])
    for row in shape_rows:
        r=row['example'];h=r['fresh_hit_examples'][0]
        lines.extend([f"### {row['kind']}",'',
                      f"影识别错误：包{r['p']}（相对首逻辑包{r['capture_seconds']}s），len={r['length']}，phase={r['phase']}，O={r['offset']}；owner=0x{r['owner']:08X}，object={r['obj']}，template={r['template']}。",
                      f"副本：{r['replicas']}。对应Hit包{h['p']}，D={h['m']}，damage={h['damage']}。fixed skill=0，当前显示名不参与真值。",
                      f"resource={r['resource']}，sequence={r['sequence']}，parent_candidate={r['parent_candidate']}。",'',
                      '从O开始的完整解码尾部：','', '```text',r['normalized_tail_hex'],'```',''])
    lines.extend([
        '## 6. 集成建议与后续验证','',
        '1. 保持现有09子类型白名单；本轮没有新的安全服务端marker+template→skill映射可直接添加。',
        '2. 先在传输/注册审计分支验证114/117封装的结构归一与副本合并，只暴露已通过守卫的owner+object+template，不直接强判skill。119/122父字段独立保留为候选，必须要求完整同owner父object与已验证来源。',
        '3. 为template92/5000补可独立给出技能/普通攻击身份的固定字段或明确父对象证据；不要使用当前猜测名称训练映射。',
        '4. 集成后用相同capture列表、同版本代码的before/after paired逐Hit守恒；分别比较总伤害、raw Hit多重集、确定性skill冲突、alias归一、模板可见性。未运行生产补丁，因此本轮不报告技能或评分提升。','',
        '### 主线补充后的最终合入结论','',
        '**新增可安全合入的伤害归因规则仍为0；可合入审计脚本、原始结构诊断和否定样本，尚不能合入新的确定性skill映射。**','',
        '- 92父边268465691（Ar_Crossbow_D01.act）；5000父边11017/11024/31381/31382（Ac_BubbleLauncher_D01_A.act）。主线提供ACT身份，本分支已核对生成目录中的数值父子边。属于装备普通攻击模板；不得硬塞具名技能，也不得只按职业选择。',
        '- **4024逐Hit复核已完成**：09 6E+4024的39条，注册年龄228.101～1537.416秒；09 6A+4024的2条，1540.458～1826.577秒。全部超过120秒，而且两种marker已支持，安全新增Hit=0。handoff.json的4024_individual_hit_recheck保存全部41条定位，不能把summary的39当作新variant收益。',
        '- 11056仅6条新鲜缺口Hit、独立fixed槽0；不合入确定性映射。',
        '- 114/117注册尾部重复owner+resource结构可用于诊断和后续transport归一的验证，但本样本独立fixed槽0；若要影响归因，须先完成主线同输入paired并保留既有skill证据门槛。119/122父字段同样先保留候选，不据最近动作选技能。','',
        '本轮执行期间主线修改了部分核心文件；各captureJSON保留开始/结束磁盘SHA256。跨轮metadata或混合基线覆盖率不可比较，原始字节证据可独立重验。',
        f"检测到本轮磁盘变更的文件：{sorted({k for r in drift for k in r['files_changed_during_replay']})}。本分支仅写自己的tools与outputs。",'',
        '## 产物','',
        '- tools/audit_remaining_registration_server_variants.py：方向显式保留的09布局审计与完整owner+object反查。',
        '- tools/audit_remaining_registration_shifted_envelopes.py：原始包114/117、119/122结构/独立锚点审计。',
        '- tools/audit_remaining_registration_report.py：从已生成JSON重建本报告，无重放。',
        '- outputs/audit_remaining_registration_server_20260907_v2：11份完整证据及summary.json。',
        '- outputs/audit_remaining_registration_shifted_20260907：6份原始包交叉检查，含完整hex。',
        '- handoff.json：紧凑可机读交接，包含明确不支持接入的候选、影响Hit下界和代表字节。','',
    ])
    (OUT/'FINAL_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(str(OUT/'FINAL_REPORT.md'))


if __name__=='__main__':main()
