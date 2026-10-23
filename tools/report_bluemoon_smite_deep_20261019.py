"""Build a local, self-contained per-hit audit with explicit uncertainty boundaries."""
import hashlib
import html
import json
import statistics
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from tools.build_dungeon_map_catalog import parse_dnt, find_resource_file
from tools.build_attack_instance_catalog import parse_act
from tools.analyze_client_skill_hits import group_damage_phases
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body_uncached

ROOT=Path('outputs/verification/bluemoon_smite_deep_20261019')
RESOURCE=Path(r'D:\DragonNest\Newborn\Resource_unpacked_all_20260916_update')
H=html.escape


def main():
    ledger=json.loads((ROOT/'smite_ledger.json').read_text(encoding='utf8'))
    wire=json.loads((ROOT/'wire_census.json').read_text(encoding='utf8'))
    packets=json.loads((ROOT/'smite_wire_packets.json').read_text(encoding='utf8'))
    packet_by_hash={p['wire_sha256']:p for p in packets}
    # Re-read amount/source/target/critical at the raw fixed record offsets,
    # independently of scorer, attribution, and the aggregate snapshot totals.
    verified=[]
    for row in ledger['rows']:
        for hit in row['raw_hits']:
            pkt=packet_by_hash[hit['wire_packet_sha256']]
            raw=bytes.fromhex(pkt['wire_hex'])
            b=_decode_v576_body_uncached(raw,(11-raw[5])%13)
            pos=hit['wire_record_offset']
            damage=int.from_bytes(b[pos+23:pos+31],'little')
            source=int.from_bytes(b[pos+8:pos+12],'little')
            target=int.from_bytes(b[pos-4:pos],'little')
            crit=bool(b[pos+39]&1)
            assert damage==hit['amount'],(row['index'],damage,hit['amount'])
            assert source==hit['wire_source_actor_id']==0x4D78152C
            assert target==hit['wire_target_actor_id']
            assert crit==hit['critical']==hit['wire_critical']
            hit['frame']=pkt['frame']; hit['wire_verified']=True
            verified.append(dict(cast=row['index'],seconds=hit['seconds'],frame=pkt['frame'],
                amount=damage,critical=crit,record_offset=pos,sha256=hit['wire_packet_sha256']))
            # Full seven-hit casts have an unambiguous temporal phase ordering.
            # In incomplete casts do NOT shift the missing phase onto later Hits.
            if len(row['raw_hits'])==7:
                hit['phase_label']=['物理100%','魔法25%','魔法25%','魔法25%','魔法25%','物理40%','魔法40%'][hit['ordinal']-1]
            else:
                hit['phase_label']='缺段，不能仅按顺序指定通道'
    assert len(verified)==279
    assert sum(r['raw_sum']for r in ledger['rows'])==ledger['raw_total']
    assert abs(sum((r['scoring_row']or{}).get('power',0)for r in ledger['rows'])-ledger['scored_skill']['actual_power'])<1e-5
    assert abs(sum(r['power']for r in ledger['theory'])-ledger['scored_skill']['theory_power'])<1e-5

    strings={}
    for file in sorted(RESOURCE.glob('Resource*/resource/uistring/uistring.xml')):
        if file.stat().st_size:
            strings.update({e.get('mid'):e.text for e in ET.parse(file).iter('message')})
    level_file=find_resource_file(RESOURCE,'resource/ext/skillleveltable_charactercleric.dnt')
    level_rows=parse_dnt(level_file)
    judge=next(r for r in level_rows if r['RowId']==1073956291)
    act_file=find_resource_file(RESOURCE,'resource/char/player/cleric/cleric_skill_2st_crusades.act')
    action=next(a for a in parse_act(act_file).actions if a.name=='Skill_Smite_EX')
    resource=dict(level_file=str(level_file),level_file_sha256=hashlib.sha256(level_file.read_bytes()).hexdigest(),
        row=judge,description=strings[str(judge['_SkillExplanationID'])],
        act_file=str(act_file),act_sha256=hashlib.sha256(act_file.read_bytes()).hexdigest(),
        action=action.name,phases=group_damage_phases(action),
        caveat='资源为本机当前拆包；原包相同技能、效果类与参数可交叉印证，但不宣称等同于10月11日客户端全文件。')

    statuses=wire['statuses']; unique={}
    for x in statuses:
        unique[(x['wire_sha256'],x['offset'],x['phase'])]=x
    instance_map={}
    for x in unique.values():
        b=bytes.fromhex(x['body_hex']);q=x['offset']+21+x['size']
        x['instance_id']=int.from_bytes(b[q:q+4],'little')
        instance_map[x['instance_id']]=x
    actors=json.loads((ROOT/'actor_packets.json').read_text(encoding='utf8'))
    removes=[]; refs=[]
    prefix=(0x4D78152C).to_bytes(4,'little')+bytes.fromhex('150500')
    for x in actors:
        b=bytes.fromhex(x['body_hex']);pos=b.find(prefix)
        while pos>=0:
            iid=int.from_bytes(b[pos+7:pos+11],'little')
            if iid in instance_map:
                apply=instance_map[iid]
                removes.append(dict(t=x['t'],frame=x['frame'],instance_id=iid,
                    apply=apply['t'],effect_class=apply['effect_class'],age=x['t']-apply['t']))
            pos=b.find(prefix,pos+1)
        pos=b.find(b'3351,0,0,1500')
        while pos>=0:
            refs.append(dict(t=x['t'],frame=x['frame'],offset=pos,context=b[max(9,pos-40):pos+35].hex()))
            pos=b.find(b'3351,0,0,1500',pos+1)

    def compare(i,j):
        a,b=ledger['rows'][i-1],ledger['rows'][j-1]
        def signature(hit):
            buffs=sorted((x['skill'],x['key'],str(x['value']),x['source']) for x in hit['buffs']
                         if x['skill']not in ('审判力量','精神爆发','突袭','神秘冥想'))
            debuffs=sorted((x['skill'],x['key'],str(x['value']),x['source'])for x in hit['debuffs']if x['effective'])
            return buffs,debuffs
        def crit_magic(row):
            return [x['amount'] for x in row['raw_hits'][1:5]if x['critical']]
        am,bm=crit_magic(a),crit_magic(b)
        return dict(a=i,b=j,first_hit_ratio=b['raw_hits'][0]['amount']/a['raw_hits'][0]['amount'],
            first_hit_same_critical=a['raw_hits'][0]['critical']==b['raw_hits'][0]['critical'],
            first_hit_same_target=a['raw_hits'][0]['target_id']==b['raw_hits'][0]['target_id'],
            first_hit_same_known_damage_state_except_judgement=signature(a['raw_hits'][0])==signature(b['raw_hits'][0]),
            critical_magic_a=am,critical_magic_b=bm,
            critical_magic_ratio=statistics.mean(bm)/statistics.mean(am),
            caveat='只控制已识别状态；攻击浮动、未知状态、技能内Debuff变化和服务器快照仍可能影响。魔法均值仅使用中间四段中的暴击Hit，不把普通Hit直接混入。')
    pairs=[compare(10,11),compare(26,28)]
    notes={
        5:'刚过当前记录的108.521秒到期点；本次108.705秒释放。下一次同目标重击的减抗/增伤明显不同，无法仅凭伤害判定。',
        11:'与188.358秒同Boss、同已知主要伤害状态相比：物理首Hit低约11.9%，但魔法25%暴击段均值完全相同（均681600）。伤害不能证明这里确实失去审判；仍可能在，也可能因浮动等原因相近。',
        12:'霜之使徒的幻影；该实体两次重击均被标为无审判，没有同目标已确认有审判的对照。不能跨Boss凭伤害高低定论。',
        13:'与228.897秒同目标，但目标减抗/物魔增伤改变；两次都没有可确认的审判对照，仍待证。',
        18:'与308.509/323.440秒相比，光明之呼吸、无情打击等恢复；这些变化能抵消审判减少的伤害，不能单独判断。',
        19:'与351.184秒同目标、状态近似，都是当前无审判标签；缺少同环境已知有审判样本，无法反证。',
        28:'与509.267秒同Boss、同已知主要伤害状态相比，物理首Hit约低16.3%，魔法25%暴击段均值约低48.4%。与失去审判及其力智转攻收益相容，但不能唯一归因为审判。',
        35:'此前708.457秒存在致死HP证据，此时判定光环、无情打击、鸡尾酒等也未恢复；低伤害无法单独证明审判缺失。',
        36:'打中黑龙崇拜图腾，不是其他对照中的同一Boss实体；不能用Boss重击值反推。',
        37:'与869.680秒相比同时有化身、队友Buff和Boss减光抗差异；不能单独逆推出审判。',
        38:'随后869.680秒重击同时进入化身，并更换减抗/增伤组合；伤害上升不等于只补了审判。',
        41:'此前化身已经结束，光明之呼吸等亦缺少识别；Boss增伤/减抗变化，不能单独反推审判。',
    }
    summary=dict(raw_packet_counts=wire['counts'],raw_judgement_status_records=len(statuses),
        unique_judgement_status_records=len(unique),all_found_statuses_current_parser_accepts=all(x['current_parser_accepts']for x in statuses),
        buff_pairs=18,candidates=len(wire['candidates']),raw_hits_verified=len(verified),
        casts=len(ledger['rows']),landed_casts=sum(bool(x['raw_hits'])for x in ledger['rows']),
        scored_casts=sum(x['scoring_row']is not None for x in ledger['rows']),
        currently_no_judgement_casts=sum(not x['recognized_judgement']for x in ledger['rows']),
        judgement_cooldown_parameter_records=len(refs),pair_comparisons=pairs,notes=notes,
        conclusion='未在原包找到被当前过滤条件拒绝的3351/164/165状态应用；这不等于证明玩家长时间没放。部分伤害样本不支持把缺失标签直接当事实，另有样本与增益下降相容。')
    (ROOT/'evidence_summary.json').write_text(json.dumps(dict(summary=summary,resources=resource,
        wire_verified_hits=verified,removal_records=removes,cooldown_parameter_wire_records=refs),ensure_ascii=False,indent=2),encoding='utf8')

    def number(v):return '—' if v is None else f'{v/10000:,.2f}'
    def buffs_text(items):
        return '；'.join(dict.fromkeys(f"{x['skill']}"+(f" ({x['value']:g})" if isinstance(x['value'],(int,float))else '') for x in items)) or '未识别到所列增益'
    def debuff_text(items):
        return '；'.join(dict.fromkeys(f"{x['skill']}·{x['key']}"+(f" {x['value']:g}"if isinstance(x['value'],(int,float))else '')+(' [被压制]'if not x['effective']else '')for x in items)) or '此目标此刻无已识别Debuff记录（不代表游戏中绝对没有）'
    def table(head,rows):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+H(h)+'</th>'for h in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>'for v in row)+'</tr>'for row in rows)+'</tbody></table></div>'
    out=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BlueMoon 重击逐次逐Hit与审判力量复核</title><style>
    body{font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif;background:#f4f8fa;color:#203740;margin:0;padding:26px}main{max-width:1500px;margin:auto}h1{font-size:27px}h2{margin-top:32px}p{max-width:1150px}.card,details{background:white;border:1px solid #d7e4e7;border-radius:8px;padding:16px;margin:12px 0}.warn{background:#fff5dc;padding:15px;border-left:4px solid #b58127}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:8px 10px;border-bottom:1px solid #dce5e8;text-align:left;vertical-align:top}th{background:#e6f1f2;white-space:nowrap}tr:hover{background:#f2f8f9}summary{cursor:pointer;font-weight:650}a{color:#00787f}.muted{color:#526b77}.no{color:#925900;font-weight:600}.yes{color:#087866}code{word-break:break-all}.hit{white-space:nowrap}input{font:inherit;padding:9px;width:360px;max-width:90%;border:1px solid #b8cccf;border-radius:6px}.small{font-size:12px}</style><main>
    <h1>1011硬核 BlueMoon：审判力量与每一次重击复核</h1>
    <p>只读核查，未修改评分、未上传云端、未补造Buff。所有时间为抓包开始后的秒数。伤害表的单位为<strong>万</strong>，可展开查看精确整数。</p>''']
    out.append('<div class="warn">'+H(summary['conclusion'])+'<br>“未识别到”与“确定没有”必须区分。当前60.66%的审判覆盖率是现有状态还原结果，不应直接用来指责玩家漏放。</div>')
    out.append('<h2>1. 重新逐包检查了什么</h2><p>扫描167,289条帧记录，其中150,838条IPv4 UDP消息逐条尝试13个XOR相位；不限定方向、目标、技能白名单、状态持续时间过滤条件。额外检查效果类164/165是否使用别的技能ID。279个原生Hit的金额、来源、目标与暴击位均从原包固定偏移重新核对成功。</p>')
    out.append('<p>找到18次审判施放，38条状态分量记录，去除首包重传后36条＝18组物攻/魔攻转换效果。全部能被现有状态解码器接受，持续字段均40,000ms。检出的57个二进制3351候选包含这些状态、18次施法和1处无关字段偶合；不能把每个字节命中都当技能。</p>')
    out.append('<p>原始消息中另有196处 <code>EffectClass 637 / 3351,0,0,1500</code>，现有快照保留190条对应技能参数事件，来自重击、神圣十字、雷电冲击、审判锤。拆包描述明确其作用是<strong>审判力量冷却减少1.5秒，不是持续时间延长</strong>。原始实例移除包也单独核对；未收到移除包并不能证明定时Buff仍然有效。</p>')
    out.append('<p class="muted">边界：本文件没有额外的3351状态应用证据，不等于能排除抓取前缺包、其他未知状态同步形式或持续时间语义差异。656条常规解析未接收的帧为IPv6，另有15,784条TCP；本次全面XOR复核的范围是IPv4 UDP，未宣称解密所有TCP业务。</p>')
    applies=sorted([x for x in unique.values()if x['effect_class']==164],key=lambda x:x['t'])
    old=json.loads((Path('outputs/verification/crusader_completion_20261019')/'judgement_wire_audit.json').read_text(encoding='utf8'))
    out.append(table(['次序','施放时刻','状态到达','原包帧号','持续字段','实例ID'],[[i+1,f"{old['casts'][i]:.3f}",f"{x['t']:.3f}",x['frame'],'40秒',x['instance_id']]for i,x in enumerate(applies)]))
    out.append('<h2>2. 为什么必须分开物理、魔法与暴击</h2><p>当前拆包的完整重击EX为：第1Hit＝100%物理，第2～5Hit＝各25%魔法，第6Hit＝40%物理，第7Hit＝40%魔法。审判是力智转攻，不能假定对物理与魔法部分都增加同一个百分比。Boss物防/魔防、物理/魔法增伤也分别影响通道。</p><p>下表只给完整7Hit释放按时间顺序标记通道；缺段的释放不硬套顺序。资源为当前拆包，来源路径、SHA-256与实际动作相位保存在 evidence_summary.json。</p>')
    out.append('<h2>3. 两组最有价值的伤害对照</h2><p>这些对照使用同一Boss实体、相同暴击类型，所列主要伤害Buff与目标已识别Debuff相同（除审判）。仍不是控制实验：未知状态和攻击浮动没有被排除。</p>')
    pairrows=[]
    for pair in pairs:
        a,b=[ledger['rows'][pair[k]-1]for k in('a','b')]
        pairrows.append([f"{a['t']:.3f} → {b['t']:.3f}",'有记录 → 无记录',
            number(a['raw_hits'][0]['amount'])+' → '+number(b['raw_hits'][0]['amount']),f"{pair['first_hit_ratio']*100:.2f}%",
            number(statistics.mean(pair['critical_magic_a']))+' → '+number(statistics.mean(pair['critical_magic_b'])),f"{pair['critical_magic_ratio']*100:.3f}%"])
    out.append(table(['释放时间','当前审判标签','物理首Hit（同暴击类型）','后/前','中间魔法25%暴击Hit均值','后/前'],pairrows))
    out.append('<p><strong>188→207秒：</strong>魔法段几乎相同，不足以确认审判消失。<strong>509→546秒：</strong>魔法段明显降低，与失去审判收益相容，但也不能唯一归因。因此不能把12次“无记录”一概认定为玩家没开审判，也不能仅凭伤害把它们全部补成有。</p>')
    out.append('<h2>4. 12次当前未识别审判的逐次判断</h2>')
    out.append(table(['序号','释放时间','目标','原生伤害（万）','判断'],[[r['index'],f"{r['t']:.3f}",H('、'.join(r['targets'])),number(r['raw_sum']),H(notes[r['index']])]for r in ledger['rows']if not r['recognized_judgement']]))
    out.append('<h2>5. 全部43次重击：原始伤害与现有评分计价</h2><p>“计价前”指该次已套用现有模型状态、尚未应用V2实际倍率修正的标准伤害；不是原包金额。“计入”是当前评分实际使用的标准伤害。原生总额481,723,621，不含突袭等追加归因伤害，不能直接与技能构成中含额外伤害的总额比较。</p>')
    out.append(table(['序号','释放时间','Hit','当前审判','化身','原生总额（万）','计价前（万）','倍率比例','计入评分（万）','目标'],[[f'<a href="#cast{r["index"]}">{r["index"]}</a>',f"{r['t']:.3f}",len(r['raw_hits']),'<span class="yes">有记录</span>'if r['recognized_judgement']else'<span class="no">未识别</span>','有'if r['recognized_avatar']else'无记录',number(r['raw_sum']),number(r['pre_v2']),f"{r['v2']['full_rate']:.2f}%"if r['v2']else'未知/未匹配',number(r['scoring_row']['power']if r['scoring_row']else None),H('、'.join(r['targets']))]for r in ledger['rows']]))
    out.append('<p class="warn">旁支问题：527.931秒这一放没有匹配到原生Hit，但当前未知证据回退仍计入约964.59万标准伤害；这不是本次查到的审判缺失证据，却说明“未知”不能直接等同于真实完整命中。166.866、588.936、617.532、986.030秒未进入此次评分的实战时间轴。其余39次累计510,017,053.31，理论41次累计754,415,513.60，仍严格复现67.6043%；本次没有改分。</p>')
    out.append('<h2>6. 逐Hit原包明细（可筛选并展开）</h2><input id="search" placeholder="输入时刻、序号或Boss名筛选" oninput="document.querySelectorAll(\'.cast\').forEach(e=>e.hidden=!e.dataset.find.includes(this.value))">')
    for r in ledger['rows']:
        title=f"#{r['index']}　{r['t']:.3f}s　{' / '.join(r['targets'])or'无原生Hit'}　{len(r['raw_hits'])}Hit　审判：{'有记录'if r['recognized_judgement']else'未识别'}"
        out.append(f'<details class="cast" id="cast{r["index"]}" data-find="{H(title,quote=True)}"><summary>{H(title)}</summary><p>释放时主要状态：{H(buffs_text(r["buffs_at_cast"]))}</p>')
        if r['index']in notes:out.append('<p class="warn">'+H(notes[r['index']])+'</p>')
        out.append(table(['Hit','命中时刻','距释放','通道/倍率','原始整数伤害','暴击','审判/化身记录','该Hit目标Debuff','该Hit主要Buff','原包'],[[h['ordinal'],f"{h['seconds']:.3f}",f"{h['delay']:.3f}s",h['phase_label'],f"{h['amount']:,}",'暴击'if h['critical']else'普通',f"{'有'if h['recognized_judgement']else'无记录'}/{'有'if h['recognized_avatar']else'无记录'}",H(debuff_text(h['debuffs'])),H(buffs_text(h['buffs'])),f"帧{h['frame']}<br>偏移{h['wire_record_offset']}<br><code class=small>{h['wire_packet_sha256']}</code>"]for h in r['raw_hits']]))
        out.append(f'<p class="muted">附加归因伤害另计：{r["extra_sum"]:,}。原生Hit绑定依据为相邻重击施放与最多5秒窗口，279Hit全部绑定；不把5秒窗口当作客户端命中规则。</p></details>')
    out.append('<h2>7. 理论41次逐次计价（不是与实战按序号强行配对）</h2>')
    out.append(table(['序号','理论释放时间','标准伤害（万）','理论轴记录的自身状态'],[[x['index'],f"{x['seconds']:.3f}",number(x['power']),H('；'.join(dict.fromkeys(w.get('name','')for w in x['effects']if w.get('source')in('审判力量','神圣化身'))))or'无这两种状态']for x in ledger['theory']]))
    out.append('<h2>结论边界</h2><p>当前能确认的是文件中有多少施法/状态包，以及每个重击Hit的原始金额和暴击位。尚不能确认12次无记录都是真实无Buff，也不能据现有证据宣布已找到一个漏解审判的UDP格式。逐次标准伤害、原始伤害、当前状态标签已并列，后续补证应针对这些具体时刻，而不是按整场覆盖率笼统判断。</p><p>本地证据：smite_ledger.json、wire_census.json、evidence_summary.json、smite_wire_packets.json。未改动生产代码/云端。</p></main></html>')
    dest=ROOT/'BlueMoon重击逐次逐Hit与审判力量复核_20261019.html'
    dest.write_text(''.join(out),encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print(dest.resolve())


if __name__=='__main__':main()
