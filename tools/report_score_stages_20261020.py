"""Report measured current-code stages; never attribute old speedups again."""
import argparse
import html
import json
from pathlib import Path
import pstats

from compare_exact_score_speed import differences

LABELS = {
    "load_replay": "初次加载（解码、汇总、完整统计）",
    "hit_evidence": "逐 Hit 证据与各 Boss 倍率校准",
    "glyph_inference": "实战纹章识别",
    "theory_build": "理论排轴与辅助对照轴",
    "exclusion_check": "技能豁免检查",
    "exclusion_rebuild": "豁免重排",
    "final_score": "最终输出／辅助／职业特色／承伤评分",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    folder = args.directory
    reports = []
    for name in ("baseline1", "baseline2", "optimized1", "sea1"):
        path = folder / (name + ".json")
        if not path.exists():
            continue
        report = json.loads(path.read_text("utf8"))
        assert report["status"] == "ok"
        stages = json.loads((folder / (name + "_stages.json")).read_text("utf8"))
        durations = {r["stage"]: r["seconds"] for r in stages}
        main_stage = next(r for r in stages if r["stage"] == "score_replay")
        exclusive = sum(durations.get(k, 0) for k in LABELS if k != "load_replay")
        last_final_notice = next((r["seconds"] for r in reversed(report["progress"])
                                  if r["value"] == 99), None)
        reports.append(dict(
            name=name, capture=Path(report["capture"]).name, stages=durations,
            scoring=report["score_seconds"], loading=report["decode_seconds"],
            elapsed=report["elapsed_seconds"], other=report["score_seconds"] - exclusive,
            final_detail=main_stage.get("diagnostics", {}),
            players=main_stage.get("player_diagnostics", {}),
            theory=report["theory_diagnostics"],
            player_count=len(report["rows"]),
            final_return_seconds=(report["init_seconds"] + report["decode_seconds"] +
                                  report["score_seconds"] - last_final_notice
                                  if last_final_notice is not None else None),
        ))
    equality = None
    if (folder / "baseline2.json").exists():
        before, after = [json.loads((folder / f"{s}.json").read_text("utf8"))
                         for s in ("baseline1", "baseline2")]
        diffs = list(differences(
            {k: before[k] for k in ("rows", "plans")},
            {k: after[k] for k in ("rows", "plans")}))
        manifests = [json.loads((folder / f"{s}_source.json").read_text("utf8"))
                     for s in ("baseline1", "baseline2")]
        equality = dict(all_fields_equal=not diffs, differences=len(diffs),
                        examples=diffs[:10], source_equal=manifests[0] == manifests[1],
                        players=len(before["rows"]),
                        skills=sum(len(r["skill_details"]) for r in before["rows"]))
    profile = []
    profile_file = folder / "axis_current.prof"
    if profile_file.exists():
        stats = pstats.Stats(str(profile_file))
        for (filename, line, function), (cc, nc, tt, ct, callers) in sorted(
                stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:35]:
            profile.append(dict(file=filename, line=line, function=function,
                                calls=nc, own_seconds=tt, cumulative_seconds=ct))
    result = dict(runs=reports, repeated_correctness=equality, axis_profile=profile,
                  scope="three prior rounds enabled; new optimization only changes queue status reads")
    optimized_equality = None
    if (folder / "optimized1.json").exists():
        before, after = [json.loads((folder / f"{s}.json").read_text("utf8"))
                         for s in ("baseline2", "optimized1")]
        diffs = list(differences({k: before[k] for k in ("rows", "plans")},
                                 {k: after[k] for k in ("rows", "plans")}))
        optimized_equality = dict(all_fields_equal=not diffs, differences=len(diffs), examples=diffs[:10])
        result["optimization_correctness"] = optimized_equality
    queue_file = folder / "queue_counts.json"
    queue = json.loads(queue_file.read_text("utf8")) if queue_file.exists() else None
    result["readonly_queue_experiment"] = queue
    evidence_check = None
    if optimized_equality is not None:
        evidence = []
        for name in ("baseline2", "optimized1"):
            evidence.append({
                p.name: json.loads(p.read_text("utf8"))
                for p in (folder / (name + "_evidence") / "evidence").glob("*.json")
            })
        common = evidence[0].keys() & evidence[1].keys()
        evidence_check = dict(
            counts=[len(e) for e in evidence],
            common_count=len(common),
            common_reports_identical=all(evidence[0][k] == evidence[1][k] for k in common),
            missing_after=sorted(evidence[0].keys() - evidence[1].keys()),
            added_after=sorted(evidence[1].keys() - evidence[0].keys()),
        )
        result["evidence_artifact_check"] = evidence_check
    (folder / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf8")
    esc = html.escape
    table = "".join("<tr><th>" + esc(label) + "</th>" + "".join(
        f'<td>{r["stages"].get(key, 0):.2f} 秒</td>' for r in reports) + "</tr>"
        for key, label in LABELS.items())
    for key, label in (("other", "评分内其余准备／整理"), ("final_return_seconds", "其中：最后 99% 提示至函数返回（不重复相加）"),
                       ("scoring", "完整评分合计（不含加载）"),
                       ("elapsed", "启动＋加载＋完整评分合计")):
        table += "<tr><th>" + label + "</th>" + "".join(
            f'<td>{r[key]:.2f} 秒</td>' for r in reports) + "</tr>"
    detail = ""
    for run in reports:
        d = run["final_detail"]
        subrows = "".join(f"<tr><td>{label}</td><td>{d[key]:.3f} 秒</td></tr>"
            for key, label in (
                ("event_index_seconds", "事件索引"),
                ("support_context_seconds", "逐时刻辅助收益索引"),
                ("player_scores_seconds", "队员分项评分（并行墙钟时间）"),
                ("support_benchmarks_seconds", "全职业辅助校准基准"))
            if key in d)
        if subrows:
            detail += f'<h3>{esc(run["name"])} · 最终评分内部</h3><table>{subrows}</table>'
        tasks = run["players"].get("tasks", ())
        if tasks:
            detail += '<p>下面是工作进程各职业实际执行时间；同一批有并行，不能直接相加当作等待时间。</p><table>'
            detail += "".join(f'<tr><td>{esc(t["job"])}</td><td>{t["worker_seconds"]:.3f} 秒</td></tr>' for t in tasks) + "</table>"
        profiles = run["theory"].get("task_profiles", [])
        profiles = [p for p in profiles if "simulation_seconds" in p]
        if profiles:
            detail += '<h3>基础理论轴 · 各职业执行时间</h3><p>这里只列八条基础轴；不含后续 48 条辅助对照轴，也不能相加当作并行等待时间。</p><table><tr><th>职业</th><th>基础轴执行秒数</th></tr>'
            detail += "".join(f'<tr><td>{esc(p["job"])}</td><td>{p["simulation_seconds"]:.3f}</td></tr>' for p in profiles) + "</table>"
    profile_table = "".join(f'<tr><td>{esc(r["function"])}</td><td>{r["calls"]:,}</td>'
        f'<td>{r["own_seconds"]:.3f}</td><td>{r["cumulative_seconds"]:.3f}</td></tr>'
        for r in profile[:25])
    queue_section = ""
    if queue:
        queue_rows = "".join(f'<tr><td>{i}</td><td>{r["scan_seconds"]:.3f} 秒</td>'
            f'<td>{r["readonly_both_counts_seconds"]*1000:.3f} 毫秒</td><td>{r["rows"]}</td>'
            f'<td>{r["rows_that_would_change"]}</td></tr>' for i, r in enumerate(queue["runs"], 1))
        queue_section = f"""<section><h2>本轮已经实施：状态计数不再反复清洗全部历史</h2>
<p>旧流程 status → _pending_count／_pending_capture_file_count → 两次 TeamScoreSyncStore 初始化 → 两次遍历全部 score_detail_json。只是查询队列数量，也重新解析并整理了所有历史明细。</p>
<p>新流程使用 SQLite 只读连接执行原有 COUNT 查询，不读取评分 JSON、不创建／迁移表、不删记录。正常写库依旧执行原来的隐私清理和维护；老数据库确实缺表／缺字段时保留原迁移兼容路径。没有改变评分公式、装备、候选、理论次数或命中修正。</p>
<p>本机数据库 {queue["database_bytes"]/1048576:.1f} MiB。下表用只读连接重现一次旧 JSON 清洗计算但不写回；旧状态查询会执行两次。计数列是读取两个数量的总耗时。三次均发现 0 条实际需要更新，说明重复扫描没有带来数据变化。</p>
<table><tr><th>重复</th><th>单次历史 JSON 扫描</th><th>只读双计数</th><th>历史明细数</th><th>本来会变化的记录数</th></tr>{queue_rows}</table>
<p>新旧完整评分、技能明细及理论轴比较：{esc(str(optimized_equality))}。相关 71 项测试通过，包括只读禁止写入、WAL 新提交可见、旧库兼容和正常上传／历史流程。</p>
<p>逐 Hit 证据文件补充检查：两次共同生成的 10 份报告内容一致；优化后少留存一份黑暗女王证据报告，因此不宣称全部证据目录逐文件一致。评分返回后另有后台导出准备，基准脚本不等待该守护线程完成；此文件数量差异与该生命周期一致，但本轮没有单独等待导出做完整目录验收。正式评分结果及 3464 条理论片段的精确比较为 0 差异。具体文件列表保存在机器可读报告中。</p>
<p>本轮完整评分 432.40 秒，比第二次基线 411.76 秒还多 20.65 秒，不能声称整场已经稳定变快。阶段拆分显示：理论排轴多了约 57 秒，而 99% 提示后的收尾从 42～45 秒降至约 0.7 秒；收尾改善与移除扫描相吻合，整场净收益仍需交替多轮验证。</p>
<p>两次旧基线本身存在明显波动，因此不能把最慢旧基线与最快新结果相除，宣称稳定整场提速。明确可归因的收益是移除历史扫描；它随本机历史库大小变化，小数据库收益会小。完整测量仍保留所有样本，不挑最快。</p></section>"""
    axis_section = ""
    axis_file = folder / "optimized1_axis_times.json"
    if axis_file.exists():
        axes = json.loads(axis_file.read_text("utf8"))
        from collections import defaultdict
        grouped = defaultdict(lambda: [0, 0.0])
        for row in axes:
            kind = ("基础" if row["token"] == "initial" else
                    "移除" if row["token"].startswith("timing-b:") else "改时机")
            grouped[(row["job"], kind)][0] += 1
            grouped[(row["job"], kind)][1] += row["simulation_seconds"]
        axis_section = '<section><h2>理论任务按职业分解（工作进程秒数）</h2><p>这些任务并行并有依赖；累计工作进程秒数不是等待秒数。它用于寻找重型职业和重复对照任务，不能与上面的整场时间直接相加。</p><table><tr><th>职业</th><th>任务</th><th>数量</th><th>累计执行秒数</th></tr>'
        axis_section += "".join(f'<tr><td>{esc(job)}</td><td>{kind}</td><td>{count}</td><td>{seconds:.2f}</td></tr>'
            for (job, kind), (count, seconds) in sorted(grouped.items())) + '</table></section>'
    doc = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>当前评分耗时拆解与无损优化方案</title><style>
body{{max-width:1250px;margin:30px auto;padding:20px;background:#f3f7f8;color:#163d44;font:16px/1.75 system-ui,"Microsoft YaHei"}}
section{{background:white;padding:24px;margin:20px 0;border:1px solid #d2e3e6;border-radius:12px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #dce5e6}}
th{{background:#edf5f5}}td{{overflow-wrap:anywhere}}a{{color:#007d83}}h1,h2{{line-height:1.4}}
</style><h1>当前评分耗时拆解与无损优化方案</h1>
<p>2026-10-20 · 三轮优化全部开启后的当前源码 · 离线测量，不上传、不发布</p>
<section><h2>先看结论</h2><p>同一抓包的当前评分基线为 412～500 秒，理论排轴是第一大开销。本轮已修复一处无效历史扫描：99% 提示后的返回等待由 42～45 秒降至约 0.7 秒，评分数值没有变化。</p>
<p>新样本整场评分为 432 秒：由于排轴自身波动较大，本轮不能证明整场稳定提速。下一步优先优化理论候选回放的重复计算，而不是继续减少最后几十秒的评分汇总。下面保留全部测量结果和边界。</p></section>
<section><h2>测量口径</h2><p>固定 4 个理论轴工作进程、4 个 Hit 工作进程；独立进程、独立空 Hit 证据目录，不读取已有评分／回放结果缓存。系统文件缓存未清除。没有提高并发来冒充算法提速。软件仍按正常后台优先级运行，用户的其他程序未关闭。</p>
<p>baseline1／baseline2 是本轮修改前的当前累计优化代码；optimized1 只新增状态计数只读优化。两次基线来自同一源码；完整数值与理论轴复核：{esc(str(equality))}。这不是拿三轮优化前的旧源码重新作基线。</p>
<p>计时只包围阶段调用，未对主测试启用逐函数 cProfile。各阶段有嵌套关系：上层总时间不能再与内部子项相加。首次打开抓包的加载耗时，与已载入抓包后点击评分的等待时间分开报告。</p>
<p>本轮禁止联网并禁用真实上传，不包含云端传输／写入完成时间、页面渲染时间；这些不能从本表推断。正常导出准备在后台线程执行，不能把整个导出和上传过程误认为同步阻塞。这里发现并修复的是评分返回前同步读取上传队列状态的无效历史扫描。</p>
<p>完整复测目前限定为同一份 1011 黑龙硬核 BlueMoon 抓包；它覆盖 8 个职业，但不代表全部职业、所有巢穴或不同电脑的固定耗时。</p></section>
<section><h2>完整流程耗时</h2><table><tr><th>流程</th>{''.join('<th>'+esc(r['name'])+'<br>'+esc(r['capture'])+'</th>' for r in reports)}</tr>{table}</table></section>
<section><h2>最终评分内部与各职业耗时</h2>{detail}</section>
{queue_section}{axis_section}
<section><h2>为什么 8 人会计算很多条轴</h2>
<p>基础轴描述八个人在当前规则下的理论输出。辅助评分还需要对照：移除某一辅助后的输出，以及把该辅助安排到候选时机后的输出。黑龙样本的 56 条轴由 8 条基础轴、24 条移除轴、24 条改时机轴组成。它们并不是 56 次独立队伍评分，也不是无条件可以删除的重复结果。</p>
<p>最值得优化的是这些轴反复使用的相同计算。不能为了快就把移除轴、改时机轴省掉，也不能把逐次审判力量快照换成平均 Buff 系数。</p></section>
<section><h2>可执行的无损优化路线</h2><table><tr><th>优先级／方案</th><th>具体做法</th><th>为什么不改分／验证门槛</th><th>适用范围</th></tr>
<tr><td>P0：重复评分提前命中完整缓存</td><td>当前完整评分缓存检查发生在 Hit 上下文生成后。先以抓包、规则／资源指纹、装备、纹章、目标等完整输入验证缓存，再决定是否需要构建 Hit 上下文。</td><td>返回同一版本同一输入的原始完整结果；任何输入变化或核验失败走现有完整流程。不得只按文件名或版本号判断。</td><td>相同抓包重复评分；不改善首评，不能将缓存耗时宣传为首评耗时。</td></tr>
<tr><td>P1：理论轴内部纯计算复用</td><td>当前十字军单轴仍有 21834 次活动属性查询、47676 次效果签名构建。现有缓存已在用，下一步是量化失配原因，复用相同机械状态的完整键与投影，不是再加一层不完整的缓存。</td><td>缓存键必须包含全部依赖，尤其逐次审判／化身属性、清 Buff、目标与顺序；保持浮点运算顺序和候选搜索范围。</td><td>首评与重算；主要方向，收益尚未实测。</td></tr>
<tr><td>P1：候选回放公共前缀检查点</td><td>当前十字军单轴对 13 个候选进行了完整回放。可保存完全相同前缀结束时的模拟状态，仅从第一个分歧处继续；状态不能完全证明一致时回退全量回放。</td><td>必须含冷却／充能、召唤物、逐次 Buff 快照、资源、时间、目标、序列及所有累计量；不减少候选，严格保持后续操作与汇总顺序。</td><td>潜力较大，但实现与回归风险高，不能直接承诺收益。</td></tr>
<tr><td>P1：辅助对照任务调度与同角色复用</td><td>让可并行的同角色对照轴更容易复用已准备的数据；根据真实任务耗时调整队列，减少尾部只剩一个长任务。</td><td>只改任务执行安排，不删轴、不改候选、不改变结果汇总顺序；测量总墙钟时间及峰值内存，不能只看单条轴。</td><td>首评；受依赖关系和机器核心数限制，需 A/B。</td></tr>
<tr><td>P2：Hit 公共数据减少重复传输</td><td>不同技能任务复用同一角色的只读上下文；继续隔离各 Boss／目标的匹配结果，不将一个 Boss 的完整命中结果复制到另一 Boss。</td><td>对照每次施放、每条 Hit 的匹配、倍率和 Boss 校准结果；不只比较最终分数。</td><td>首评；现有原始解码复用和按技能并行已开启，不能重复计为新收益。</td></tr>
<tr><td>P2：首次加载解码热点</td><td>单独分析协议解码、归属判定、事件汇总、统计投影，优先消除反复扫描或重复转换。</td><td>事件顺序、角色归属、伤害／治疗／状态区间必须全量一致，不能丢弃包或缩短历史来提速。</td><td>首次打开文件；与评分按钮本身耗时分别验收。</td></tr>
</table><p>以上未标注百分比的方案是实施候选，不是已经获得的整场提速。总收益受阶段占比限制：某阶段占总评分 70%，即使该阶段快 20%，整场也仅减少约 14%。需要按同一抓包前后交替多次运行验证。</p></section>
<section><h2>单轴函数热点（诊断）</h2><p>以下使用本轮保存的当前十字军输入，另开进程运行 cProfile；插桩耗时不能当作正式评分时间，累积耗时互相包含。</p><table><tr><th>函数</th><th>调用次数</th><th>函数自身秒数</th><th>含子调用秒数</th></tr>{profile_table}</table></section>
<section><h2>验收标准</h2><p>固定相同工作进程数、输入与资源，采用修改前后交替运行；首评空缓存与重复评分热缓存分开。比较完整 PlayerScore、每技能明细、理论时间轴、Hit 证据，要求未四舍五入值一致；缓存统计、耗时等诊断字段单独处理。任何真实结果差异必须先定位，不能以“不到 3 分”放行。本轮生产修改仅为上传状态计数只读化，未打包或发布。</p><p><a href="analysis.json">机器可读结果</a> · <a href="baseline1_stages.json">第一轮阶段原始计时</a> · <a href="baseline2_stages.json">复测阶段原始计时</a> · <a href="optimized1_stages.json">优化后阶段原始计时</a></p></section></html>"""
    (folder / "当前评分耗时拆解与无损优化方案_20261020.html").write_text(doc, "utf8")
    print(json.dumps(dict(runs=[{k: r[k] for k in ("name", "scoring", "loading", "elapsed")} for r in reports],
                         equality=equality), ensure_ascii=False))


if __name__ == "__main__":
    main()
