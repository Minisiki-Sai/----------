"""Report inspected current paths separately from historical live evidence."""
import html
import json
from pathlib import Path
from statistics import mean
from tools.report_sea_support_timeline_1019 import table

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/"outputs/verification/decode_backlog_causes_20261020"
ab = json.loads((OUT/"marker_cache_ab.json").read_text(encoding="utf-8"))
old = mean(x["elapsed_seconds"] for x in ab["results"] if x["capacity"] == 16)
new = mean(x["elapsed_seconds"] for x in ab["results"] if x["capacity"] == 128)
body = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>解包积压：新增原因核查与优化方案</title>
<style>body{max-width:1200px;margin:30px auto;padding:0 24px;font:16px/1.8 system-ui;color:#263e47}
h1,h2{color:#006b78}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border:1px solid #cbdadd;padding:10px;text-align:left;vertical-align:top}th{background:#eaf4f5}
aside{background:#fff2db;padding:16px}code{background:#edf3f4}</style>
<h1>解包积压：新增原因核查与优化方案</h1><p>2026-10-20｜诊断及独立实验；没有修改生产逻辑、评分、抓包端口、安装包或云端。</p>
<aside>历史现场日志来自v4.87；源码是当前已含上一轮修复的版本。不能用旧日志宣称新版本仍有相同延迟。
本轮没有游戏现场重现8秒可听延迟。以下严格区分：现场堵塞位置、当前代码风险、独立实验结果。</aside>
<h2>1．现场能确认堵在哪里</h2>"""
body += table(["样本/时刻（抓包首帧起算）","解码水位落后","网卡读取滞后指标","原始/已解码队列","含义"],[
    ["第十四把约262秒","13,684ms","7ms","5,489 / 1,427","至少该采样时网卡读取正常，主要积压已在应用内部；不能全归因于一把统计锁。"],
    ["第十四把约272秒","13,428ms","27ms","3,891 / 3,014","原始队列下降，但统计队列上升，两个阶段需要分别诊断。"],
    ["第十一把约104秒","8,126ms","77ms","1,902 / 485","快照当次518ms，不能直接解释全部8秒。"],
    ["第十九把约294秒","6,041ms","3,533ms","2,383 / 475","上游读取也有迟到：可能涉及线程调度/GIL/驱动交付等，旧日志不足以唯一确定。"],
])
body += """<p>上述多次采样解码线程已经是normal优先级。189条记录样本的原始队列与事件队列totalSpilled均为0：
这些已记录样本不支持“溢写磁盘造成积压”的判断；也不等于证明整场没有任何I/O或调度等待。
水位差是两个包时间戳之差，不是逐包测出的CPU耗时，也不是音频播放延迟。</p>
<h2>2．当前仍存在的优化入口</h2>"""
body += table(["优先级","代码确认的现象","为什么会影响实时性","建议及证据边界"],[
    ["P0：先补分段观测","已有批次wall/CPU，但尚缺原始队列中转、逐包完成、统计锁等待、SQLite耗时等分段","单包16ms不包含等待被调度、取包、前面的包、发布及音频路径","记录入队→中转就绪→解码开始/结束→轻量事件发布→统计消费；慢样本记录线程CPU、GC、锁等待。采样记录，不逐包同步写日志。"],
    ["P1：统计锁内I/O","_run持_collector_lock调用collector.poll；后者history.append先于aggregator.ingest，满1000事件或3秒flush/commit；身份持久化也在同一锁作用域调用","数据库慢会推迟统计消费和悬浮窗拿锁；不会直接等价为解码线程在等待这把锁","历史存储迁到专属写线程，SQLite连接由该线程创建与使用；统计先应用有序事件再提交不可变存储任务；Stop完整排空，不丢历史。不声称已测得现场SQLite卡了8秒。"],
    ["P1：队列中转和逐条交接","Npcap先进入AsyncSpillQueue staging，再由另一个Python线程逐条put到inner；解码后又逐事件入队","多一层必须获得执行机会的中转；内存充足时也有逐条锁和统计更新。总pending无法区分在staging还是inner","先暴露两段队列最老等待；再做批量移交/发布，保留严格FIFO和溢写次序。没有证据可直接宣称这一层贡献了多少秒。"],
    ["P1：同进程执行时间竞争","读包、解包、统计、悬浮快照与时间轴仍有同一Python进程中的计算；锁外计算不等于无竞争","普通带GIL构建下Python计算竞争执行时间；网卡读完后回到Python也要获得执行机会","短期合并相同revision投影、增量缓存、对非实时任务限预算；仍有长尾则把有序解码/轻量事件独立进程，分离展示计算。不能仅靠增加Python解析线程。现场具体竞争者仍待采样。"],
    ["P2：重复包结构检查","第十一把43893次feed中marker探测调用约15.86万次、_decode_v576_body约8.34万次（含缓存命中）","模块独立验证同一个包会重复找标记/走结构；存在缓存，不能把调用次数当实际解密次数","引入单包上下文共享已验证相位/边界；缓存键保留payload/方向等必要条件；有序状态提交不动，未知包仍留原档。需逐事件等价测试。"],
    ["P2：缓存容量抖动","正则模式LRU只有16项，本样本实际用18项，造成4681次构建miss","有额外重复工作，但量级不足以解释秒级卡顿","128容量诊断对照miss降到18，输出一致；实际速度结果见下一节。不作为解决8秒延迟的主方案。"],
    ["P2：有限批次＋固定休眠","统计每次最多512事件，循环固定先等40ms；实际还有ingest、历史和身份处理","12800事件/秒只是忽略所有工作耗时的上界；已有积压时每轮固定休眠延缓追赶","改为有积压时按毫秒预算自适应消费，及时释放统计锁、让出执行时间。禁止一次持锁清空全部队列，也不能靠丢旧事件追上。"],
])
body += """<h2>3．缓存试验：减少了浪费，但没有证明明显提速</h2>
<p>独立诊断进程、ABBA顺序、同一份第十一把；修改只在诊断进程内有效。每轮清理协议缓存，逐事件类型和全部字段计算摘要。</p>"""
body += table(["缓存项数","回放耗时（秒）","模式构建miss","事件数","输出摘要"],
              [[r["capacity"],f'{r["elapsed_seconds"]:.3f}',r["cache"]["misses"],r["events"],r["digest"][:16]] for r in ab["results"]])
body += f"""<p>16项平均{old:.3f}秒，128项平均{new:.3f}秒，约改善{(old-new)/old*100:.1f}%；
轮间波动大于改善幅度，不能声称稳定提速。四轮24926事件摘要完全相同。这是可低风险清理的重复计算，不是秒级积压的主因证明。</p>
<h2>4．排除了一条看起来很重、实际不在当前热路径的代码</h2>
<p>完整snapshot(include_events=False)仍做伤害归因与整场状态区间整理；本机第十一把不带分析器的采样耗时约193–411ms。
但当前抓包主循环只发布轻量投影，_queue_page_snapshot没有实时调用点；完整评分源也被活动抓包保护。
因此不把这项耗时列为当前持续抓包积压原因。它只提醒今后不要重新接回实时热路径。</p>
<h2>5．建议实施顺序与验收</h2>
<ol><li>先补低开销分段时间和慢样本：以本地monotonic测排队等待，同时保留包时间戳。将“已取出”与“解析完成”分开，避免当前解码水位在feed开始前就推进而掩盖慢包。</li>
<li>移走统计锁内的数据库I/O，合并队列逐条交接。保持伤害/Buff/职业/召唤归因逐事件完全一致。</li>
<li>减少重复投影、按数据revision增量处理；做共同包上下文和缓存优化。不要降低悬浮窗关键状态变化和语音事件的优先级。</li>
<li>若wall明显大于线程CPU且现场仍有长尾，再做进程隔离：有序解析在独立进程，立即播报走轻量IPC；完整统计和图表独立消费。不按UDP/TCP拆两条会乱序的状态机。</li></ol>
<p>验收应比较同一抓包、同一负载多轮的P50/P95/P99/最大排队延迟，外加丢包计数、事件逐字段一致性、每个语音事件次数与时间戳。
分别测安静、游戏高负载、上一把历史整理与下一把重叠。可设目标P99应用内排队&lt;100ms、超过500ms有可定位原因；
这是验收目标，不是当前已达到的成绩。禁止为了数字好看缩窄端口、丢旧包或永久关闭GC。</p>
<p>原始实验：<a href="marker_cache_ab.json">缓存ABBA</a>；
<a href="1019噬魂竞速第十一把.json">函数热点与快照诊断</a>。
历史现场证据在../voice_latency_1019/live_logs.json；上一轮已完成修复不在本轮重复计功。</p></html>"""
path = OUT/"解包积压新增原因与优化方案_20261020.html"
path.write_text(body, encoding="utf-8")
print(path)
