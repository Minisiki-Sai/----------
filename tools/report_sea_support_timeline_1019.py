"""Make an evidence-labelled raid support planning draft, not a score optimizer."""
import html
import json
from collections import Counter
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/verification/sea_support_timeline_1019"
DATA = OUT / "three_buffs"
NAMES = ["灼热火焰", "时间加速", "幽灵之魂"]
DURATION = {"灼热火焰": 14000, "时间加速": 15000, "幽灵之魂": 18000}
PHASES = {
    "4235453": "开场双火焰怪", "4235447": "天启", "4235448": "狮蝎",
    "4237003": "海龙一阶段", "4237012": "地狱犬", "4237023": "海龙三阶段",
}


def table(headers, rows):
    return "<table><thead><tr>" + "".join(f"<th>{html.escape(str(h))}</th>" for h in headers) + "</tr></thead><tbody>" + "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in row) + "</tr>" for row in rows
    ) + "</tbody></table>"


def stages(d):
    found = {}
    for target, t in d["targets"].items():
        template = target.rsplit("@", 1)[-1]
        if template not in PHASES:
            continue
        s = found.setdefault(template, dict(name=PHASES[template], first=t["first_ms"]/1000,
            last=t["last_ms"]/1000, damage=0, seconds={}))
        s["first"] = min(s["first"], t["first_ms"]/1000)
        s["last"] = max(s["last"], t["last_ms"]/1000)
        s["damage"] += t["damage"]
        for sec, amount in t["seconds"].items():
            s["seconds"][int(sec)] = s["seconds"].get(int(sec), 0) + amount
    return found


def cast_rows(d):
    rows = []
    for c in sorted(d["casts"], key=lambda c: c["timestamp_ms"]):
        if c["skill_name"] not in NAMES:
            continue
        statuses = [s for s in d["statuses"] if s["skill_name"] == c["skill_name"]
            and s["operation"] == "apply" and s["duration_ms"] == DURATION[c["skill_name"]]
            and c["timestamp_ms"] <= s["timestamp_ms"] < c["timestamp_ms"] + 4000
            and s["source_id"] == c["source_id"]]
        recv = {s["target_id"] for s in statuses}
        first = min((s["timestamp_ms"]/1000 for s in statuses), default=None)
        last = max((s["timestamp_ms"]/1000 for s in statuses), default=None)
        rows.append(dict(skill=c["skill_name"], cast=c["timestamp_ms"]/1000,
            provider=c["source_name"], receiver_count=len(recv),
            receiver_jobs=sorted(d["players"].get(i, {}).get("job", i) for i in recv),
            first_apply=first, last_apply=last))
    return rows


def main():
    records = [json.loads(p.read_text(encoding="utf-8")) for p in DATA.glob("*.json")]
    assert len(records) == 19, f"Expected 19 capture audits, got {len(records)}"
    old = json.loads((ROOT / "outputs/verification/voice_latency_1019/live_logs.json").read_text(encoding="utf-8"))
    ordering = {x["file"]: x["first"] for x in old}
    records.sort(key=lambda d: ordering[d["file"]])
    peak_rows = []
    for d in records:
        p = stages(d).get("4237023")
        if not p or p["last"] - p["first"] < 65:
            continue
        score, start = max((sum(p["seconds"].get(s, 0) for s in range(t, t+15)), t)
            for t in range(int(p["first"]), min(int(p["first"])+61, int(p["last"])-14)))
        peak_rows.append(dict(file=d["file"], phase_first=p["first"], start=start,
            relative=start-p["first"], damage=score))
    summary = dict(captures=len(records), counts={n:sum(c["skill_name"] == n for d in records for c in d["casts"]) for n in NAMES},
        early_phase3_peaks=peak_rows,
        warning="Observed damage-window alignment only. Not counterfactual team DPS or guaranteed optimal schedule.",
        files=[dict(file=d["file"], stages={k:{x:v for x,v in s.items() if x!="seconds"} for k,s in stages(d).items()},
                    casts=cast_rows(d)) for d in records])
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    body = """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>梦魇海龙：爆发、加速、吉米时间轴草案</title>
<style>body{max-width:1350px;margin:32px auto;padding:0 24px;font:16px/1.75 system-ui;color:#243e47}
h1,h2{color:#006c78}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #ccdadd;padding:9px;text-align:left}th{background:#edf5f6}
aside{background:#fff3dc;padding:18px;border-radius:10px}details{margin:15px 0;border:1px solid #d3e0e3;padding:12px}summary{cursor:pointer;font-weight:bold}
.muted{color:#516974}li{margin:8px 0}</style>
<h1>梦魇海龙：爆发、加速、吉米时间轴草案 v0.1</h1>
<p>数据：1019噬魂竞速第1–19把，离线逐包提取；爆发＝火舞灼热火焰，加速＝时间加速，吉米＝幽灵之魂。
这是给队伍试打的阶段锚点方案，不改ACT评分、不发布、不自动控制游戏。</p>
<aside>不能将19把拼成一根绝对秒轴：有中途结束和从海龙阶段重新开始的记录。
下文“阶段起点”指该抓包第一次记录到该实体受伤，不等同于实体出生、进图或服务器开放攻击的精确时刻。
没有伤害记录不自动证明无敌。未记录到后续技能，也不直接判定玩家忘放或技能可用。</aside>
<h2>1．已确认的三种增益</h2>"""
    body += table(["技能", "第十七把实收持续时间", "规划含义"], [
        ["灼热火焰 / 爆发", "14秒", "动作速度和冷却增益；开场可能用于跑图，不只用于打Boss"],
        ["时间加速 / 加速", "15秒", "核心技能进入冷却且下一段仍可输出时使用；不要把冷却减少当成伤害直接增加70%"],
        ["幽灵之魂 / 吉米", "18秒", "四维提高60%，不是全队最终伤害直接提高60%；靠站位把Buff送到主力"],
    ])
    body += "<p>三项识别到的释放数：" + "，".join(f"{k} {v}次" for k,v in summary["counts"].items()) + "。以下持续时间来自接收状态包；效果是否提前清除及死亡后的有效收益需要另算，不能把名义持续直接当实际全覆盖。</p>"
    body += """<h2>2．建议先试的阶段轴</h2>
<p>目标不是每一关强行三Buff全重叠，而是把有限的可用轮次分配到真正能打满的窗口。第十七把的秒数只帮助定位；实际执行按场景、队员回位和技能确实可用来触发。</p>"""
    body += table(["阶段/锚点", "第十七把定位", "爆发", "加速", "吉米"], [
        ["进图、跑图、开场双火焰怪", "爆发7.6；双怪25.4–32.1", "跑图爆发单独记用途；不因它没有Boss伤害便认定无用", "通常保留给较长Boss输出段", "双怪开打前给实际到位的小队；别假设8人都在范围"],
        ["天启首轮", "79.5–97.0", "保留约开打时的爆发；样本81.3", "这套快打路线先保留给狮蝎；进度变慢再改", "开打前约1秒，样本78.7；优先保证全队收到"],
        ["狮蝎主输出", "106.3–135.9", "上一轮未转好不强求；接近击杀时不盲补一轮", "首轮核心技能进入CD后开启；样本111.8", "与加速主窗口靠拢；样本113.1，已有较好重合"],
        ["海龙一阶段开打", "167.0–218.6", "优先保留到这里与吉米一起用；不是固定等到201.8", "只有已转好、且不会牺牲后面更重要一轮时才加", "样本165.8可作为开打前给Buff的参考"],
        ["地狱犬快速击杀", "246.7–263.4", "若已好，主力集合后开，不等到Boss快死", "若本轮需要加速，开打后首轮技能进CD即用；样本250.9", "开打前给，样本246.4；按实际分组收件人核对"],
        ["三阶段首轮主力爆发", "首伤269.8；高峰约306–321", "机制处理完、主力回位后开，样本307.0较吻合", "若地狱犬已用，则本轮通常不能凭空再安排；准备另一套分配方案", "紧贴这轮开打前给，样本303.5；不在首次见到三阶段实体时就交"],
        ["三阶段后续轮次", "进度差异大", "CD转好后先判断是否马上离场/拉冰", "挑连续可输出窗口；不能为等三件套一直压着不用", "可短等3–5秒配合爆发，长等可能损失整轮，需重新规划"],
    ])
    body += """<p><b>快打狮蝎的具体改动候选：</b>第十七把爆发131.9秒、狮蝎最后记录到伤害135.9秒。
这轮爆发起效后只剩约3–4秒该Boss输出。第5把没有在狮蝎末尾交这一轮，而在海龙一阶段169.3秒放爆发、171.9秒放吉米；
它提供了一条实际执行过的参考路线。但不能据此断言少放狮蝎爆发一定更快：省下的爆发也可能使狮蝎死得更慢，需要实战A/B验证。</p>
<h2>3．三阶段首轮不是“进阶段即爆发”</h2>
<p>对记录到三阶段至少65秒的样本，在阶段首伤后的前75秒内，扫描连续15秒的已记录Boss伤害总和。
它只用于找现有打法的集中输出位置，不是预测挪Buff后的增伤。</p>"""
    body += table(["抓包", "三阶段首伤", "首段15秒峰值窗口", "相对首伤", "已记录该Boss伤害"], [
        [r["file"],f'{r["phase_first"]:.1f}s',f'{r["start"]}–{r["start"]+15}s',f'+{r["relative"]:.1f}s',f'{r["damage"]/1e6:.1f}M']
        for r in peak_rows])
    body += """<p>把这个窗口作为“处理机制、集合回位后的首轮爆发”候选；不要机械地在每次进图后同一秒播报。
各把的后续高峰和战斗长度差异较大，后半程暂不提供假精确的统一秒表。</p>
<h2>4．地狱犬与三阶段首轮：加速需要二选一分配</h2>
<p>第十七把加速250.9秒给地狱犬；三阶段首轮输出峰值在306秒左右，仅隔55秒。
不能把这两处都当成可随意安排的新加速。ACT当前数据的基础CD为100秒，实战还受已确认的减CD与限制状态影响；
不能只看技能图标以外的一项数值，更不能为对齐直接忽略CD。</p>
<ul><li><b>方案A，保留现有稳妥路线：</b>狮蝎加速→地狱犬加速→三阶段首轮爆发＋吉米，加速等后续窗口。这条安排有现有抓包支持。</li>
<li><b>方案B，试打对照：</b>若地狱犬不靠加速也能稳定快速结束，省下这轮加速给三阶段首轮。
同时记录地狱犬是否变慢、三阶段是否提前推进，以及下一轮加速是否受影响。尚未证明B更优。</li></ul>
<h2>5．一轮内怎么喊</h2>
<p>先确认技能确实可用、主力在范围内、预计还有足够连续输出时间。以团队正式集中输出的时刻T为准：
<b>吉米约T−1秒；爆发约T；加速在首轮核心技能进入CD后，通常先以T＋1～3秒试打</b>。
这只是起步口令，不是已经验证的全职业最优顺序。若窗口很短或技能本来已在CD，加速应更早；若即将强制离场，整轮延后。</p>
<p>第十七把的加速从动作记录到队友收到Buff约0.94～0.99秒，吉米通常也有约0.6～1秒到达延迟。
因此排轴应对齐实际收到Buff的时刻，不应把按键时刻直接当全队已经生效。</p>
<h2>6．范围、样本偏差与下一步验收</h2>
<ul><li>第十七把开场吉米只观察到4名接收者；天启和狮蝎部分轮次观察到8人。后半程人数还会变化。没有收到记录不自动等于一定没吃到，也不能默认所有轮次覆盖8人。</li>
<li>现有曲线已经受原来的Buff时机、死亡、机制处理与暴击影响。把同一条曲线换个罩子，不能直接声称真实团队DPS提高某个百分比。</li>
<li>建议先验证“快打狮蝎末尾不浪费爆发、保给海龙一阶段”的候选，以及三阶段首轮的爆发/吉米接收重合；加速A/B单独试，不一次更改所有安排。</li>
<li>记录每阶段用时、全队收到Buff人数、技能次数、死亡和机制失败。不能用单场总伤害直接比较不同进度/不同人数的方案。</li>
<li>若接入ACT，可先做基于阶段/机制的“准备爆发/准备吉米”提示与人工确认，不把草案直接改成强制自动播报或评分标准。</li></ul>
<h2>7．19把实际记录（展开查看）</h2>"""
    for d in records:
        body += f'<details><summary>{html.escape(d["file"])}</summary>'
        ss=stages(d)
        body += table(["阶段","本包首次伤害","本包最后伤害"],[
            [s["name"],f'{s["first"]:.1f}s',f'{s["last"]:.1f}s'] for s in sorted(ss.values(),key=lambda s:s["first"])])
        body += table(["动作时间","技能","提供者","本次4秒内确认收到人数","首次收到","最后收到","接收职业"],[
            [f'{r["cast"]:.3f}s',r["skill"],r["provider"],r["receiver_count"],
             f'{r["first_apply"]:.3f}s' if r["first_apply"] is not None else "未确认",
             f'{r["last_apply"]:.3f}s' if r["last_apply"] is not None else "未确认",
             "、".join(r["receiver_jobs"])] for r in cast_rows(d)])
        body += "</details>"
    body += '<p class="muted">证据：<a href="summary.json">汇总JSON</a>；three_buffs目录中保存19场逐包提取结果。阶段伤害仅为抓包已记录事实，不把缺失事件补成既定机制。</p></html>'
    path=OUT/"梦魇海龙_爆发加速吉米时间轴草案_20261020.html"
    path.write_text(body,encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k!="files"},ensure_ascii=False))
    print(path)


if __name__ == "__main__":
    main()
