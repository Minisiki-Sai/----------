"""Offline damage/cast/time reconciliation; no upload or score mutation."""
import json
import html
import pickle
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.live_dps.additional_damage_composition import attach_attributed_skill_views

OUT = ROOT / "outputs/verification/black_dragon_scope_20261020"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cached = OUT / "snapshot.pkl"
    if cached.exists():
        snapshot = pickle.loads(cached.read_bytes())
    else:
        path = Path(sys.argv[1])
        source = V576DiagnosticReplaySource(path, ROOT / "dragon_nest_builder/data")
        try:
            events = source.poll()
        finally:
            source.close()
        print("decoded", len(events), flush=True)
        agg = CombatAggregator()
        for event in events:
            agg.ingest(event)
        snapshot = agg.snapshot()
        cached.write_bytes(pickle.dumps(snapshot))
    encounters = BossRecognizer().recognize_snapshot(snapshot)
    boss_ids = {t for row in encounters for t in row.target_ids}
    players = {p.source_id for p in snapshot.players}
    player = next(p for p in snapshot.players if p.name.casefold() == "bluemoon")
    payload = {"players": [asdict(p) for p in snapshot.players],
               "combat_events": [dict(timestamp_ms=e.timestamp_ms, source_id=e.source_id,
                                     target_id=e.target_id, skill_id=e.skill_id, skill_name=e.skill_name,
                                     amount=e.amount, healing=e.healing,
                                     cast_instance_id=e.cast_instance_id,
                                     attribution_method=e.attribution_method,
                                     has_real_hit_evidence=e.has_real_hit_evidence)
                                 for e in snapshot.combat_events]}
    attach_attributed_skill_views(payload, snapshot)
    damage = [e for e in snapshot.combat_events if not e.healing and e.amount > 0 and e.source_id in players]
    targets = defaultdict(lambda: dict(damage=0, hits=0, names=set(), players=Counter()))
    for e in damage:
        t = targets[e.target_id]
        t["damage"] += e.amount
        t["hits"] += 1
        t["names"].add(e.target_name)
        t["players"][e.source_id] += e.amount
    for target, row in targets.items():
        row["names"] = sorted(row["names"])
        row["boss"] = target in boss_ids
    dragon = next(e for e in encounters if any("@4181001" in t for t in e.target_ids))
    windows = TeamScoreEngine._output_windows(snapshot, dragon.target_ids)
    all_windows = TeamScoreEngine._output_windows(snapshot, frozenset(boss_ids))
    smite_casts = sorted([c for c in snapshot.skill_cast_events if c.source_id == player.source_id
                          and c.skill_name.replace("EX", "") == "重击"], key=lambda c:c.timestamp_ms)
    smite_hits = [e for e in damage if e.source_id == player.source_id
                 and e.skill_name.replace("EX", "") == "重击"]
    stamps = [c.timestamp_ms for c in smite_casts]
    cast_rows = []
    for cast in smite_casts:
        t = cast.timestamp_ms / 1000
        cast_rows.append(dict(seconds=t, skill_id=cast.skill_id, kind=cast.packet_kind,
                              in_phase=any(a <= t <= b for a,b in dragon.phase_windows),
                              in_output=any(a <= t <= b for a,b in windows),
                              targets={}, dragon_hits=0, all_hits=0, damage=0))
    unmatched = []
    for hit in smite_hits:
        index = bisect_right(stamps, hit.timestamp_ms)-1
        if index < 0 or hit.timestamp_ms - stamps[index] > 8000:
            unmatched.append(asdict(hit))
            continue
        row = cast_rows[index]
        factual = hit.has_real_hit_evidence is True
        row["damage"] += hit.amount
        row["all_hits"] += int(factual)
        row["dragon_hits"] += int(factual and hit.target_id in dragon.target_ids)
        target = row["targets"].setdefault(hit.target_id, dict(name=hit.target_name, hits=0, damage=0))
        target["hits"] += int(factual)
        target["damage"] += hit.amount
    stamps = sorted(e.timestamp_ms for e in damage)
    gaps = [dict(start=a/1000,end=b/1000,gap=(b-a)/1000,charged=min(b-a,5000)/1000)
            for a,b in zip(stamps,stamps[1:]) if b-a>1000]
    def duration(rows):
        return sum(b-a for a,b in rows)
    def clock(rows):
        ts = sorted(e.timestamp_ms for e in rows)
        return sum(min(b-a,5000) for a,b in zip(ts,ts[1:]))/1000
    special_rows = [e for e in damage if "变身" in e.skill_name or "电磁炮" in e.skill_name]
    special_ids = {e.skill_id for e in special_rows}
    normal_rows = [e for e in damage if e.skill_id not in special_ids]
    direct_rows = [e for e in damage if TeamScoreEngine._is_output_window_anchor(e)]
    blue_payload = next(p for p in payload["players"] if p["source_id"] == player.source_id)
    result = dict(total=snapshot.total_damage, player_sum=sum(p.damage for p in snapshot.players),
                  normalized_damage=sum(e.amount for e in damage), target_sum=sum(t.damage for t in snapshot.damage_targets),
                  boss_sum=sum(e.damage for e in encounters),
                  elapsed=snapshot.elapsed_seconds, dps_seconds=snapshot.dps_elapsed_seconds,
                  blue_damage=player.damage, blue_dps=player.dps, implied_seconds_at_2_5m=player.damage/2500000,
                  raw_positive_gap_seconds=sum(min(b-a,5000) for a,b in zip(stamps,stamps[1:]))/1000,
                  team_boss_output_windows=all_windows, team_boss_output_seconds=duration(all_windows),
                  dragon=asdict(dragon), dragon_output_windows=windows, dragon_output_seconds=duration(windows),
                  encounters=[asdict(e) for e in encounters], targets=targets, smite_casts=cast_rows,
                  unmatched_smite_hits=unmatched, damage_gaps=gaps,
                  blue_skill_views={k:v for k,v in blue_payload.items() if k.startswith("skills")},
                  smite_event_evidence=[e for e in payload["combat_events"] if e["source_id"]==player.source_id
                                        and e["skill_name"].replace("EX","")=="重击"],
                  diagnostic_clocks=dict(all_damage=clock(damage), excluding_transformation=clock(normal_rows),
                      active_skill_anchors=clock(direct_rows),
                      bosses_only=clock([e for e in damage if e.target_id in boss_ids])),
                  transformation_skills=dict(Counter(e.skill_name for e in special_rows)),
                  transformation_damage=sum(e.amount for e in special_rows),
                  transformation_blue_damage=sum(e.amount for e in special_rows if e.source_id==player.source_id))
    def default(value):
        if isinstance(value, (set,frozenset)): return sorted(value)
        raise TypeError(type(value))
    (OUT/"audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=default), encoding="utf-8")
    phase_casts = [c for c in cast_rows if c["in_phase"]]
    elsewhere = [c for c in phase_casts if c["all_hits"] > 0 and c["dragon_hits"] == 0]
    landed = [c for c in phase_casts if c["dragon_hits"] > 0]
    uncertain = [c for c in phase_casts if c["all_hits"] == 0]
    hit_sum = sum(c["dragon_hits"] for c in phase_casts)
    other_totals = Counter()
    for target, row in targets.items():
        if not row["boss"]:
            other_totals[" / ".join(row["names"]) or target] += row["damage"]
    def table(headers, rows):
        return "<table><thead><tr>" + "".join("<th>"+html.escape(str(x))+"</th>" for x in headers) + \
               "</tr></thead><tbody>" + "".join("<tr>"+"".join("<td>"+html.escape(str(x))+"</td>" for x in r)+"</tr>" for r in rows) + "</tbody></table>"
    basic = table(["核对项", "结果"], [
        ("玩家伤害总和", f"{result['player_sum']:,}"),
        ("逐事件伤害总和", f"{result['normalized_damage']:,}"),
        ("所有目标伤害总和", f"{result['target_sum']:,}"),
        ("9 个主要 Boss 合计", f"{result['boss_sum']:,}"),
        ("其他目标合计", f"{sum(other_totals.values()):,}"),
        ("BlueMoon 总伤害", f"{player.damage:,}"),
        ("当前 DPS 分母", f"{snapshot.dps_elapsed_seconds:.3f} 秒"),
        ("当前 DPS", f"{player.dps:,.3f}"),
    ])
    cast_table = table(["施放秒数", "原分母是否计入", "命中目标", "龙体原生 Hit", "其他目标原生 Hit", "龙体可输出区间", "核对结论"], [
        (f"{c['seconds']:.3f}", "是", " / ".join(t["name"] for t in c["targets"].values()) or "无伤害记录",
         c["dragon_hits"], c["all_hits"]-c["dragon_hits"], "内" if c["in_output"] else "外",
         "命中龙体" if c["dragon_hits"] else ("明确打其他目标，应排除龙体分母" if c["all_hits"] else "无原生 Hit；不据此删除施放"))
        for c in phase_casts])
    damage_table = table(["未列为主要 Boss 的目标", "伤害"], [(name, f"{value:,}") for name,value in other_totals.most_common()])
    normal_clock = result["diagnostic_clocks"]["excluding_transformation"]
    text = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>1011 黑龙硬核：Hit、DPS 与总伤害对账</title>
<style>body{{font:16px/1.65 system-ui,sans-serif;color:#23414a;background:#f3f8f8;margin:0}}main{{max-width:1150px;margin:24px auto;padding:30px;background:white}}h1,h2{{color:#087b83}}table{{border-collapse:collapse;width:100%;margin:16px 0}}td,th{{border:1px solid #cbdcde;padding:9px;text-align:left}}th{{background:#e7f2f3}}.note{{background:#fff7dc;padding:14px}}code{{font-size:14px}}a{{color:#087b83}}</style><main>
<h1>1011 黑龙硬核 BlueMoon：Hit、DPS 与总伤害对账</h1>
<p>原包：1011硬核bluemoon十字.dpscap。2026-10-20；本报告只读重放，不上传评分、不改变评分公式。</p>
<h2>一、龙体平均 Hit 的确存在口径错误</h2>
<p>旧页面分子只统计龙体 Hit，分母却是龙体整段存续时间内的重击。复现：{hit_sum} Hit ÷ {len(phase_casts)} 次 = {hit_sum/len(phase_casts):.2f}。</p>
<p>其中 {len(landed)} 次命中龙体，{len(elsewhere)} 次明确打其他目标，{len(uncertain)} 次没有原生 Hit。其他目标包括四使徒幻影 10 次、黑龙崇拜图腾 1 次。748.967 秒打图腾时龙体可输出区间仍然开放，因此只按可输出时间过滤仍不足，必须结合实际命中目标。</p>
<p>保留无法证明打其他目标的零命中施放，排除已证实打其他目标的 11 次：132 ÷ 20 = <b>6.60</b>。仅对 19 次确有龙体原生 Hit 的施放统计：132 ÷ 19 = <b>6.95</b>，但这必须另标为“已命中施放平均”，不能冒充包含打空的完整平均。</p>
<p>527.931 秒存在施放记录；528.251 秒的一笔 74,451 伤害为 supplemental_cast_owner、非原生 Hit，附加伤害归因后属于连锁闪电。不能拿它证明重击命中，也不能仅因没有 Hit 就擅自删掉该次施放。</p>
{cast_table}
<h2>二、2.43M DPS：公式复现与机制时间污染线索</h2>{basic}
<p>当前计算：每两个队伍伤害事件之间的间隔最多记 5 秒，累加得到 892.379 秒。它包含装置、变身和持续伤害事件；不是评分使用的 Boss 可输出区间。</p>
<p>评分的全队 Boss 可输出区间合计 {result['team_boss_output_seconds']:.3f} 秒；仅龙体为 {result['dragon_output_seconds']:.3f} 秒。不能直接用这些时间去除全场含所有目标的伤害，否则会混用口径。</p>
<p>只排除“黑龙硬核变身左键射击”“某铁匠的超电磁炮（黑龙硬核变身）”的计时锚点，仍用同一 5 秒间隔算法：分母为 <b>{normal_clock:.3f} 秒</b>，减少 {snapshot.dps_elapsed_seconds-normal_clock:.3f} 秒。若总伤害不变，得到 <b>{player.damage/normal_clock/1e6:.6f}M</b>，与用户记忆中的约 2.5M 吻合。</p>
<p class="note">这是对差异来源的有力解释，不是游戏内部算法的直接证据。BlueMoon 仍有 {result['transformation_blue_damage']:,} 机制伤害；若同时排除这些伤害，结果为 {(player.damage-result['transformation_blue_damage'])/normal_clock/1e6:.6f}M。发布前需明确展示“全场伤害／有效输出时间”还是“角色技能伤害／有效输出时间”，不能为凑 2.5M 调整规则。</p>
<h2>三、伤害差额完整闭合，没有丢账</h2>
<p>6,435,585,640 = 5,895,443,004（主要 Boss）＋540,142,636（其他目标）。玩家合计、逐事件合计和所有目标合计完全相等。</p>{damage_table}
<h2>建议修复边界</h2>
<ol><li>Boss 技能统计统一目标口径：明确命中所选 Boss 的施放保留，明确仅打其他目标的施放排除；目标不明、疑似打空的施放独立标记，不能全部删除。投射物、召唤物跨窗口命中须使用施放归属证据，不能仅靠时间切片。</li>
<li>把“DPS 计时时长”和“评分可输出时间”明确区分；特殊机制伤害不证明玩家正常输出，计算有效输出指标时不应由它们单独延长分母。全场伤害仍保留，机制伤害另列，避免账目不守恒。</li>
<li>Boss 拆分表增加“其他目标／未列为主要 Boss”汇总及展开明细，让全场总量可直接对账；不要为了让表相等而把图腾护盾伪装成主要 Boss。</li></ol>
<p>本轮未修改生产统计或评分规则。原始核对数据：<a href="audit.json">audit.json</a>。</p></main></html>"""
    (OUT/"1011黑龙硬核_Hit_DPS与总伤害对账.html").write_text(text, encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if isinstance(v,(str,float,int))}), flush=True)
    print("smite",[(r["seconds"],r["dragon_hits"],r["all_hits"],r["in_phase"],r["in_output"]) for r in cast_rows], flush=True)


if __name__ == "__main__":
    main()
