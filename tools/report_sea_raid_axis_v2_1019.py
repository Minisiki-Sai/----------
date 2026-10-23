"""Nineteen-capture manual raid axis. Does not modify ACT, scores, or the cloud."""
import html
import json
import math
import statistics
from pathlib import Path

from tools.report_sea_support_timeline_1019 import stages, table, PHASES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/verification/sea_support_timeline_1019"
NUMBERS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
           "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九"]
SEA = ["五", "十", "十四", "十七", "十九"]
SKILLS = {
    325430: "拉冰·右向冰弹", 325431: "拉冰·左向冰弹", 30576: "冰弹后续",
    325476: "冰弹左后续", 325477: "冰弹右后续", 325474: "飞天起手",
    325468: "高血线飞天龙息", 325455: "低血线飞天流程", 325467: "飞天后续",
    325465: "飞天后续", 325473: "空中阶段后续", 325469: "落地",
    325461: "尾部破坏", 325462: "头部破坏", 325456: "冰捆",
    325458: "冰捆结束段", 325471: "尾部攻击", 325472: "尾部攻击",
    325451: "黑洞起手（AI）", 325452: "受击关联动作", 325453: "黑洞后续（AI）",
}
COLORS = {"机制": "#537a94", "破坏": "#a56716", "输出": "#177967",
          "爆发": "#b64c27", "加速": "#2265a2", "吉米": "#794b9a"}


def load(folder, n):
    return json.loads((OUT / folder / f"1019噬魂竞速第{n}把.json").read_text(encoding="utf8"))


def q(values, p):
    values = sorted(values)
    if not values:
        return 0
    pos = (len(values)-1)*p
    i = int(pos)
    return values[i] + (values[min(i+1, len(values)-1)]-values[i])*(pos-i)


def fmt(t):
    return f"{int(t)//60:02d}:{t%60:04.1f}"


def hp_at(rows, t):
    old = [x for x in rows if x["timestamp_ms"]/1000 <= t]
    return old[-1]["current_hp"]/old[-1]["target_max_hp"]*100 if old else None


def damage(sec, start, end):
    # Fractional-bin weighting avoids claiming sub-second Hit timestamps here.
    return sum(v*max(0, min(t+1, end)-max(t, start)) for t,v in sec.items()
               if t < end and t+1 > start)


def aligned_wire(n, h):
    raw = load("break_wire", n)["rows"]
    diffs = []
    for x in raw:
        if x["marker"] != "0e1600":
            continue
        candidates = [a["timestamp_ms"]/1000 for a in h["BossActionEvent"]
                      if a["template_id"] == 4237023 and a["skill_index"] == x["value"]]
        if candidates:
            nearest = min(candidates, key=lambda t: abs(t-x["t"]))
            if abs(nearest-x["t"]) < .3:
                diffs.append(nearest-x["t"])
    offset = statistics.median(diffs) if diffs else 0
    return [{**x, "t": x["t"]+offset} for x in raw], offset


def build_facts():
    records, breaks, phase_stats = [], [], {}
    for n in NUMBERS:
        d, h = load("three_buffs", n), load("hp_actions", n)
        st = stages(d)
        first = st.get("4235453", {}).get("first")
        for k,s in st.items():
            v = phase_stats.setdefault(k, [])
            v.append(dict(number=n, start=s["first"], end=s["last"],
                          relative=s["first"]-first if first is not None else None,
                          duration=s["last"]-s["first"], damage=s["damage"]))
        r = dict(number=n, file=d["file"], phases={k:{a:b for a,b in s.items() if a!="seconds"}
                                                 for k,s in st.items()},
                 integrity=d.get("capture_integrity"), sea=None)
        if n in SEA:
            phase = st["4237023"]
            base = phase["first"]
            hp = sorted([x for x in h["BossHealthEvent"] if x["target_id"].endswith("@4237023")
                         and x["part_id"] == 0], key=lambda x:x["timestamp_ms"])
            parts = [x for x in h["BossHealthEvent"] if x["target_id"].endswith("@4237023") and x["part_id"]]
            wire, off = aligned_wire(n, h)
            for i,x in enumerate(wire):
                if x["marker"] != "0e1600" or x["value"] not in (325461, 325462):
                    continue
                pid = 4150061 if x["value"] == 325462 else 4150062
                zero = [p for p in parts if p["part_id"]==pid and p["current_hp"]==0
                        and abs(p["timestamp_ms"]/1000-x["t"])<.3]
                nxt = next((a for a in wire[i+1:] if a["t"] > x["t"]+.5), None)
                reset = next((p for p in parts if p["part_id"]==pid and p["current_hp"]>0
                              and p["timestamp_ms"]/1000>x["t"]+1), None)
                row = dict(number=n, kind="头" if pid==4150061 else "尾", t=x["t"], relative=x["t"]-base,
                           skill=x["value"], hp=hp_at(hp,x["t"]+.2), zero_confirmed=bool(zero),
                           next_action=nxt["value"] if nxt else None,
                           next_action_after=nxt["t"]-x["t"] if nxt else None,
                           reset_after=reset["timestamp_ms"]/1000-x["t"] if reset else None,
                           damage_7s=damage(phase["seconds"],x["t"],x["t"]+7))
                breaks.append(row)
            # Display-only reduction: last whole-body/part HP per second.
            tracks = {}
            for p in hp+parts:
                key = str(p["part_id"])
                tracks.setdefault(key,{})[int(p["timestamp_ms"]/1000)] = round(p["current_hp"]/p["target_max_hp"]*100,3)
            p15, pstart = max((damage(phase["seconds"],t,t+15),t)
                             for t in range(int(base),int(base)+61))
            r["sea"] = dict(start=base, end=phase["last"], last_hp=hp_at(hp,1e9),
                whole_max=hp[0]["target_max_hp"], peak_start=pstart, peak15=p15,
                wire_offset=off, wire=wire, tracks=tracks, seconds=phase["seconds"],
                support=[dict(t=c["timestamp_ms"]/1000,skill=c["skill_name"]) for c in d["casts"]
                         if c["skill_name"] in ("灼热火焰","时间加速","幽灵之魂")],
                part_breaks=[b for b in breaks if b["number"]==n])
        records.append(r)
    return records, breaks, phase_stats


# Candidate command times use the seventeenth capture's clock as a worked example.
# They are NOT a claim that changing casts leaves future Boss HP/mechanics unchanged.
PLAN = {
    "爆发": [80,168,247,304,350,396,457,503,549],
    "加速": [109,250,353,460],
    "吉米": [79,115,167,246,303,345,391,459,493,535],
}
DUR = {"爆发":14,"加速":15,"吉米":18}


def cd_work(start,end,plan=PLAN):
    cuts = {start,end}
    for k in ("爆发","加速"):
        for t in plan[k]:
            cuts.update(v for v in (t+.8,t+.8+DUR[k]) if start<v<end)
    cuts=sorted(cuts)
    total=0
    for a,b in zip(cuts,cuts[1:]):
        m=(a+b)/2
        cdr=.12  # Confirmed cocktail packet; remove this if the player did not receive it.
        if any(t+.8<=m<t+.8+14 for t in plan["爆发"]):
            cdr+=.40
        if any(t+.8<=m<t+.8+15 for t in plan["加速"]):
            cdr+=.70
        total+=(b-a)/max(.30,1-cdr)
    return total


def check_cd():
    rows=[]
    for key,times in PLAN.items():
        for a,b in zip(times,times[1:]):
            if key=="吉米":
                work=cd_work(a,b)
                valid=work>=60
                text=f"累计冷却进度{work:.1f}/60秒；鸡尾酒12%，爆发40%，加速70%，上限70%"
            else:
                minimum=46 if key=="爆发" else 101
                valid=b-a>=minimum
                text=f"间隔{b-a:.0f}秒；保守门槛{minimum}秒（含约1秒状态接收余量）"
            rows.append(dict(skill=key, previous=a,current=b,valid=valid,note=text))
    assert all(x["valid"] for x in rows), rows
    return rows


def proxy_audit():
    """Fixed observed damage exposure. Not a counterfactual damage gain."""
    d=load("three_buffs","十七")
    s=stages(d)["4237023"]
    result=[]
    for key,name in (("爆发","灼热火焰"),("加速","时间加速"),("吉米","幽灵之魂")):
        old=[x["timestamp_ms"]/1000 for x in d["casts"]
             if x["skill_name"]==name and x["timestamp_ms"]/1000>=s["first"]]
        new=[t for t in PLAN[key] if t>=s["first"]]
        def coverage(times):
            return sum(v*min(1,sum(max(0,min(sec+1,t+.8+DUR[key])-max(sec,t+.8))
                                  for t in times)) for sec,v in s["seconds"].items())
        result.append(dict(skill=key, old=old, new=new,
                           observed_damage_in_old_windows=coverage(old),
                           observed_damage_in_new_windows=coverage(new)))
    return result


def reproduce_fixed_curve_search():
    """Exact DAG search for this limited coverage proxy, not true raid optimization."""
    s=stages(load("three_buffs","十七"))["4237023"]
    first,last=300,557
    dp={t:(0,[]) for t in range(first-46,first)}
    for t in range(first,last):
        old=dp.get(t-1,(0,[]))
        value,path=dp[t-46]
        take=(value+damage(s["seconds"],t+.8,t+14.8),path+[t])
        dp[t]=max(old,take,key=lambda x:x[0])
    fire=max(dp.values(),key=lambda x:x[0])
    gd={}
    for t in range(first,last):
        previous=max([v for a,v in gd.items() if cd_work(a,t)>=60] or [(0,[])],
                     key=lambda x:x[0])
        gd[t]=(previous[0]+damage(s["seconds"],t+.8,t+18.8),previous[1]+[t])
    genie=max(gd.values(),key=lambda x:x[0])
    assert fire[1]==[t for t in PLAN["爆发"] if t>=first]
    assert genie[1]==[t for t in PLAN["吉米"] if t>=first]
    return dict(fire_times=fire[1],genie_times=genie[1],search_step=1,
                objective="Fixed observed main-Boss damage inside candidate buff intervals; no causal DPS claim",
                acceleration_times_fixed=PLAN["加速"])


def hp_damage_chart(sea, start=270, end=590, width=2200):
    left,right=150,32
    x=lambda t:left+(t-start)/(end-start)*(width-left-right)
    b=[f'<div class="axis-scroll sync"><svg width="{width}" height="360" role="img" aria-label="同一时间刻度的整实体HP、部位HP和每秒伤害"><title>第17把血量及团队DPS</title>']
    panels=[
        (30,70,"整实体HP %",[("0","#177967")]),
        (140,100,"头/尾HP %",[("4150061","#a56716"),("4150062","#794b9a")]),
        (250,70,"团队5秒均值 M/s",[]),
    ]
    for top,maximum,label,series in panels:
        minimum=40 if top==30 else 0
        y=lambda value:top+75-(value-minimum)/(maximum-minimum)*75
        b.append(f'<text x="7" y="{top+30}">{label}</text>')
        for value in (minimum,(minimum+maximum)/2,maximum):
            yy=y(value)
            b.append(f'<line x1="{left}" x2="{width-right}" y1="{yy}" y2="{yy}" stroke="#dbe6e9"/><text x="{left-10}" y="{yy+4}" text-anchor="end">{value:g}</text>')
        for t in range(math.ceil(start/10)*10,int(end)+1,10):
            b.append(f'<line x1="{x(t)}" x2="{x(t)}" y1="{top}" y2="{top+75}" stroke="#edf2f4"/>')
        if series:
            for key,color in series:
                points=sorted((int(t),v) for t,v in sea["tracks"].get(key,{}).items() if start<=int(t)<=end)
                # Do not bridge long unobserved gaps as though HP were sampled.
                paths=[];current=[];prev=None
                for t,v in points:
                    if prev is not None and t-prev>12:
                        paths.append(current);current=[]
                    current.append((t,v));prev=t
                paths.append(current)
                for segment in paths:
                    path=" ".join(("M" if i==0 else "L")+f"{x(t):.2f},{y(v):.2f}" for i,(t,v) in enumerate(segment))
                    b.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
        else:
            path=" ".join(("M" if t==int(start) else "L")+f'{x(t):.2f},{top+75-min(maximum,damage({int(k):v for k,v in sea["seconds"].items()},t-2,t+3)/5/1e6)/maximum*75:.2f}'
                          for t in range(int(start),int(end)+1))
            b.append(f'<path d="{path}" fill="none" stroke="#2265a2" stroke-width="2"/>')
    b.append('<text x="160" y="135" fill="#a56716">头部</text><text x="220" y="135" fill="#794b9a">尾部</text>')
    for t in range(math.ceil(start/10)*10,int(end)+1,10):
        b.append(f'<text x="{x(t)}" y="350" text-anchor="middle">{fmt(t)}</text>')
    return "".join(b)+"</svg></div>"


def svg_axis(lanes, start, end, title, step=30, width=1600):
    """Standalone report SVG; horizontally scrollable at useful label size."""
    left,right=150,32
    x=lambda t:left+(t-start)/(end-start)*(width-left-right)
    height=60+len(lanes)*54
    b=[f'<div class="axis-scroll"><svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{html.escape(title)}">',
       f'<title>{html.escape(title)}</title>']
    for t in range(math.ceil(start/step)*step, int(end)+1,step):
        b.append(f'<line x1="{x(t)}" x2="{x(t)}" y1="32" y2="{height-15}" stroke="#dbe6e9"/>'
                 f'<text x="{x(t)}" y="22" text-anchor="middle">{fmt(t)}</text>')
    for i,(label,items) in enumerate(lanes):
        y=48+i*54
        b.append(f'<text x="8" y="{y+19}" class="lane-label">{html.escape(label)}</text>')
        for item in items:
            a=max(start,item["start"]); z=min(end,item["end"])
            if z<=a:
                continue
            col=COLORS.get(item.get("kind"),"#537a94")
            opacity=".34" if item.get("estimated") else ".83"
            desc=html.escape(item.get("detail",item["label"]))
            b.append(f'<g><title>{fmt(item["start"])}–{fmt(item["end"])} {desc}</title>'
                     f'<rect x="{x(a):.2f}" y="{y}" width="{max(2,x(z)-x(a)):.2f}" height="28" fill="{col}" opacity="{opacity}" rx="3"/>')
            if item.get("estimated"):
                b.append(f'<rect x="{x(a):.2f}" y="{y}" width="{max(2,x(z)-x(a)):.2f}" height="28" fill="none" stroke="{col}" stroke-dasharray="4 3"/>')
            labeltext=html.escape(item["label"])
            if x(z)-x(a)>len(item["label"])*12:
                text_color="#243b43" if item.get("estimated") else "white"
                b.append(f'<text x="{(x(a)+x(z))/2:.2f}" y="{y+19}" text-anchor="middle" fill="{text_color}">{labeltext}</text>')
            else:
                b.append(f'<text x="{x(a):.2f}" y="{y+43}" font-size="12">{labeltext}</text>')
            b.append('</g>')
    b.append('</svg></div>')
    return ''.join(b)


def item(a,b,label,kind="机制",estimated=False,detail=""):
    return dict(start=a,end=b,label=label,kind=kind,estimated=estimated,detail=detail or label)


def mechanism_intervals(sea):
    wire=sea["wire"]; result=[]; busy_to=-1
    for i,x in enumerate(wire):
        t=x["t"];v=x["value"]
        if x["marker"]!="0e1600":
            continue
        after=wire[i+1:]
        if v in (325430,325431):
            child=next((a for a in after if a["value"] in (325476,325477) and a["t"]<t+20),None)
            resume=next((a for a in after if child and a["t"]>child["t"]+1),None)
            end=resume["t"] if resume else min(t+24,sea["end"])
            result.append(item(t,end,"拉冰",detail=f"{v}及后续动作；这段是需要处理机制，不表示全程无敌"))
        elif v==325474:
            subtype=next((a for a in after if a["value"] in (325468,325455)),None)
            if not subtype:
                continue
            if subtype["value"]==325468:
                resume=next((a for a in after if a["t"]>subtype["t"]+1),None)
                end=resume["t"] if resume else sea["end"]
                label="飞天/暗影"
            else:
                landing=next((a for a in after if a["value"]==325469),None)
                resume=next((a for a in after if landing and a["t"]>landing["t"]+.5),None)
                end=resume["t"] if resume else sea["end"]
                label="飞天/上炮" if landing else "飞天/上炮（记录截断）"
            result.append(item(t,end,label,detail=f"飞天起手325474；后续{subtype['value']}。任务小怪另算，不把Buff强行用在主Boss。"))
        elif v==325456:
            child=next((a for a in after if a["value"]==325458),None)
            end=child["t"] if child else t+6
            result.append(item(t,end,"冰捆",detail="冰捆施放至结束段；并非整段禁止其他人输出"))
    return result


def report():
    records,breaks,stats=build_facts()
    cd=check_cd()
    proxy=proxy_audit()
    search=reproduce_fixed_curve_search()
    seventeenth=next(r for r in records if r["number"]=="十七")
    sea=seventeenth["sea"];base=sea["start"]
    mechanics=mechanism_intervals(sea)
    break_items=[item(b["t"],b["t"]+7,b["kind"]+"破坏","破坏",
                      detail=f"{b['skill']}，部位HP归零；客户端名义7秒，下一动作间隔{b['next_action_after']:.2f}秒")
                 for b in sea["part_breaks"]]
    # 435 is deliberately a proposed earlier tail finish, not an observed break.
    planned_breaks=[item(312,319,"首轮破头","破坏",True),
                    item(409,416,"二轮破头","破坏",True),
                    item(458,465,"破尾窗口","破坏",True),
                    item(549,556,"三轮破头候选","破坏",True)]
    peak_values=[r["sea"]["peak15"]/15 for r in records if r["sea"]]
    stat_summary={k:dict(n=len(v),start_median=statistics.median([x["relative"] for x in v if x["relative"] is not None]) if any(x["relative"] is not None for x in v) else None,
                         duration_median=statistics.median(x["duration"] for x in v),
                         duration_p25=q([x["duration"] for x in v],.25),
                         duration_p75=q([x["duration"] for x in v],.75)) for k,v in stats.items()}
    source_info=[
        "D:/DragonNest/Reborn/Resource_unpacked_all/Resource09/resource/ext/monsterpartstable.dnt",
        "D:/DragonNest/Reborn/Resource_unpacked_all/Resource14/resource/ext/skillleveltable_monster.dnt",
        "D:/DragonNest/Reborn/Resource_unpacked_all/Resource20/resource/char/monster/sea_dragon/sea_dragon_ut_p3.act",
        "D:/DragonNest/Reborn/Resource_unpacked_all/Resource17/resource/script/monsterai/seadragonnest_finalboss_seadragon_3phase_hc.lua",
    ]
    evidence=dict(records=records,breaks=breaks,phase_statistics=stat_summary,plan=PLAN,
                  cd_audit=cd, fixed_curve_proxy=proxy, search_reproduction=search,source_files=source_info,
                  warning="Reference-clock candidate plan, NOT a full counterfactual AI/DPS simulation or a guaranteed kill ETA. Below 43.99% whole HP unobserved.")
    (OUT/"十九把_机制破坏冷却执行轴证据.json").write_text(json.dumps(evidence,ensure_ascii=False),encoding="utf8")
    body="""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>梦魇海龙·19把机制与团队执行轴</title>
<style>
body{max-width:1550px;margin:28px auto;padding:0 24px;background:#f8fafb;color:#243b43;font:16px/1.75 system-ui}
h1,h2,h3{color:#116b73;line-height:1.35}h2{margin-top:38px}a{color:#096c80}
.intro{border-left:5px solid #177967;padding:14px 20px;background:#edf6f3}
.warn{border-left:5px solid #b77a22;padding:12px 18px;background:#fff4dc}
.axis-scroll{overflow-x:auto;background:white;border:1px solid #d4e1e5;margin:14px 0}
.table-scroll{overflow-x:auto;max-width:100%}.table-scroll table{min-width:780px}
svg{display:block;font-family:system-ui}svg text{font-size:13px}.lane-label{font-weight:600}
table{border-collapse:collapse;width:100%;margin:16px 0;background:white;font-size:14px}
th,td{border:1px solid #d5e1e4;padding:9px 11px;vertical-align:top;text-align:left}th{background:#eaf2f4}
details{margin:14px 0;padding:12px;background:white;border:1px solid #d4e1e5}
summary{cursor:pointer;font-weight:600}.muted{color:#536971}.legend{display:flex;gap:20px;flex-wrap:wrap}
.key{display:inline-block;width:14px;height:12px;margin-right:6px}
select{font:inherit;padding:6px}nav{display:flex;gap:20px;flex-wrap:wrap}p{max-width:1350px}
.printline{font-size:18px;font-weight:600;padding:18px;border:1px solid #bad0d6;background:white}
@media print{body{font-size:11px}svg{max-width:100%;height:auto}.axis-scroll{overflow:visible}details{break-inside:avoid}}
</style>
<h1>梦魇海龙：19把机制、头尾破坏与团队辅助执行轴 v2</h1>
<p>2026-10-20 · 当前八人队：圣骑／曜／女王／火舞／剑皇／烈／炼金／噬魂。爆发＝灼热火焰，加速＝时间加速，吉米＝幽灵之魂。</p>
<nav><a href="#plan">执行总轴</a><a href="#p3">三阶段放大轴</a><a href="#breaks">头尾破坏</a><a href="#late">后半场接续轴</a><a href="#samples">19把原始对照</a><a href="#cd">冷却验算</a></nav>
<div class="intro"><strong>这次的主要改法：</strong>不再把三个Buff都等到“Boss倒了”才放。
首轮先用爆发＋吉米推动破头；破头提供约7秒动作占用，继续把本轮伤害打完。中段补上一轮已经转好的加速，
让主力技能在接下来的机制处理期间继续恢复。第二次破头前先交Buff，不等破头后才补。
破尾前后集中一轮；尾部若能更早收掉，则整轮跟着提前并重算后续，而不是尾提前了、Buff还照旧等二十多秒。</div>
<p class="warn">本页区分两种线：实色＝抓包事实；虚线＝建议试打安排。已解析全部19把：只有5把打到海龙三阶段，最深整实体HP约43.99%，没有本队通关样本。
因此可给出前半程实证秒轴、三阶段候选秒轴及后半程事件接续轴；不能把44%以下编成已验证的固定通关秒表。
改变辅助和破坏时机后，后续HP与机制也会改变，不能继续照旧抓包绝对秒数硬喊。</p>
<h2 id="plan">1. 指挥主轴：先照这条试，不同时试多个改法</h2>
<p>为能和包逐项核对，下图使用第17把的抓包时钟：首怪首次受伤在00:25.36。现场以首怪第一次受伤为锚，
把本页时间统一减去25.36秒即可从0起表。首怪不是第17把速度时，每关开打重新校准，而不是让计时误差一路累积。</p>"""
    lanes=[("阶段（实测）",[item(s["first"],s["last"],s["name"],"输出") for s in seventeenth["phases"].values()]),
           ("建议·爆发",[item(t,t+14,fmt(t),"爆发",True) for t in PLAN["爆发"]]),
           ("建议·加速",[item(t,t+15,fmt(t),"加速",True) for t in PLAN["加速"]]),
           ("建议·吉米",[item(t,t+18,fmt(t),"吉米",True) for t in PLAN["吉米"]])]
    body+=svg_axis(lanes,0,590,"全程辅助执行参考轴",30,1800)
    commands=[
        ("01:19 / 01:20","天启开打","吉米 → 爆发","天启有效输出约15–25秒，首轮集中。不要等先打一轮才加Buff。"),
        ("01:49 / 01:55","狮蝎开始输出","先加速；约6秒后吉米","刚在天启用过吉米，不能再写成狮蝎起手同时吉米。该延后经过CD进度验算。"),
        ("02:47 / 02:48","海龙一阶段可打","吉米 → 爆发","开打重新校准。阶段目标约11%实体满血；快进89%附近时不为收尾追加速。"),
        ("04:06 / 04:07 / 04:10","犬组主力在位","吉米 → 爆发 → 加速","保留当前犬阶段加速路线，不同时冒险改掉。牛头组不强拉回来集合；转场以犬及任务实际完成为准。"),
        ("05:03 / 05:04","三阶段首次拉冰处理完、回到输出位","吉米 → 爆发；集中打头","首次头部约05:12破坏，把Buff放在破坏前，而不是05:12才开。"),
        ("05:45 / 05:50 / 05:53","第一轮短飞天后的地面，接近下次拉冰","吉米 → 爆发 → 加速","此轮不强求同时：爆发需等45秒抵抗，加速需等上一发冷却。加速是帮助已进CD的技能恢复，机制移动期间也有价值；不是喊所有人站着硬吃拉冰。"),
        ("06:31 / 06:36","第二轮拉冰与冰捆处理完后的地面","吉米 → 爆发；推进第二次破头","原包06:49.56破头、06:59.15起飞，原先06:54才开爆发太靠后。"),
        ("07:37 / 07:39 / 07:40","尾部即将打空/刚破坏，主力就位","爆发 → 吉米 → 加速","参考原包457.86秒破尾。若434秒回位时就能收掉残尾，这一整轮应提前到破尾前后；后续全部重锚，不再照抄07:40的加速。"),
        ("08:13 / 08:23","下一轮拉冰结束后地面","吉米先放；爆发转好再补","不等待三个Buff齐，也不把还没转好的爆发强排。"),
        ("08:55 / 09:09（可取消）","45%长飞天之前","吉米先放；爆发视剩余窗口决定","若已经接近飞天且无法马上破头，取消09:09这次爆发，留落地；不是必须每45秒按一次。"),
    ]
    body+=table(["参考时刻","现场锚点","喊什么","为什么这样放"],commands)
    body+='<p class="printline">口令串：天启吉米爆发 → 狮蝎加速、吉米稍后 → 一阶段吉米爆发 → 犬吉米爆发加速 → 首次拉冰后两项破头 → 短飞天回来分次补三项 → 第二次破头前先开Buff → 破尾前后三项集中 → 后续随机制重锚。</p>'
    early=load("hp_actions","十七")["BossActionEvent"]
    early_groups=[]
    for tid,label in ((4235447,"天启机制"),(4235448,"狮蝎机制"),(4237003,"海龙一阶段"),(4237034,"二阶段冰眼")):
        seen={}
        for a in early:
            if a["template_id"]!=tid or not a["skill_index"]:
                continue
            t=a["timestamp_ms"]/1000
            key=a["skill_index"]
            if key in seen and t-seen[key]<3:
                continue
            seen[key]=t
            text={50409:"逆时针激光",50459:"地面激光",50317:"旋风",325430:"冰弹",
                  325426:"清增益",325427:"后续状态",325447:"冰眼",325448:"冰眼后续"}.get(key,str(key))
            early_groups.append((label,item(t,t+2,text,detail=f"{key}：{a['skill_name']}；条宽仅为启动标记，不是技能持续时间")))
    body+='<h3>前半程机制不能漏：第17把可确认的启动点</h3><p>下面短条只标启动，不把条宽当机制持续时间。天启旋转激光期间仍有很高实战输出，不能简单把整段从输出窗口删除；安全走位与Buff同时安排。</p>'
    body+=svg_axis([(label,[v for k,v in early_groups if k==label])
                    for label in dict.fromkeys(k for k,v in early_groups)],70,270,"前半程Boss机制启动标记",10,1900)
    body+='<h2 id="p3">2. 三阶段：机制与Buff必须放在同一条轴上</h2><p>S0＝海龙三阶段第一次受到伤害，第17把为04:29.796。下图仍保留抓包绝对时钟以便核查。每段机制不等于全程无敌；“飞天/暗影”还包括处理小怪与回位。</p>'
    refground=[item(303.56,319.75,"首轮","输出"),item(334.96,356.16,"地面","输出"),
               item(378.96,419.15,"地面/冰捆","输出"),item(433.77,470.75,"地面/破尾","输出"),
               item(496.16,557.15,"地面","输出")]
    body+=svg_axis([
        ("Boss机制·实测",mechanics),
        ("头尾破坏·实测",break_items),
        ("可操作地面段",refground),
        ("破坏目标·候选",planned_breaks),
        ("爆发·候选",[item(t,t+14,"爆发","爆发",True) for t in PLAN["爆发"]]),
        ("加速·候选",[item(t,t+15,"加速","加速",True) for t in PLAN["加速"]]),
        ("吉米·候选",[item(t,t+18,"吉米","吉米",True) for t in PLAN["吉米"]]),
    ],270,590,"三阶段机制破坏辅助共同时间轴",10,2200).replace('class="axis-scroll"','class="axis-scroll sync"')
    body+=hp_damage_chart(sea)
    body+='<p class="muted">上下两张图横向滚动同步；同一横坐标对应机制、候选施放、实际HP和DPS。HP空白表示没有连续样本，不自动填成满血或无敌。</p>'
    body+="""<p><strong>为什么不把加速放在第一次破头？</strong>这条主轴保留了犬阶段04:10的加速，05:12破头时不足其冷却/抵抗间隔，不能安排再放。
本方案在05:53冷却允许后补下一发，让已用技能在机制处理期间恢复；不是把未冷却的技能强排进去。
若要把加速留第一次破头，需要另开“犬不用加速”的对照试打，并把犬变慢的时间算回去，本页没有偷偷把两条路线混用。</p>
<p><strong>为什么有的轮次只放两项？</strong>爆发有45秒抵抗、加速实收约94秒抵抗；吉米是另一条冷却进度。
重叠能提高收益，但让三个技能每轮同时亮起不符合冷却事实。加速的收益是后续技能更快转好，不是把全队伤害直接乘一个固定系数。</p>
<h3>三阶段应优先改的三个执行点</h3>
<ol><li>首轮：拉冰处理完就集合铺减抗，吉米、爆发接上；重点让能稳定命中头部的队员完成第一次90M破头。别等破头动画出现才开始Buff，剩下只有约7秒，装不下完整14/18秒增益。</li>
<li>中段：原包第17把到409.56秒破第二次头时已经55.53%HP，约9.59秒后起飞。候选把爆发从414秒前移至396秒，覆盖更多破头前的伤害，而不是只给最后几秒。</li>
<li>第二次短飞天后：原包434秒尾部剩约1.73%＝1210万，450秒仍约1.50%，457.86秒才破。
这是另一项可执行的优化：“暗影处理好，指定能摸到尾巴的人收尾，主力不要继续散着打”。若采取此方案，把07:37这轮整体移到真正破尾前后，
并重新检查下一轮CD与机制；不要一边提前破尾、一边继续使用上图保持旧Boss动作的基准轴。
它尚未经过新打法实测，不承诺一定省23秒。尾部未到这个剩余量、有人减员或任务未完成，就取消提前破尾目标。</li></ol>
<h2 id="breaks">3. 头尾破坏：这次找到了直接技能包，不再用伤害空档猜</h2>"""
    body+=table(["部位","独立HP","归零技能包","资源动作/名义时间","恢复"],
                [["头","9000万","325462","Stun；420帧；技能效果标注7000ms","破坏后约60秒恢复部位血量"],
                 ["尾","7亿","325461","Destroy_Tail；420帧；技能效果标注7000ms","破坏后约60秒恢复部位血量"]])
    body+="""<p>部位HP不是额外需要再从Boss总血量扣一次的血条。本页团队DPS只累计对主Boss的实际伤害，不把头部90M、尾部700M再次加进总伤害。
部位恢复也不等于60秒后自动再次硬直；恢复后仍要重新打空。</p>
<p><strong>硬直对机制的正确处理：</strong>画出当前约7秒的破坏动作占用；之后重新检查HP门槛和Boss已就绪技能。
HP越线时Boss还在硬直，可以待动作结束后立刻接机制；并不意味着所有CD都暂停7秒，也不能把后面每个机制统一加7秒。
破头破尾如果重叠，不能未经验证就按7＋7＝14秒可输出来算。</p>"""
    body+=table(["把数","部位","破坏时刻","当时整实体HP","原包归零吻合","下一动作/间隔","部位恢复间隔","随后7秒主Boss伤害（估计）"],
                [[b["number"],b["kind"],fmt(b["t"]),f'{b["hp"]:.2f}%',"是" if b["zero_confirmed"] else "未匹配",
                  f'{SKILLS.get(b["next_action"], "普通动作 "+str(b["next_action"]))} / {b["next_action_after"]:.2f}s',
                  f'{b["reset_after"]:.2f}s' if b["reset_after"] else "记录内未见",
                  f'{b["damage_7s"]/1e8:.2f}亿'] for b in breaks])
    body+="""<p class="warn">特别纠正：第17把312.501秒破头→334.956秒普通攻击之间的22.455秒，不是22秒硬直。
原始0E 16 00还明确记录319.745秒飞天、324.953秒龙息。正确顺序是：破头 → 约7.24秒后飞天 → 龙息 → 恢复普通动作。
旧技能汇总漏掉破坏及部分飞天启动，不能据此认定全段都安全站桩。此次只补离线证据，未顺带改动正式解析器或播报。</p>
<h2>4. 团队输出与HP：为什么要给“窗口”而不是只给秒表</h2>"""
    body+=table(["把数","首轮15秒高峰","相对三阶段起点","主Boss伤害","平均DPS","首次破头","首次破尾","最后整实体HP"],
                [[r["number"],f'{fmt(r["sea"]["peak_start"])}–{fmt(r["sea"]["peak_start"]+15)}',
                  f'+{r["sea"]["peak_start"]-r["sea"]["start"]:.1f}s',
                  f'{r["sea"]["peak15"]/1e8:.2f}亿',f'{r["sea"]["peak15"]/15/1e6:.1f}M/s',
                  next((f'+{b["relative"]:.1f}s' for b in r["sea"]["part_breaks"] if b["kind"]=="头"),"未见"),
                  next((f'+{b["relative"]:.1f}s' for b in r["sea"]["part_breaks"] if b["kind"]=="尾"),"未见"),
                  f'{r["sea"]["last_hp"]:.2f}%'] for r in records if r["sea"]])
    body+=f"""<p>五把首峰平均DPS中位数约{statistics.median(peak_values)/1e6:.1f}M/s，范围{min(peak_values)/1e6:.1f}–{max(peak_values)/1e6:.1f}M/s。
海龙三阶段满血98.2亿，1%为9820万。因此首峰状态下一整个百分点大约需要{98.2/(statistics.median(peak_values)/1e6):.1f}秒，
首轮15秒常能推进约5–6个百分点。但这只是短峰估计，不能把这个DPS外推十分钟。</p>
<p>首轮头部通常在S0+40～43秒破，第5把却到+81秒；尾部在+81～228秒都有。这种差异不能靠“统一再加7秒”修正。
头尾任务分配、攻击位置、主力是否在场，以及小怪处理，会改变整条轴。新轴要同时盯主Boss血条和部位血条。</p>
<p>预估某段还可打多久时：先看距离下个HP门槛的剩余主Boss血量，除以最近5～10秒有效输出速率；
再看当前机制与即将破坏部位。低血量部位可确认即将打空时才把约7秒破坏占用纳入预期，不能提前凭空添加。</p>
<h2 id="late">5. 45%以后：完整接续规则轴，而不是伪造通关秒数</h2>
<p>第19把是本批最深样本。其第三次飞天起手418.597秒，落地技能483.761秒，488.160秒开始下一普通地面动作：
从起飞到地面动作约69.56秒。与前两轮约14～15秒的短飞天不是同一时长。这个差别必须进入后半场辅助轴。</p>"""
    nineteen=next(r for r in records if r["number"]=="十九")["sea"]
    body+=svg_axis([
        ("第19把·机制",mechanism_intervals(nineteen)),
        ("第19把·破坏",[item(b["t"],b["t"]+7,b["kind"]+"破坏","破坏") for b in nineteen["part_breaks"]]),
        ("示范·落地吉米",[item(488.2,506.2,"落地＋0","吉米",True)]),
        ("示范·落地爆发",[item(489.2,503.2,"落地＋1","爆发",True)]),
        ("示范·落地加速",[item(490.2,505.2,"落地＋2","加速",True)]),
    ],400,550,"已观察到的长飞天及落地接续轴",10,1600)
    body+="""<p>图中三个落地建议只用于说明顺序，是否可放仍要检查上一轮CD。不能把第17把和第19把的绝对时间拼成同一场。
后续每个落地定义L0，使用下面这条局部秒轴：</p>"""
    body+=svg_axis([
        ("Boss/任务",[item(0,3,"确认可打/回位"),item(3,18,"地面集中输出","输出"),item(18,45,"短CD持续推进","输出")]),
        ("吉米",[item(0,18,"L0：吉米","吉米",True)]),
        ("爆发",[item(1,15,"L0+1：爆发","爆发",True)]),
        ("加速",[item(2,17,"L0+2：加速","加速",True)]),
        ("部位任务",[item(0,10,"收残血部位","破坏",True),item(10,17,"若成功：约7秒","破坏",True)]),
    ],0,50,"后半场落地局部执行轴",5,1400)
    body+="""<p>部位任务行是示意，不承诺第10秒一定破。若落地当时部位没恢复或剩余太多，就没有这段保证硬直。</p>"""
    body+=table(["门槛/事件","这条轴如何接","三项辅助如何处理","证据范围"],
                [["主Boss约59/49/39/29/19%等十个百分点拉冰区间","收到对应325430/325431后按方向处理，再重锚地面段","不把预警线当实际起手；机制开始前放不满的增益留回位","59%、49%区间有本队包；更低区间只有AI条件"],
                 ["飞天候选55–64%、45–54%","已观察短飞天链；还需清暗影与回位","两次短飞天约14～15秒，不能当足够让加速从零转好的间隔","五把有直接动作证据"],
                 ["飞天候选35–44%及更低十个百分点区间","进入长飞天/上炮机制后，不继续按旧秒轴输出；等落地L0","长飞天中CD会继续恢复，但不是全部技能必然就绪；按剩余CD检查","一次完整低血线飞天链可见；后续循环未实测"],
                 ["冰捆325456；30–59%/0–29%","CD约120/60秒只表示可用，仍受Boss动作和优先级限制","被点者先处理；其余成员有安全输出位置才保持窗口","高血线实测；低于30%仅AI"],
                 ["45%以下最后推进","每次落地重复L0局部轴，若HP推进更快，则删掉中间的一轮而非赶时间补放","下一飞天前能打满才新开；能杀则全交，不再等待下一轮","没有本队44%以下实测，不给固定斩杀秒数"]])
    body+='<h2 id="samples">6. 19把都纳入了什么，而不是只挑最好一把</h2>'
    body+=table(["抓包","包含阶段","三阶段结束HP","用途"],
                [[r["file"]," → ".join(x["name"] for x in r["phases"].values()),
                  f'{r["sea"]["last_hp"]:.2f}%' if r["sea"] else "未到达",
                  "阶段＋机制＋部位＋辅助完整对照" if r["sea"] else "前半程阶段耗时/辅助/失败分布；不纳入三阶段DPS"] for r in records])
    body+=table(["阶段","有记录把数","首怪起点后的开打中位秒","记录内输出跨度中位（P25–P75）"],
                [[PHASES[k],v["n"],f'{v["start_median"]:.1f}' if v["start_median"] is not None else "—",
                  f'{v["duration_median"]:.1f}s（{v["duration_p25"]:.1f}–{v["duration_p75"]:.1f}）']
                 for k,v in stat_summary.items()])
    body+='<p class="muted">包含失败局，因此“记录内输出跨度”不等于击杀时间；第13、19把从海龙阶段开始，不用于“首怪→阶段”耗时统计。</p>'
    body+='<h3>五把三阶段原始机制轴：同一S0对齐</h3>'
    for r in records:
        if not r["sea"]:continue
        s=r["sea"]; origin=s["start"]
        lanes=[("机制",[dict(x,start=x["start"]-origin,end=x["end"]-origin) for x in mechanism_intervals(s)]),
               ("头尾破坏",[item(b["relative"],b["relative"]+7,b["kind"],"破坏") for b in s["part_breaks"]])]
        for key,name in (("爆发","灼热火焰"),("加速","时间加速"),("吉米","幽灵之魂")):
            lanes.append((key,[item(x["t"]-origin,x["t"]-origin+DUR[key],key,key)
                              for x in s["support"] if x["skill"]==name]))
        body+=f'<details><summary>第{r["number"]}把 · S0={fmt(origin)} · 最后HP {s["last_hp"]:.2f}%</summary>'
        body+=svg_axis(lanes,0,max(150,math.ceil((s["end"]-origin)/30)*30),f'第{r["number"]}把实测',30,1800)
        body+=table(["相对S0","原包时钟","机制编号","机制/动作"],
                    [[f'+{x["t"]-origin:.3f}s',fmt(x["t"]),x["value"],SKILLS.get(x["value"],"未命名技能")]
                     for x in s["wire"] if x["marker"]=="0e1600"])
        body+='</details>'
    body+='<h2 id="cd">7. 冷却验算及验收条件</h2>'
    body+="""<p>本条候选轴按：火舞45秒抵抗另留约1秒余量；加速取比包内94秒更保守的100秒并留1秒余量。
吉米按基础60秒冷却进度，使用实际已核到的鸡尾酒12%持续减CD、爆发40%减CD、加速70%减CD，叠加上限70%。
Buff接收按施放后0.8秒建模；吉米不额外假定有减CD纹章。
鸡尾酒实收参数：效果96、0.88、150000ms。若噬魂没吃到鸡尾酒或爆发，表内吉米就绪时间必须后移。</p>
<p>这项验算只证明候选时刻没有明显冷却冲突，不证明每个人都能吃到Buff、不证明伤害最大，更不证明Boss未来仍按旧秒数行动。
没有对八人新技能轴和服务端AI做反事实复现，因此本页不宣称提升多少DPS或省多少秒。</p>"""
    body+=table(["技能","上一发→下一发","校验","结果"],
                [[x["skill"],f'{fmt(x["previous"])} → {fmt(x["current"])}',x["note"],"通过" if x["valid"] else "失败"] for x in cd])
    body+='<h3>为什么选择这些秒点：固定实战曲线的筛选检查</h3>'
    body+="""<p>先把原包伤害曲线固定，在合法CD下比较候选窗口能覆盖多少已经发生的主Boss伤害：爆发候选按整数秒、最小46秒间隔筛选；
吉米候选再按冷却进度约束筛选。最后人工检查飞天、拉冰、头尾和任务。该方法只用来筛选值得试打的秒点，
不是八人重新排轴，也不能用覆盖量的增长率当作DPS提升率；旧伤害曲线本身已受旧Buff影响。</p>"""
    body+=table(["技能","原施放次数→候选","原窗口内实测主Boss伤害","候选窗口内同一条实测伤害","如何解释"],
                [[x["skill"],f'{len(x["old"])} → {len(x["new"])}',
                  f'{x["observed_damage_in_old_windows"]/1e8:.2f}亿',
                  f'{x["observed_damage_in_new_windows"]/1e8:.2f}亿',
                  "只作对齐代理；加速还影响之后的CD，不能按当时伤害判优劣" if x["skill"]=="加速" else
                  "只表示更覆盖原有高峰，不代表新增了这些伤害；也未假设全队都收到Buff"]
                 for x in proxy])
    body+="""<p><strong>首轮试打只验这四件事：</strong>①首次破头是否仍在S0+40～43秒附近；②两项增益是否在破头前已覆盖主力；
③第二次短飞天后残血尾是否立即处理；④长飞天期间不浪费新的长CD窗口。
记录第一次及第二次飞天时刻、当时HP、头尾破坏时刻、主力增益接收，再更新轴；不能只看“三个Buff重合更久”就判定更好。</p>
<details><summary>来源、边界与复现</summary>
<p>19份three_buffs＋19份hp_actions；五份break_wire独立遍历原始UDP，不受正式技能白名单限制。
第10把原始流时间原点与已解析事件相差约0.11秒，已用共同技能启动记录校准；保留偏移量。
硬直表的7秒输出基于每秒伤害桶按边界比例估计，非逐Hit精确积分。</p><ul>"""
    for p in [OUT/"十九把_机制破坏冷却执行轴证据.json",ROOT/"tools/report_sea_raid_axis_v2_1019.py",
              ROOT/"tools/audit_sea_break_wire_1019.py",ROOT/"tools/audit_sea_plan_hp_1019.py", *map(Path,source_info)]:
        body+=f'<li><a href="{p.as_uri()}">{html.escape(p.name)}</a></li>'
    body+='''</ul><p>未修改ACT主程序、评分算法、实时解析、CloudBase或安装包。</p></details>
<script>
const synchronized = [...document.querySelectorAll('.axis-scroll.sync')];
let syncing = false;
for (const node of synchronized) node.addEventListener('scroll', () => {
  if (syncing) return;
  syncing = true;
  for (const other of synchronized) if (other !== node) other.scrollLeft = node.scrollLeft;
  requestAnimationFrame(() => { syncing = false; });
});
</script></html>'''
    body=body.replace("<table>","<div class=\"table-scroll\"><table>").replace("</table>","</table></div>")
    dest=OUT/"梦魇海龙_十九把机制与破坏执行轴_20261020.html"
    dest.write_text(body,encoding="utf8")
    print(dest)
    print("records",len(records),"sea",len([x for x in records if x["sea"]]),"breaks",len(breaks),"CD checks",len(cd))
    print("break intervals",[(b["number"],b["kind"],round(b["next_action_after"],3)) for b in breaks])


if __name__=="__main__":
    report()
