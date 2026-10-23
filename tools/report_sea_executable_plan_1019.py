"""Evidence-backed manual raid plan. No counterfactual DPS/score claims."""
import hashlib
import html
import json
from pathlib import Path
from statistics import median
from tools.report_sea_support_timeline_1019 import stages, table

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/verification/sea_support_timeline_1019"
RESOURCE = Path("D:/DragonNest/Reborn/Resource_unpacked_all")
NUMBERS = ["五", "十", "十四", "十七", "十九"]

def read(folder, n):
    return json.loads((OUT / folder / f"1019噬魂竞速第{n}把.json").read_text(encoding="utf-8"))

def whole_hp(d, template="4237023"):
    # Part HP uses the same actor ID; NEVER divide it by the whole-body HP.
    return sorted((e for e in d["BossHealthEvent"]
                   if e["target_id"].endswith("@" + template) and not e["part_id"]
                   and e["target_max_hp"] > 1_000_000_000),
                  key=lambda e: e["timestamp_ms"])

def hp_at(rows, sec):
    before = [x for x in rows if x["timestamp_ms"] <= sec * 1000]
    if not before:
        return None
    x = before[-1]
    return round(100*x["current_hp"]/x["target_max_hp"], 3)

def main():
    facts = []
    for n in NUMBERS:
        d, h = read("three_buffs", n), read("hp_actions", n)
        phase = stages(d)["4237023"]
        hp = whole_hp(h)
        damage, start = max((sum(phase["seconds"].get(s, 0) for s in range(t, t+15)), t)
                           for t in range(int(phase["first"]), int(phase["first"])+61))
        first_ice = next((a["timestamp_ms"]/1000 for a in h["BossActionEvent"]
                          if a["template_id"] == 4237023 and a["skill_index"] in (325430, 325431)), None)
        facts.append(dict(number=n, first=phase["first"], peak_start=start,
                          relative=start-phase["first"], damage=damage,
                          hp_at_start=hp_at(hp, start), hp_at_end=hp_at(hp, start+15),
                          first_ice=first_ice, last_hp=hp_at(hp, 1e9),
                          max_hp=hp[0]["target_max_hp"], first_entity_hp=hp[0]["current_hp"]))
    ai_paths = [
        RESOURCE / "Resource17/resource/script/monsterai/seadragonnest_finalboss_seadragon_3phase_hc.lua",
        RESOURCE / "Resource11/resource/script/monsterai/seadragonnest_finalboss_seadragon_1phase_hc.lua",
    ]
    ai_sources = [dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in ai_paths]
    d17 = read("three_buffs", "十七")
    target = next(v for k,v in d17["targets"].items() if k.endswith("@4237023"))
    contribution = sorted([(d17["players"].get(k, {}).get("job") or "未归属",
                            sum(v for s,v in secs.items() if 306 <= int(s) < 321))
                           for k,secs in target["player_seconds"].items()], key=lambda x:-x[1])
    result = dict(five_capture_validation=facts, ai_sources=ai_sources,
                  first_peak_job_damage=contribution,
                  scope="Manual trial plan, not a proven optimum. No new client/score/cloud changes.")
    (OUT / "执行轴证据_20261020.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    body = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>梦魇海龙：当前八人队执行时间轴</title>
<style>body{max-width:1320px;margin:28px auto;padding:0 24px;font:16px/1.8 system-ui;color:#243e47}
h1,h2,h3{color:#006c78}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #cadadd;padding:10px;text-align:left;vertical-align:top}
th{background:#eaf4f5}aside{background:#fff2d7;padding:16px;border-radius:10px}strong{color:#075c68}li{margin:6px 0}
details{border:1px solid #ccdadd;padding:12px;margin:12px 0}summary{cursor:pointer;font-weight:600}
@media print{body{font-size:11px}details{display:block}tr{break-inside:avoid}}</style>
<h1>梦魇海龙：当前八人队执行时间轴 v1.0</h1>
<p>2026-10-20｜爆发＝火舞灼热火焰；加速＝女王时间加速；吉米＝噬魂幽灵之魂。</p>
<p><strong>建议先试：天启“爆发＋吉米” → 狮蝎“加速＋吉米” → 海龙一阶段“爆发＋吉米” → 犬阶段按当前打法用三项 → 三阶段首次拉冰处理完后“爆发＋吉米”，下一轮加速留给确实能持续打的地面窗口。</strong></p>
<aside>这不是把旧释放记录重新排版，也不是已证明最优的八人反事实模拟。
下文给出可执行的试打口令、冷却限制和血量条件。参考秒数不能盖过现场机制。
19把先前已提取输出与三项辅助，本次对第5、10、14、17、19把重新解析整实体HP及动作。
这些样本三阶段均未出现整实体HP归零，不能提供有实测依据的最后斩杀秒数。
当前客户端AI是本地现有文件，不代表已证明10月19日服务端执行的每个门槛、优先级完全相同。</aside>
<h2>一、这版按谁的队伍做</h2>
<p>第十七把：圣骑、曜、黑暗女王、火舞、剑皇、烈、炼金圣士、噬魂。不是通用八职业模板，不使用精神爆发。</p>
<p>第一轮海龙高峰中，火舞、烈、剑皇合计约61.7%的Boss伤害。因此先让这三人和其他正在输出的队员吃到增益，
比为了追求表面“8/8覆盖”跑去远处找暂时不输出的角色更重要。圣骑负责拉稳；曜的存活保障不能为了凑爆发中断。</p>"""
    body += table(["职业", "第十七把306–321秒对海龙伤害", "本窗口占比（不是整场占比）"],
                  [[j, f"{v/1e6:.2f}M", f"{100*v/606774641:.1f}%"] for j,v in contribution if v])
    body += """<h2>二、指挥可以照着喊的前半程</h2>
<p>秒数用第五把作为参考，T0＝这份抓包开始；不是从第一下伤害开始。每关实际开打早晚变化后，优先使用右侧事件条件。
表中“微调”还未实测，只在技能确实亮起时执行；不编造已验证的冷却。</p>"""
    body += table(["参考时间", "现场条件", "指挥口令／安排", "依据与冷却约束"], [
        ["0:06左右", "跑图开场", "爆发可用于跑图；吉米先留天启。", "第五把6.44秒爆发、85.39秒再放。开局吉米不是必需；若想用于首怪，必须确认天启前能转好。"],
        ["0:25–0:32", "两只火焰怪聚好", "普通输出快速清怪，不额外交长CD加速。", "第十七把这段仅约7秒；短目标不值得占用给后续Boss的加速。"],
        ["1:24–1:26", "天启成为主集火目标，主力集合", "“吉米，爆发，打天启。”噬魂先起手，火舞紧接，输出技能进场。", "第五把吉米84.03、爆发85.39；天启84.67–99.30。约15秒窗口与两项增益接近。"],
        ["1:49–1:55", "转火狮蝎、第一轮主力技能已开始进CD", "“狮蝎加速，吉米跟上。”火舞本轮不强等爆发。", "第五把加速112.89、吉米115.53；第十七把同样111.82/113.14。刚在天启放完爆发，不安排不可能的连续释放。"],
        ["2:10–2:15", "狮蝎已接近击杀，预计只剩数秒", "“爆发留海龙。”用短技能收尾，别在这时新开完整团队窗口。", "第十七把131.88爆发、135.86最后伤害，名义14秒只有约4秒在该目标收尾。若狮蝎还很多血则取消留技能。"],
        ["2:49–2:52", "海龙一阶段已能攻击、确认安全输出侧", "“海龙一阶段，爆发吉米。”先集合，不把Buff开在过场。", "第五把爆发169.35、吉米171.94；海龙170.12开始受伤。可试把吉米起手前移1–2秒，但只在CD已好且人已到位时。"],
        ["3:00以后", "一阶段接近89%实体HP转场", "正常输出压过门槛；不为最后一点血再交加速。", "AI存在89%结束条件，实际HP可因最后一击低于89%；不是要打空一阶段整条HP。"],
        ["3:50–3:57", "地狱犬组站好，任务组不强行聚齐", "“犬，吉米；核心技能出手后加速，爆发跟上。”", "第五把229.51/232.15/237.19；第十七把246.41/250.88/254.21。可试爆发提前2–3秒覆盖首轮，前提CD允许；不把现有时刻当作最优。"],
        ["犬结束后", "转三阶段", "“下一轮先处理拉冰，爆发吉米留回位；加速本轮不等。”", "犬用加速后，离三阶段首峰约60秒，不安排再放一次。不是让输出职业全程停手。"],
    ])
    body += """<h2>三、三阶段执行轴：第一次按时间，后面按血量和机制</h2>
<p>S0＝三阶段首次记录到Boss受伤。第五把S0=255.96秒，第十七把S0=269.80秒。
所有“已好”都以本人实际CD/锁定状态为前提，不能按截图里的Buff消失就认为技能好了。</p>"""
    body += table(["相对时间／事件", "第五把参考", "安排", "什么情况下取消或顺延"], [
        ["S0～首次拉冰处理", "4:16～约4:48", "短CD可以打；确认拉冰方向与站位；保留三阶段第一轮爆发＋吉米。", "不要空站等35秒。若Boss更早稳定、主力已能连续输出，则按现场提前，不死守秒数。"],
        ["S0+约33～37秒，主力回位", "4:49～4:53", "“回位，吉米爆发，打第一轮。”噬魂与火舞相隔约0–2秒；先铺必要减抗/易伤再把主力技能打进去。", "实际五把首峰从+33～37秒起。两项CD必须已好；犬阶段用过加速，本轮只用两项。"],
        ["第一轮后进入飞天/拉冰相关血量带", "约5:07以后，实际机制优先", "已有Buff尽量打满；新长CD增益先看剩余地面时间，不追着血条硬补。", "HP门槛只是AI可用条件，不是立即无敌。不能用“无伤害记录”直接判定无敌。"],
        ["下一段稳定地面，约S0+80～86秒", "5:36～5:42", "爆发、吉米均已好时再对齐一轮；可把第五把336秒吉米微调至约339秒，接342秒爆发。", "这是≤3秒的候选调整，不是强等。若主力此刻已在集中输出，或等待会少一次吉米，则立即放已好的技能。"],
        ["再下一段稳定地面，约S0+125～132秒", "6:19～6:28", "加速可用时做三项大窗口；可试吉米约383、加速约385、爆发约388，主力技能集中覆盖。", "第五把实际379.29/384.98/387.52，建议吉米延后约4秒需重新看CD。该段387秒有冰捆动作，安全处理高于纸面重叠。"],
        ["后续每次落地/拉冰后回位", "不再写死全局秒数", "指挥确认“目标可打、主力在位、技能已好”：爆发＋吉米先对齐；加速满足CD且有持续输出才加入。", "缺一项不必空等十几秒。观察下一次飞天血量带，必要时留到落地；不因留Buff停止正常推进。"],
        ["最后一段可击杀", "没有足够通关样本给出秒数", "确认剩余HP能在本轮打完，则剩余团队增益用于击杀，不再为“下一轮”留。", "仅规则建议，不能从这五把未归零的记录推导准确斩杀线。"],
    ])
    body += """<h2>四、HP与AI具体怎么参与排轴</h2>
<p>本次只使用part_id=0的整实体血量；头、尾部位血量不混入Boss百分比。
三阶段实体满血98.2亿，入场约67.758亿＝69%；一阶段实体满血71.2亿，机制要求打约11%，不是把满血全部打完。</p>
<p>当前3Phase_hc.lua的325474飞天候选区间为55–64%、45–54%、35–44%、25–34%、15–24%、5–14%、0–4%；
拉冰325430/325431分布于60–69%、50–59%等十个百分点区间；冰捆325456在30–59%标注120秒CD、0–29%标注60秒CD。
这些条件还受当前动作、距离、状态和优先级约束，<strong>不是“到64%必定立即飞天”</strong>。
ACT的68%、58%等提前提醒是预警线，不等于AI实际释放血线。</p>
<p>第十七把306–321秒约6.07亿输出，相当于满血6.18%；这15秒平均约4045万/秒。
例如只差1%就进入下一机制血量带，按这个短峰速率仅约2.4秒。
不能因此预订接下来还有完整14–18秒站桩，必须看Boss动作。
这里只是用实测短峰说明风险，不用它外推整场击杀时间。</p>"""
    body += table(["抓包", "首次伤害", "首次冰弹", "首轮15秒高峰", "相对起点", "输出", "峰前→峰后整实体HP", "最后可见HP"],
                  [[f"第{x['number']}把", f"{x['first']:.2f}s", f"{x['first_ice']:.2f}s",
                    f"{x['peak_start']}–{x['peak_start']+15}s", f"+{x['relative']:.2f}s",
                    f"{x['damage']/1e8:.2f}亿", f"{x['hp_at_start']}% → {x['hp_at_end']}%",
                    f"{x['last_hp']}%"] for x in facts])
    body += """<h2>五、同一轮内部：不是三个Buff必须同一毫秒</h2>
<ol><li>噬魂先报吉米，火舞紧接爆发：以队员收到状态为准。抓包中Buff有约0.6–1秒甚至更长的接收差，口令应略早于主力输出。</li>
<li>剑皇重锤、女王能量反射、炼金减抗、噬魂烙印/怨恨之像等，尽量在主力伤害前到位；不能把开团的前3秒全部用于补辅助后才想起来交主力。</li>
<li>有加速的轮次，核心技能开始进入CD后即可交，不需要等八个人所有大技能都打完。可试主力起手后约1–3秒；这只是队伍执行节拍，不是已证明的数学最优偏移。</li>
<li>噬魂吉米后接输出、保证烙印，门和纸尽量覆盖可持续攻击的窗口；火舞避免等长引导打完才补爆发。</li>
<li>坦克稳定朝向和机制，曜优先保证生存及持续辅助。分组阶段按实际打同一目标的人聚Buff，不为了全队覆盖率破坏任务分工。</li></ol>
<p>第十七把首峰实际记录到：吉米303.47、炼金C2H5OH304.81、女王上升重力304.91、怨恨之像304.99、
黑洞305.54、门306.98、能量反射306.99、爆发307.03、乌鸦307.08、重锤307.80、纸307.88。
说明队伍确实有集中起手，并非只按火舞一个人的伤害曲线推测。
这里没有证据证明所有召唤物都锁施放快照，因此不擅自规定“提前放门必定能吃到后续全部增益”。</p>
<h2>六、冷却检查与两套加速路线</h2>
<p>本地技能表基础CD：灼热火焰90秒、时间加速100秒、幽灵之魂60秒。
第十七把收到状态持续约14/15/18秒；幽灵之魂表中原始15秒不应替换实收18秒。
实战CD受加速、爆发、装备/被动及锁定效果影响，不能简单按基础CD加到时间戳，也不能拿相邻两次施放间隔当“已经测出的CD”。</p>"""
    body += table(["方案", "加速位置", "选择理由", "风险"], [
        ["A：先执行的保守轴", "狮蝎约113s → 犬约232s → 海龙地面约385s，后续按CD+窗口", "第五把已出现这些释放间隔；前半程不大改。", "海龙首个大窗口没有加速。以降低试打风险为主，不宣称全程最快。"],
        ["B：只改一处的对照试打", "保留狮蝎；犬不交；留到三阶段首个稳定输出窗口（参考约293–296s）", "狮蝎到这里约180秒，不依赖额外刷新即可满足基础100秒；有机会让海龙首轮受益。", "犬会慢多少、进三阶段是否变晚都未知，不能只计算海龙收益；后续加速轮次重新排，不同时照抄A的385s。"],
    ])
    body += """<h2>七、试打怎么验证，避免“重叠更好但打得更慢”</h2>
<p>先试A，固定阵容和任务分工，重点改狮蝎尾段留爆发、海龙一阶段对齐、三阶段回位口令；然后再单独试B。
每种至少记录3次条件接近的有效尝试，分别比较犬击杀耗时、到达三阶段时刻、海龙69→60%、60→50%耗时、存活人数、
主力收到三种增益的人数与持续时间、每轮有效伤害。不要把减员局和完整队伍局直接比较。</p>
<p>只有“前半程增加的时间＋海龙节省的时间”合计更好，B才值得启用。
目前尚未重跑八人完整反事实技能轴、没有实测新打法，所以不能声称能快多少秒或提高多少DPS。
已完成的是五把HP/动作交叉验证及一份能试打、能校准的执行方案，不是最终最优解。</p>
<details><summary>证据与复现</summary><ul>"""
    for p in [OUT/"执行轴证据_20261020.json", ROOT/"tools/audit_sea_plan_hp_1019.py",
              ROOT/"tools/report_sea_executable_plan_1019.py", *ai_paths]:
        body += f'<li><a href="{p.as_uri()}">{html.escape(str(p))}</a></li>'
    body += "</ul><p>原始三项辅助/每秒伤害：three_buffs目录；本次整实体HP/动作：hp_actions目录。未修改客户端、评分、播报、CloudBase或安装包。</p></details></html>"
    path = OUT / "梦魇海龙_当前八人队执行时间轴_20261020.html"
    path.write_text(body, encoding="utf-8")
    print(path)
    print(json.dumps(facts, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
