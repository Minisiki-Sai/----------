"""Generate an evidence-bounded report of history isolation and pacing A/B."""
import html
import json
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/verification/history_catchup_20261020"


def table(headers, rows):
    def cells(tag, row):
        return "".join(f"<{tag}>{html.escape(str(x))}</{tag}>" for x in row)
    return ("<div class=scroll><table><thead><tr>" + cells("th", headers)
            + "</tr></thead><tbody>" + "".join(
                "<tr>" + cells("td", row) + "</tr>" for row in rows
            ) + "</tbody></table></div>")


def mean(rows, key):
    return statistics.mean(r[key] for r in rows)


data = [json.loads(p.read_text("utf-8")) for p in sorted(OUT.glob("1019*.json"))]
small = next(r for r in data if "第十一把" in r["file"])
size_rows = []
for n in (485, 1427, 3014):
    selected = [r for r in small["sampled_backlog_size_runs"] if r["events"] == n]
    fixed = [r for r in selected if r["mode"] == "async_fixed"]
    adaptive = [r for r in selected if r["mode"] == "async_catchup"]
    old, new = mean(fixed, "drain_seconds"), mean(adaptive, "drain_seconds")
    equal = len({r["snapshot_sha256"] for r in selected}) == 1
    size_rows.append([
        n, f"{old*1000:.1f} ms", f"{new*1000:.1f} ms",
        f"{(old-new)*1000:.1f} ms", "一致" if equal else "不一致",
    ])

bulk_rows, cpu_rows = [], []
for result in data:
    groups = {mode: [r for r in result["runs"] if r["mode"] == mode]
              for mode in ("sync_fixed", "async_fixed", "async_catchup")}
    old, fixed, adaptive = [mean(groups[m], "drain_seconds") for m in groups]
    bulk_rows.append([
        result["file"], result["events"],
        f"{old:.3f} s", f"{fixed:.3f} s", f"{adaptive:.3f} s",
        f"{(1-adaptive/fixed)*100:.1f}%",
        "全部一致" if result["all_results_identical"] else "不一致",
    ])
    fixed_cpu = mean(groups["async_fixed"], "process_cpu_seconds")
    adaptive_cpu = mean(groups["async_catchup"], "process_cpu_seconds")
    cpu_rows.append([
        result["file"],
        f"{fixed_cpu:.3f} → {adaptive_cpu:.3f} 核·秒",
        f"{fixed_cpu/fixed:.2f} → {adaptive_cpu/adaptive:.2f} 核",
        " / ".join(f'{r["drain_seconds"]:.3f}s' for r in groups["async_catchup"]),
    ])
block_rows = []
for r in small["database_block_runs"]:
    block_rows.append([
        "旧：锁内同步写库" if r["mode"] == "sync_fixed" else "新：专属线程写库",
        f'{r["statistics_lock_max_ms"]:.1f} ms',
        f'{r["reader_wait_max_ms"]:.1f} ms',
        f'{r["drain_seconds"]:.3f} s',
        r["database_summary"][0],
    ])

test_text = "测试尚未结束，不能据此宣称通过。"
if (OUT / "tests.xml").exists():
    tests = ET.parse(OUT / "tests.xml").getroot()
    suites = list(tests.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    test_text = f"{total} 项测试；失败/错误 {failed}，跳过 {skipped}。"
targeted_text = ""
if (OUT / "targeted_tests.xml").exists():
    suites = list(ET.parse(OUT / "targeted_tests.xml").iter("testsuite"))
    targeted_text = (
        f'本次直接相关回归：{sum(int(s.get("tests", 0)) for s in suites)} 项；'
        f'失败/错误 {sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)}。'
    )

body = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>统计锁与历史写库隔离 · 自适应追赶对照</title>
<style>
body{max-width:1300px;margin:32px auto;padding:0 24px;background:#f6f9fa;color:#203d47;font:16px/1.8 system-ui}
h1,h2{color:#006b78}h2{margin-top:36px}article{background:white;border:1px solid #d5e4e7;border-radius:12px;padding:24px}
aside{padding:16px;border-left:5px solid #d2942e;background:#fff4de;margin:18px 0}
.scroll{overflow:auto}table{border-collapse:collapse;width:100%;margin:16px 0;font-size:15px}
th,td{padding:11px;border:1px solid #cfdee2;text-align:left;vertical-align:top}th{background:#e8f3f5}
code{background:#edf3f4;padding:2px 5px}a{color:#006e83}.flow{background:#eaf5f4;padding:16px;line-height:2}
</style><article>
<h1>统计锁与历史写库隔离 · 自适应追赶对照</h1>
<p>2026-10-20｜已修改源码；没有更新安装包、发布版本或改动云端。自适应追赶默认关闭。</p>
<aside>结论：历史写库已移出实时统计线程；阻塞实验验证了隔离效果。追赶对“已解码事件排队”有效，
但不能据此宣称已经修复现场 1 秒/8 秒语音延迟。没有在游戏运行和真实上传并发下复现或测得可听延迟。</aside>
<h2>1．改了什么</h2>
<div class=flow>
旧：取得统计锁 → 取事件 → 历史证据整理 / SQLite 提交 → 应用统计事件 → 身份缓存读写 → 释放锁。<br>
新：有序取事件 → <strong>取得统计锁 → 只应用事件 → 释放锁</strong> → 把不可变事件批次交给历史写线程。<br>
历史写线程：建连接 → 开始会话 → 顺序处理批次 → 写入最终汇总 → 排空并关闭连接。
</div>
<p>身份缓存/队友资料的处理移出了统计锁，但仍在控制线程执行，尚未宣称它们的全部数据库访问都已异步化。
原包解析顺序、每批 256/512 事件上限、抓包端口、评分公式均未修改。</p>
<p>开始、追加、结束走同一 FIFO 队列；停止后的历史整理在锁外等待排空。失败会记录
<code>historyWriter.error</code>，并在最终 flush/close 返回错误；不会为了历史库失败而停止原始抓包。
队列没有“满了丢旧事件”的策略；它暂存事件引用而非重复序列化完整抓包，仍需观察长时间磁盘故障时的内存积压。
原始 .dpscap 才是事件档案，历史 SQLite 是辅助索引。</p>
<h2>2．数据库慢写时，实时锁是否还会被拖住？</h2>
<p>使用第十一把前 4,096 条真实解码事件。另一个 SQLite 连接对临时历史库持有
<code>BEGIN IMMEDIATE</code> 写锁 1 秒；没有碰用户现有数据库。</p>
"""
body += table(["方案", "最大统计锁占用", "观察线程最大等锁", "事件消费完成", "最终入库事件数"], block_rows)
equal_block = len({r["snapshot_sha256"] for r in small["database_block_runs"]}) == 1
body += f"""<p>阻塞前后最终统计快照：{"一致" if equal_block else "不一致"}。
“事件消费完成”不包括后续数据库排空：新方案允许统计先完成，历史写入在写锁释放后补齐，并不是跳过写库。
观察线程只是每 10ms 尝试读取统计 revision，不代表完整悬浮窗或声音已经显示/播放。</p>
<h2>3．追赶对现场出现过的队列规模，能节省多少？</h2>
<p>历史日志出现过 485、1,427、3,014 条<strong>已解码事件</strong>待统计。
复用第十一把真实事件前缀构造同规模积压，ABBA 顺序各测两次；下表为均值。
这不是相同现场时刻的重演，也未同时注入新流量。</p>"""
body += table(["起始积压事件数", "固定40ms", "实验性追赶", "减少等待", "统计快照"], size_rows)
body += """<p>因此，对几百条的积压，收益通常是几十毫秒量级；不能用它解释或抵消数秒的原包解码迟到。
原包解码器本身已有按积压调整批量/优先级的逻辑，本轮没有把那个已有机制算成新增收益。</p>
<h2>4．大积压压力测试：三份完整抓包、对称重复顺序</h2>
<p>每份抓包先有序解码一次，再将整场事件作为预先积压的队列。顺序为旧同步、异步固定、异步追赶、
异步追赶、异步固定、旧同步。每种两次，下表是均值。它测的是统计消费恢复能力，不是整场实时延迟。</p>"""
body += table(["样本", "真实事件数", "旧同步固定", "新异步固定", "新异步追赶", "追赶相对新固定缩时", "最终统计/数据库汇总"], bulk_rows)
body += """<p>对照包含伤害、治疗、人物/技能汇总和最终统计快照 SHA-256。
没有重跑全队评分；评分实现未修改。相同最终快照是本轮一致性证据，不把它写成整场抓包零丢包或全链路评分测试。</p>
<h2>5．CPU 代价与何时值得开启</h2>"""
body += table(["样本", "固定 → 追赶，总CPU", "固定 → 追赶，平均占用", "追赶两次耗时"], cpu_rows)
body += """<p>CPU 核·秒是进程 CPU 累计时间，平均占用=CPU时间/墙钟时间。
例如 0.7 核意味着平均消耗约一个逻辑核心的 70%，不是整台电脑 CPU 70%。
没有运行游戏测帧率，不能保证更集中的 CPU 工作不会影响游戏。</p>
<p>实验规则：队列为空仍等40ms；有剩余事件时等1ms，连续4轮或累计工作20ms后至少让出5ms；
每轮批量不扩大，仍执行原有语音/悬浮投影调度。普通模式完全保持40ms。
这不是硬实时保证：操作系统调度可能让实际等待更长。</p>
<p><strong>建议先使用已完成的写库隔离；追赶仍保持关闭，等你决定。</strong>
如果新增现场记录显示原包解码已跟上、但已解码事件队列仍持续堆积，再针对这部分启用追赶更有依据。
如果主要是解码水位落后、身份缓存维护或GIL/游戏调度占用，需继续解决对应阶段，不能用追赶替代。</p>
<h2>6．是不是评分上传导致的？</h2>
<p>代码核查：评分保存/上传已经在 <code>v410-team-score-sync</code> 线程执行，
使用 <code>team_score_sync.sqlite3</code>；历史索引使用 <code>live_dps_history.sqlite3</code>。
上传线程使用自己的 operation_lock，未发现它直接取得实时 collector_lock。
因此两者并非同一个 SQLite 写锁，当前证据不能认定“上传直接锁住实时统计”。</p>
<p>同进程 CPU/GIL、磁盘、内存和系统调度仍可能相互影响。本轮没有向云端上传实验数据。
新增 <code>statisticsPipeline</code> 分段观测：取事件、等统计锁、应用事件、历史入队、身份资料维护。
每个状态采样周期保留各段最大值，并加入原有慢链路日志；同时记录历史写队列深度/耗时/错误，
后续现场才能进一步区分是哪个阶段慢。</p>
<h2>7．验收与复现文件</h2>"""
body += f"<p>{html.escape(targeted_text)}</p><p>扩大回归：{html.escape(test_text)}</p>"
if (OUT / "failure_scope.json").exists():
    traced = json.loads((OUT / "failure_scope.json").read_text("utf-8"))
    body += f"""<aside>广泛回归的 {traced["selected_failures"]} 项失败已单独复跑；
对本次修改的运行路径做调用跟踪，记录为 {html.escape(str(traced["changed_path_calls"]))}，
即未进入本次历史写线程、追赶或实时消费改动路径。失败集中在协议归因、Boss血量、旧时间轴回退等测试，
其中包含仍期待飞行冲击9秒的旧断言。本轮没有修改这些测试来制造通过。
这不是旧检出版本的基线对比，不能宣称全量测试通过或所有既有失败都已解决。</aside>"""
body += """<p>新增测试覆盖：SQLite连接线程归属、两场连续FIFO、慢写时不阻塞统计、批次容器隔离、
初始化/写入失败、关闭排空和幂等、实验策略默认关闭/有界让步，以及生产循环锁内不调用归档或身份数据库逻辑。</p>
<ul>
<li><a href="../../../tools/benchmark_history_catchup_20261020.py">可复现基准脚本</a></li>
<li><a href="tests.xml">测试记录</a></li>
<li><a href="targeted_tests.xml">直接相关回归记录</a>；
<a href="failure_scope.json">扩大回归失败的运行路径核查</a></li>
"""
for result in data:
    stem = Path(result["file"]).stem
    body += f'<li><a href="{html.escape(stem)}.json">{html.escape(stem)}：逐轮原始数据</a></li>'
body += "</ul></article></html>"
if (OUT / "enabled_tests.xml").exists():
    enabled_suites = list(ET.parse(OUT / "enabled_tests.xml").iter("testsuite"))
    enabled_failures = sum(
        int(s.get("failures", 0)) + int(s.get("errors", 0))
        for s in enabled_suites
    )
    if enabled_failures == 0:
        enabled_count = sum(int(s.get("tests", 0)) for s in enabled_suites)
        body = body.replace(
            "<h1>统计锁与历史写库隔离 · 自适应追赶对照</h1>",
            "<h1>统计锁与历史写库隔离 · 自适应追赶对照</h1>"
            "<aside><strong>后续启用记录：用户批准后，自适应追赶已接入当前源码的实时统计循环。</strong>"
            f"启用后 {enabled_count} 项相关回归通过；未打包发布。"
            '<a href="enabled_tests.xml">查看测试记录</a>。'
            "以下保留启用前的实验记录，其中“关闭／等用户决定”描述的是当时状态。</aside>",
        )
path = OUT / "历史写库隔离与自适应追赶实测_20261020.html"
path.write_text(body, encoding="utf-8")
print(path)
print(json.dumps({"backlog_sizes": size_rows, "bulk": bulk_rows,
                  "database_block": block_rows, "tests": test_text}, ensure_ascii=False, indent=2))
