"""Evidence-backed local HTML report for the Hit matching investigation."""
from audit_hit_matching_20261020 import *
import html, hashlib, struct
from collections import defaultdict

def esc(s): return html.escape(str(s))
def table(headers, rows):
    return '<table><thead><tr>'+''.join('<th>'+esc(x)+'</th>'for x in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+esc(x)+'</td>'for x in row)+'</tr>'for row in rows)+'</tbody></table>'
def read(name): return json.loads((OUT/name).read_text(encoding='utf-8'))

def main():
    index=read('index.json')
    first=read('00089_5216_26753.json')
    second=read('00233_5216_82106.json')
    no_mark=read('01124_5216_306976.json')
    events=read('source_events.json')
    ab=read('order_ab.json')
    groups=defaultdict(list)
    for x in ab:groups[x['skill']].append(x)
    # Record the actual local client files, not inferred asset names.
    from tools.build_attack_instance_catalog import parse_act
    root=Path('D:/DragonNest/Reborn/Resource_unpacked_all')
    assets=[]
    for rel in ['Resource08/resource/weapon/kali_soulgate_ghostbig_ex.act',
                'Resource08/resource/weapon/kali_soulgate_ghost_ex.act']:
        path=root/rel
        parsed=parse_act(path)
        assets.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            damage=[dict(action=a.name,start=s.start_frame,end=s.end_frame,
                coefficient_percent=struct.unpack_from('<f',s.payload,0)[0])
                for a in parsed.actions for s in a.signals if s.signal_type==4]))
    (OUT/'client_asset_evidence.json').write_text(json.dumps(assets,ensure_ascii=False,indent=2),encoding='utf-8')
    sections=[]
    if (OUT/'required_brand_policy_validation.json').exists():
        sections.append('''<aside class="lead"><b>后续口径确认（2026-10-20）：</b>
        用户指定：门和灵魂之纸均以有烙印完整输出作为应完成标准，没有维持烙印也属于操作损失。
        本地源码已接入此规则，34次门/纸释放验证及30项回归测试通过，尚未打包发布。
        因此，下文306.976秒这发门的21.2/33.6＝63.10%，在新口径下正是应采用的完成率，
        不再将它视为“应按无烙印满分”的分母错误；无烙印模板中大小幽灵构成错误的诊断仍成立。
        新口径只改变应达到的标准，不声称实际始终存在烙印，也不虚构缺失Hit。
        下文保留最初诊断与隔离实验，其他匹配问题尚未在此轮全部修复。</aside>''')
    sections.append('''<h1>Hit 配对与灵魂之门倍率核查</h1><p>2026-10-20 · 原包：1019噬魂竞速第十七把.dpscap</p>
    <p class="lead">确认存在算法问题，不是仅凭截图误会。至少包含：等倍率 Hit 的后处理错位、灼烧之手追加段关联窗口过窄、灵魂之门条件形态误选，以及“无烙印 EX”模板的大小幽灵构成错误。</p>
    <p>本轮为诊断与隔离对照实验：没有修改正式解析器、评分程序、安装包或云端。所有实验复用同一原包；不是把缺失段补成满分。</p>
    <h2>1．核查范围与证据边界</h2>''')
    sections.append(f'<p>扫描 {len(set(x["job"]for x in index))} 个职业、{len(set((x["job"],x["skill_id"])for x in index))} 个技能、{len(index)} 次有伤害且有理论候选的释放。不是全职业、全抓包验证。</p>')
    sections.append('''<p>扫描发现时间偏差后，对 8 类技能进行了“仅关闭匹配后的顺序重排”实验；70 次释放的最终对应窗口距离平均值改善超过 0.1 秒。它们不是 70 次确定的漏包，也不代表新配对已全部正确。逐项实例见下表。</p>
    <h2>2．火焰风暴：为什么前面明明打中了，却显示前面缺失？</h2>
    <p>先做时间和伤害匹配，再执行 prefer_packet_order。后者的动态规划保留伤害合理性，却没有把时间代价带进去。等倍率槽位大量并列时，会把实际早期 Hit 配到更晚的理论槽位。第一发甚至丢开早期 3 Hit，把后面的 Hit 挪到起手位置。</p>''')
    sections.append(table(['释放','当前现象','仅关闭重排后的对应','完成率是否因此变化'],[
        ['111.233秒','实际1.386秒的首Hit被标额外；理论第1段配到2.412秒','首Hit重新对应第1段；对应窗口距离均值459→22毫秒','此隔离实验中124%不变；它只证实时间配对错位'],
        ['255.941秒','实际1.013秒首Hit配到理论第4段；第1～3段显示缺失','首Hit重新对应第1段；中后段仍有待核缺口','此隔离实验中80% Hit、112.1%倍率不变；不能直接改成满Hit'],
    ]))
    sections.append('''<h3>还有独立的追加段漏认问题</h3><p>追加段要求：已确认灼烧之手来源为2215，同角色、同目标、原生Hit与触发记录相差不超过5毫秒，之后10～100毫秒只有一个追加候选。原包中三处本来相邻的原生/触发事件相差18、37、38毫秒，因此过不了5毫秒门槛。</p>''')
    o=first['observation']
    flame=read('00404_2215_111233.json')['observation']
    stamps=[flame['cast_timestamp_ms']+round(t*1000)for t in flame['hit_times']]
    proc_rows=[]
    for t in [114025,115034,115684]:
        e=next(e for e in events if e['type']=='CombatEvent'and e['timestamp_ms']==t and e.get('skill_id')=='2301')
        before=max(v for v in stamps if v<=t)
        after=min(v for v in stamps if v>t)
        proc_rows.append([before/1000,t/1000,after/1000,t-before,
            e['burning_hand_origin_skill_id'],e['wire_packet_sha256'][:16]])
    sections.append(table(['原生Hit绝对秒','灼烧触发绝对秒','后续同技能Hit绝对秒','触发比原生晚(ms)','包内已确认来源','触发包SHA前16位'],proc_rows))
    sections.append('''<p>这些是具有明确触发来源的追加段候选，不该仅因相差超过5毫秒就被丢给普通循环段。修复须做同次施放、目标、触发与后续Hit的一对一关联；不能只无限放宽时间窗口。第一发若这三段全部核准为0.08追加段，才会从已识别3段变成6段，倍率由1.24变成1.48。该数值是条件计算，不是已发布修复结果。</p>
    <p>另外，表头21毫秒/48毫秒等误差是前一轮匹配留下的统计值；最终行已经重排，却没有同步重算误差。它不能证明截图中的最终连线只有几十毫秒误差。倍率拟合误差字段也需要统一在最终配对后计算。</p>
    <h2>3．灵魂之门：不是同一套倍率忽然变了，而是选错了形态</h2>
    <p>26.753秒这发之前，25.360秒已对同一目标0x00000091@4235453施加烙印，包内持续17秒。对照器自动形态选择没有利用这条条件状态。</p>
    <p>程序比较“16Hit无烙印”与“24Hit有烙印”时，认为残缺太多（缺口超过40%）便不再优先采信倍率形状；然后按理论Hit数接近实际8Hit的程度排序，于是错误选中16Hit模板。用少打的结果去倒推本来是什么形态，会系统性缩小分母。</p>''')
    correct=first['candidates'][1]
    sections.append(table(['实际Hit','距施放秒','伤害','暴击','当前所选权重','另一候选解释权重'],[
        [r['actual_hit'],r['actual_time_seconds'],r['damage'],'是'if r['critical']else'否',
         next(z['viewer_weight']for z in first['selected']['rows']if z['actual_hit']==r['actual_hit']),
         r['viewer_weight']]for r in correct['rows']if r['actual_hit']]))
    sections.append('''<p>合理解释为：2次0.1大幽灵接触＋2次3.9大幽灵爆炸＋4次1.1小幽灵＝12.4倍。当前误选结果只有7.7倍，还留下1个“额外”。有烙印完整分母33.6倍下，倍率实现率约36.90%，不是43.75%。所以修正不保证分数上升：必须同时纠正实战分子和理论分母。</p>
    <p>82.106秒这发24Hit完整，程序恰好选对24Hit/33.6倍模板，所以图4显示100%。这解释了“有时对、有时错”。分段权重仍是结合对象归属、伤害档位推断，不是UDP直接写出了0.1/3.9。</p>
    <h3>更深一层：无烙印模板本身也写错了</h3>
    <p>现模板把无烙印EX的16Hit全部写成小幽灵1.1，合计17.6倍，漏掉了大幽灵结构。已确认的六轮是3小＋1大＋3小＋3小＋1大＋3小，即12次小幽灵和2只大幽灵的4次Hit，应是16Hit、21.2倍。</p>
    <p>306.976秒这发尤其典型：原包呈现三小、一个小接触接大爆炸、三小、三小、一个小接触接大爆炸、三小，合计16Hit与21.2倍；最近已见烙印的记录在286.570秒，持续17秒，已于303.570秒结束。当前程序因无烙印模板解释不了大幽灵，反而选了有烙印24Hit模板，算成21.2/33.6＝63.10%。这是强烈的无烙印完整路线证据；仍需与目标全部状态及逐轮条件一起进入正式回归测试，不能仅以缺少新状态包为无烙印的绝对证明。</p>''')
    sections.append(table(['客户端文件','动作','Signal4首字段（百分比）'],[
        [Path(x['path']).name,d['action'],d['coefficient_percent']]
        for x in assets for d in x['damage']]))
    sections.append('''<p>最新 Reborn 拆包也确认：小幽灵Shoot为110%，大幽灵Shoot为10%、Hit为390%。模板修正必须回到数据生成源，并同步查看器、对照器和评分共享数据，不能只改显示文字。</p>
    <h2>4．其他技能也存在同类问题吗？</h2>''')
    rows=[]
    for skill, values in groups.items():
        x=max(values,key=lambda x:x['before_distance']['mean']-x['after_distance']['mean'])
        b=x['before']['realization'];a=x['after']['realization']
        rows.append([x['job'],skill,len(values),f'{x["time"]/1000:.3f}',
            f'{x["before_distance"]["mean"]*1000:.0f} → {x["after_distance"]["mean"]*1000:.0f}',
            f'{b["realized_weight"]:.5g} → {a["realized_weight"]:.5g}',
            '含倍率变化，必须再核组成'if skill=='野兽之灵'else'实例倍率不变；配对时序有问题'])
    sections.append(table(['职业','技能','改善>0.1秒的释放数','代表释放秒','窗口距离均值ms：当前→关闭重排','代表倍率：当前→关闭重排','结论'],rows))
    sections.append('''<p>最直观的例子：167.104秒冰尖柱仅有1次实际Hit，却从理论第7段改回第1段，窗口距离2.9秒降到0.1秒；432.204秒地狱火海的两次Hit从第15、16段改回第2、3段，窗口距离1.732秒降到0.002秒。不是把原生7Hit或16Hit改成只需这些Hit，而是纠正已命中的位置。</p>
    <p>70次中命中数量均未变化，67次倍率权重未变化；野兽之灵有3次权重变化。这证明“连线错位”与“倍率计分错误”必须分开检查，不能见到错线就统一补满倍率。关闭顺序重排只是定位原因的实验，不是最终可直接上线的方案。</p>
    <p>同包召唤彗星也有多个候选，但均为1Hit/1倍，没有发现与灵魂之门相同的分母跳变；圆环与极度打击的形态变化存在独立规则，本轮不能据此判为错误。</p>
    <h2>5．建议修复顺序与验收</h2><ol>
    <li><b>先修最终配对的一致性：</b>最终Hit表、连线、缺失位置、误差统计、倍率实现率使用同一份结果。等倍率段优先保留时间合理的一对一对应；后处理不得仅为顺序整齐就牺牲时间证据。飞行物不同分支不能硬套全局单调。</li>
    <li><b>再修形态选择：</b>职业/EX、技能等级、对象来源、烙印和输入条件先筛选路线，再做命中匹配。不得按实际Hit少就选择较低分母；状态不确定要显示未定/区间，而不是假定无烙印。门在出生后才补烙印、烙印中途消失时，应按实际触发轮次核对，不能只看施放瞬间。</li>
    <li><b>修正门无烙印EX模板：</b>12×1.1＋2×(0.1＋3.9)＝21.2倍；有烙印16×1.1＋4×(0.1＋3.9)＝33.6倍。为部分命中、只有大幽灵命中、两目标、补烙印等建立测试。</li>
    <li><b>修正追加段关联：</b>使用包内触发来源与同目标、同次施放的唯一前后Hit链；允许已观测的跨包时间差，有竞争候选就保留未定。原生Hit和灼烧追加Hit分列，不能互相填补。</li>
    <li><b>同步评分与缓存：</b>先用这份原包做全技能回归，再扩展其他职业抓包；更新证据缓存指纹，避免旧结果继续复用。共享匹配层会被评分调用，需重新计算影响，不能声称只影响查看器，也不能直接把上述单技能变化当作最终总分变化。</li>
    </ol><p>多目标边界：332.405秒这发门全目标11Hit，分别6Hit与5Hit；本轮当前代码的单目标对照选5Hit。与截图选11Hit的口径不同，不能把少出的6Hit当漏包，也不能用它与截图逐字比较。前两发门及前两发风暴则已复现截图关键数值。</p>
    <h2>6．复核文件与代码位置</h2><ul>
    <li>index.json：1,078次自动对照的索引；每次JSON保存原始观察、全部候选、最终配对。</li>
    <li>source_events.json：相关角色的状态与技能事件，含包摘要和记录偏移。</li>
    <li>order_ab.json：仅关闭后处理的70个对照实例。</li>
    <li>client_asset_evidence.json：拆包动作百分比、路径与SHA256。</li>
    <li>act_hit_timeline_comparator/core.py：best_comparison候选排序；stage_estimator.py：prefer_packet_order及最终行替换。</li>
    <li>skill_hit_tool/confirmed_components.py：annotate_components追加关联5ms门槛、_firestorm配对；runtime_skill_catalog/default_timeline数据：条件路线与倍率源。</li>
    </ul>''')
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Hit配对与灵魂之门倍率核查</title><style>body{font:16px/1.75 system-ui;margin:36px auto;max-width:1250px;padding:0 24px;color:#243b45;background:#f7fafb}h1,h2{color:#09636c}h2{border-bottom:2px solid #c7dce0;padding-top:25px}table{border-collapse:collapse;width:100%;font-size:14px;background:white;margin:20px 0}th,td{border:1px solid #d4e0e2;padding:10px;text-align:left}th{background:#e4f0f1}.lead{background:#e4f0f1;padding:18px;border-left:5px solid #11818b}li{margin:12px 0}</style><body>'+''.join(sections)+'</body></html>'
    dest=OUT/'Hit配对与灵魂之门倍率核查_20261020.html'
    dest.write_text(page,encoding='utf-8')
    print(dest)

if __name__=='__main__':main()
