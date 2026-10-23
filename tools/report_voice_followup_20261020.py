"""Read saved diagnostic evidence; create a follow-up report. No live changes."""
import html
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/verification/voice_latency_followup_20261020"
OLD = ROOT / "outputs/verification/voice_latency_1019"
replay = json.loads((OUT / "1019噬魂竞速第十一把.json").read_text(encoding="utf-8"))
live = json.loads((OLD / "live_logs.json").read_text(encoding="utf-8"))
case = next(r for r in live if r["file"] == replay["file"])
signal = next(r for r in replay["signals"] if r["skill"] == 50456)
row = next(r for r in case["rows"]
           if abs(r["decodedFrameTimestamp"] - signal["packet_wall_seconds"]) < 1e-6)
control = json.loads((OUT / "controlled.json").read_text(encoding="utf-8"))
assert row["captureToDecodeMs"] > 8000
evidence = {
    "capture": replay["file"],
    "signal": signal,
    "matching_live_sample": row,
    "controlled_tests": control,
    "limitations": [
        "No historical audio-start or per-stage stack trace exists.",
        "Capture-to-decode is sampled immediately before frame decoding, not at audible output.",
        "A large overlay snapshot duration is not proof that its lock directly blocked the decoder.",
        "Controlled timer tests assume a responsive consumer and an already-known cast anchor.",
    ],
}
(OUT / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


def table(headers, rows):
    return "<table><tr>" + "".join(f"<th>{html.escape(str(h))}</th>" for h in headers) + "</tr>" + "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(x))}</td>" for x in r) + "</tr>" for r in rows
    ) + "</table>"


body = """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>语音延迟追查：触手8秒、倒计时与再次进本</title>
<style>body{font:16px/1.8 system-ui;max-width:1160px;margin:32px auto;padding:0 24px;color:#203a44}
h1,h2{color:#006b75}table{border-collapse:collapse;width:100%;margin:18px 0}th,td{border:1px solid #ccd9dc;padding:10px;text-align:left}
th{background:#edf5f6}aside{background:#fff4dc;padding:16px}code{background:#eef3f5;padding:2px 5px}li{margin:8px 0}</style>
<h1>语音延迟追查：触手8秒、倒计时与再次进本</h1>
<p>2026-10-20。依据：19场原有现场日志、本次重新回放第十一把、当前代码及独立静音调度测试。
本次只新增诊断脚本和报告；没有修改生产代码、安装包、语音设置或云端。</p>
<aside>这次能把第十一把触手动作包的时间戳与现场解码水位精确对上：该帧进入解析前已经等待约8.184秒。
但没有当时的发声时间或慢线程堆栈，不能声称已经定位了全部8秒的内部函数归属。</aside>
<h2>一、触手8秒：不是技能识别本身计算了8秒</h2>"""
body += table(["证据", "数值"], [
    ["抓包", replay["file"]],
    ["技能", "50456 · 天启触手 · structured_action"],
    ["相对抓包起点", f'{signal["timestamp"]/1000:.3f} 秒'],
    ["原始帧时间", datetime.fromtimestamp(signal["packet_wall_seconds"]).isoformat()],
    ["现场采样时间", row["recordedAt"]],
    ["现场已开始处理帧的时间戳", datetime.fromtimestamp(row["decodedFrameTimestamp"]).isoformat()],
    ["采集至开始解码", f'{row["captureToDecodeMs"]:.3f} ms'],
    ["采样时原始帧队列", row["pendingRawFrames"]],
    ["驱动读取延迟", f'{row["npcapReaderLagMs"]:.3f} ms'],
    ["该轮悬浮快照构建/等待", f'{row["snapshotBuildMs"]:.3f} ms'],
    ["该次解码线程优先级", row["decoderPriority"]],
    ["本次离线此逻辑包至语音事件发布", f'{signal["packet_to_publish_ms"]:.3f} ms'],
])
body += """<p>原文件中该时间戳只有一个普通采集帧（第26526条、网卡3、241字节）；动作由现有协议解码器识别。
因此这是“包早就录到了，现场解码线程晚处理”的直接证据，不需要等玩家被触手打中才识别。
上述8.184秒不是声卡耗时，也不是仅由倒计时造成。</p>
<p>本次第十一把离线回放43893个逻辑包；最慢包55.249ms发生在第1个TCP包，触手事件约1.296ms。
前次19场测试最慢16.7ms只代表那次无游戏、无页面、无评分竞争的feed_events测量，不是实时全链路上限。</p>
<h2>二、为什么单包很快仍能积压</h2>
<p>端到端时间＝驱动交付等待＋原始队列等待＋重组/解析/事件交付＋HTTP/页面等待＋音频调度/起声。
旧离线计时只包住feed_events，不包括前后的排队、端口/网卡刷新、线程调度和实际页面。</p>
<ul><li>解码器独立线程不等于独立CPU或独立Python解释器。同进程的快照、实现率、深拷贝等工作仍可争用GIL；游戏与后台工作也会争用CPU。
此包前后各1秒分别录到497和421个普通原始帧，线程只要短时得不到足够运行时间，就能堆积数百至数千帧。原始帧数量不等于逻辑包数量。</li>
<li>实时解码循环还执行feed_events以外的工作，例如端口/网卡刷新、身份信息交付、事件入队。仅测feed_events无法排除这些阶段等待。</li>
<li>悬浮快照持有_collector_lock，统计消费循环也需要它，能直接拖慢状态和时钟更新。
但原始帧解码循环并不直接取得这把锁，不能把“快照1957ms”简单说成“解码被同一把锁锁住1957ms”。</li>
<li>第十一把8秒样本中解码优先级已经是normal，而该轮快照耗时只有518ms；同场9.795秒快照发生在22:42:35，不能拿那个峰值解释22:41:38的触手。</li>
<li>后台实现率线程没有实时抓包暂停检查；当天22:40:06有对应HTTP响应连接中断日志。代码证明它存在继续运行的风险，
但连接中断日志没有CPU堆栈，不能单独证明它就是此次积压的唯一原因。</li></ul>
<p>第14把357.860秒拉冰附近的110帧、1182ms水位差、1957ms快照构建/等待，同样是不同指标，不能互相相加或用110×最慢16.7ms当作真实耗时。</p>
<h2>三、单调时钟锚点的收益与边界</h2>
<p>当前内部已有单调时钟辅助，但voice_status返回的是统计循环上次写入的elapsedSeconds；前端又仅在接口返回后调用due。
因此“计算方式里用了monotonic”并不等于倒计时已经独立推进。</p>
<p>建议保存已对齐的战斗时间t₀和单调时刻m₀，当前战斗时间按t₀＋(m−m₀)计算；
截止时刻仍为原始施放时间＋预警间隔，不能变成收到迟到包后再完整等75秒。跨进程不能直接混用不同来源的单调时钟数值，需映射或交换剩余时长。</p>"""
body += table(["受控条件（不是昨晚声卡实测）", "现有冻结时间输入", "独立锚点输入"], [
    ["战斗174秒后统计停更2.2秒，175秒应预警", "176.2秒才报，迟1.2秒", "175秒触发"],
    ["同一条件停更8秒", "恢复时晚7秒，超过5秒有效期而丢弃", "175秒触发"],
])
body += """<p>测试调用真实SeaReminderClock，仅模拟时间输入；消费端保持响应。收益是消除“统计停更造成的倒计时停摆”。
它不能提前得知尚未解析到的技能、死亡或血量变化，也不能在主页面被阻塞时自动播放；后者需独立调度/播放运行环境。</p>
<p>必须保留会话隔离、Boss死亡清理、阶段结束停表和血量触发CD缩短。状态通道不健康时需明确过期提醒策略，
不能为了准点而在未收到死亡消息时无条件继续报。不能仅把时钟改成Date.now。</p>
<h2>四、同批多条预警：确实没有完全并行</h2>
<p>当前播放器先把texts扁平化成音频片段，再用when += buffer.duration依次安排。
两个独立play调用可以重叠；一次play里传两条独立提醒则串行。现有单元测试只验证了前者。</p>"""
body += table(["调用/测试", "第一句开始", "第二句开始"], [
    ["本次静音stub：同一个数组，两段各1秒", "10.000s", "11.000s"],
    ["本次静音stub：两个独立调用", "10.000s", "10.000s"],
    ["前次真实AudioContext静音测试：Vivian 六芒星已好＋马上拉冰", "0ms", "960ms"],
    ["前次同样测试：Uncle_Fu", "0ms", "1680ms"],
])
body += """<p>这个组合是受控例子，不声称昨天实际同时说了这两句。正常100ms送达的19场模型未找到普通海龙HP/CD提醒同批的样本；
若页面或接口晚返回，多条到期提醒仍可能聚成一批。此结构问题可额外放大延迟，但不能替代第十一把原始队列积压的证据。</p>
<p>正确语义：不同机制/不同目标的独立提醒同时起播、不互相打断；同一句“注意拉冰，前往龙左侧安全区”的内部片段仍按顺序。
分句不能全扁平化；音量混音需避免并发叠加削波，但不应靠把独立提醒排队解决。</p>
<h2>五、重新进本后悬浮窗很晚出现</h2>
<p>这与语音延迟可能共享资源争用，但还有明确的独立串行门槛：</p>
<ol><li>_auto_loop检测上一把_thread.is_alive()时直接continue，不恢复进本监听。</li>
<li>旧线程需排空尾部包、冻结快照、处理历史及清理后才能退出。即使UI先显示stopped，线程仍可能在finish_history。</li>
<li>退出后自动结束路径暂停1秒，然后才恢复轻量探测；手动停止另有30秒暂停，不能混为一谈。</li>
<li>前端依赖每秒captureStatus轮询发现新会话。同一时间只允许一个刷新请求，旧请求不返回就无法检测下一次开始。</li>
<li>显示悬浮窗之前再次请求状态，并在starting时最多检查40轮、每轮等待100ms且还要等待HTTP响应。因此40×100ms不是整个显示流程最多4秒。</li></ol>
<p>这些路径能解释为什么连续进本比第一把更容易慢，但旧记录没有完整的“进图包→自动确认→新会话running→窗口显示”时间线，
不能断言你观察到的每次几十秒延迟都由同一处造成。本次回放初始化7.742秒也只是当前诊断进程初始化，不冒充昨晚启动时间。</p>
<h2>六、建议的验证与修复顺序</h2>
<ol><li>先记录每个预警的帧时间、解码入队/出队单调时间、发布、页面接收、音频计划起播；记录解码循环各阶段和慢时堆栈。
必须把队列等待与实际计算分开。</li>
<li>把统计之外的倒计时推进和独立提醒并行调度分别修正，增加同批/分批、死亡、换本、低血量CD缩短测试。</li>
<li>抓包期间对上一把后台实现率和重投影施加可验证的资源隔离或协作暂停；不丢包、不缩端口、不乱序拆协议。</li>
<li>将重新进本监听从上一把历史整理中解耦，保证新旧会话事件归属；窗口生命周期使用轻量状态，不等待大页面数据。</li>
<li>在游戏与上一把后台任务同时运行时做端到端验收。离线几毫秒不能代替现场分位数、最大值与漏报率。</li></ol>
<p><a href="evidence.json">现场精确对齐证据</a> · <a href="controlled.json">受控时钟/并行测试</a> ·
<a href="1019噬魂竞速第十一把.json">本次重新回放</a></p></html>"""
(OUT / "语音延迟四项追查_20261020.html").write_text(body, encoding="utf-8")
print(OUT / "语音延迟四项追查_20261020.html")
