"""Read historical field diagnostics only; no replay, scoring, or uploads."""
import hashlib
import html
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/verification'
OUT = BASE / 'midrun_latency_20261021'
LOG = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/overlay_latency.jsonl')
TZ = timezone(timedelta(hours=8))


def epoch(s):
    dt = datetime.fromisoformat(s)
    return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).timestamp()


def value(r, key):
    for part in key.split('.'):
        r = r.get(part) if isinstance(r, dict) else None
    return float(r) if isinstance(r, (float, int)) else None


def elapsed(s):
    return f'{int(s // 60):02d}:{s % 60:04.1f}'


def cell(r, key, scale=1):
    v = value(r, key)
    return '—' if v is None else f'{v / scale:,.2f}'


def table(head, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{html.escape(h)}</th>' for h in head) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def main():
    raw = LOG.read_bytes()
    logs = [json.loads(s) for s in raw.decode('utf-8').splitlines() if s.strip()]
    boundaries = json.loads((BASE / 'opening_trim_20261021/shadow_trim_audit.json').read_text('utf-8'))['rows']
    old = json.loads((BASE / 'voice_latency_1020/field_audit.json').read_text('utf-8'))
    ends = {r['file']: epoch(r['end']) for r in old['captures']}
    result = {'source': str(LOG), 'sourceSha256': hashlib.sha256(raw).hexdigest(),
              'openingExcludedSeconds': 90, 'strictMidrunSeconds': 180,
              'note': 'Historical v4.88 field samples, not validation of subsequently edited source. Threshold-triggered and rate-limited; not incident counts or audio latency.', 'runs': []}
    summary, sections = [], []
    for b in boundaries:
        start, end = b['boundaryEpoch'], ends[b['file']]
        rows = []
        for row in logs:
            t = epoch(row['recordedAt'])
            if start + 90 < t <= end:
                rows.append({**row, 'elapsedSeconds': t - start})
        rows.sort(key=lambda r: epoch(r['recordedAt']))
        bad = [r for r in rows if (value(r, 'decoderQueueDelayMs') or 0) >= 1000]
        strict = [r for r in bad if r['elapsedSeconds'] > 180]
        peak = max(rows, key=lambda r: value(r, 'decoderQueueDelayMs') or 0, default=None)
        strict_peak = max(strict, key=lambda r: value(r, 'decoderQueueDelayMs') or 0, default=None)
        item = {'file': b['file'], 'boundary': b['boundaryLocal'], 'end': datetime.fromtimestamp(end, TZ).isoformat(),
                'samplesAfter90': len(rows), 'queueAbove1sSamplesAfter90': len(bad),
                'queueAbove1sSamplesAfter180': len(strict), 'peakAfter90': peak, 'peakAfter180': strict_peak,
                'samples': rows}
        result['runs'].append(item)
        summary.append([b['file'].replace('.dpscap', ''), len(rows), len(bad), len(strict),
                        elapsed(peak['elapsedSeconds']) if peak else '—', cell(peak or {}, 'decoderQueueDelayMs', 1000),
                        elapsed(strict_peak['elapsedSeconds']) if strict_peak else '未采到 ≥1s', cell(strict_peak or {}, 'decoderQueueDelayMs', 1000)])
        details = [[r['recordedAt'][11:23], elapsed(r['elapsedSeconds']), cell(r, 'decoderQueueDelayMs', 1000),
                    cell(r, 'pendingRawFrames'), cell(r, 'snapshotBuildMs'), cell(r, 'snapshotLockHeldMs'),
                    cell(r, 'snapshotOutsideLockMs'), cell(r, 'decodeBatchWallMs'), cell(r, 'decodeBatchCpuMs'),
                    cell(r, 'statisticsPipeline.identityAndProfileMs'), cell(r, 'npcapReaderLagMs'),
                    cell(r, 'historyWriter.lastWriteMs'), cell(r, 'historyWriter.pendingEvents')] for r in rows]
        sections.append('<details><summary>' + html.escape(b['file']) + f'：90 秒后 {len(bad)} 条 ≥1s 积压样本 / {len(rows)} 条异常日志</summary>' + table(
            ['现场时间', '进本后', '队列落后(s)', '排队包', '快照总耗时(ms)', '持统计锁(ms)', '锁外构建(ms)', '上批解码墙钟(ms)', '上批线程CPU(ms)', '身份/装备阶段最大(ms)', '驱动读取滞后(ms)', '最近写库(ms)', '待写事件'], details) + '</details>')
        print(json.dumps({k: v for k, v in item.items() if k not in ('samples', 'peakAfter90', 'peakAfter180')}, ensure_ascii=False))
        for r in strict:
            print('  ', r['recordedAt'][11:23], elapsed(r['elapsedSeconds']), cell(r, 'decoderQueueDelayMs', 1000), 'pending', cell(r, 'pendingRawFrames'))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'midrun_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    doc = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>1020 竞速中途延迟核查</title><style>
    body{font:16px/1.7 "Microsoft YaHei",sans-serif;background:#f3f7f8;color:#263f47;max-width:1450px;margin:30px auto;padding:0 24px}h1{font-size:27px}h2{font-size:21px}section,details{background:white;border:1px solid #d8e5e8;border-radius:10px;padding:18px;margin:16px 0}summary{cursor:pointer;font-weight:bold;color:#087f89}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #e0eaed;white-space:nowrap}th{background:#edf5f6}strong{color:#a94930}li{margin:8px 0}.note{color:#687e87}</style>
    <h1>1020 竞速：开场之外的解码积压</h1><p>取证日期：2026-10-21；对象：2026-10-20 v4.88 七场现场日志。此次只做核查，不修改评分、抓包或发布版本。</p>
    <section><h2>结论</h2><p><strong>不是只在开场慢。</strong>第三、四、六把和通关场，超过进本 3 分钟后仍有秒级积压。第三把 03:19.2 落后 11.70 秒；第四把 06:36.7 落后 5.42 秒；第六把 04:24.3 落后 7.61 秒；通关场 10:50.1 落后 4.49 秒。</p>
    <p>前 90 秒不列入主明细；另外单列 180 秒后的结果。90～180 秒仍可能是开场追赶延续，不能仅凭时间把它称为新一轮堵塞。</p></section>
    <section><h2>统计边界，避免误读</h2><ul><li>“进本后”从抓包保存的正式进本边界计算，不从悬浮窗出现计算。现场时间为北京时间的日志落盘时间。</li>
    <li>队列落后 = 最新收到包的时间戳 − 最近解码包的时间戳。它不是每句播报的实际晚播时间；没有音频实际开始播放的逐句日志，不能声称对应语音必然晚了相同秒数。</li>
    <li>日志只在处理链路超过 500ms 时记录，且最多约每 10 秒一条。样本数不是独立故障次数，不能计算“多少比例时间在卡”，空档也不代表不卡。</li>
    <li>快照指标、最近完成解码批次、身份维护区间最大值不一定是同一时刻发生的事件，不能把它们相加解释积压。日志本身也可能晚于状态采样。</li>
    <li>这些是发布版 v4.88 的历史现场表现，不能作为后续源码修复已生效或仍失效的验证。</li></ul></section><section><h2>逐场汇总</h2>'''
    doc += table(['场次', '90秒后日志', '≥1秒样本', '180秒后≥1秒样本', '90秒后最严重时点', '落后(s)', '180秒后最严重时点', '落后(s)'], summary)
    doc += '''</section><section><h2>为什么不能只修开场</h2><ul>
    <li>第三把 21:37:23.809：队列 2074 包，落后 11.70s；最近完成的 1024 包批次墙钟 4101ms，但解码线程 CPU 仅 703ms。其余约 3398ms 未计入该线程执行 CPU，可能涉及调度、GIL 或阻塞等待，现有日志不能进一步分摊。单包离线很快不代表现场能及时得到执行机会。</li>
    <li>第四把 21:58:52.651：身份/装备维护阶段最大 4281ms，驱动读取滞后约 1503ms，队列落后 3342ms。这是维护和采集交付都变慢的线索，不证明维护独自造成全部延迟。</li>
    <li>第四把 22:00:52.134：快照总耗时 3293ms，其中持锁约 73ms、锁外构建约 3219ms。只缩短统计锁无法覆盖这一类慢；锁外工作仍可能争用 CPU/GIL，具体占比需独立 CPU/等待采样。</li>
    <li>第六把 22:15:24.497：快照总耗时 5097ms，其中约 5000ms 在锁外。应分别测复制、曲线/表格构建、序列化和调度，不把 5 秒全部说成计算耗时。</li>
    <li>不能据此归因于后台评分。第一把 21:29:05 已积压 2.77s，早于日志记录的首次评分任务创建 21:30:07。写库队列和最近写入耗时是辅助证据，不能单独排除其它时间的数据库压力。</li>
    </ul><p>优先方向：实时解析与播报和重型统计/UI 构建隔离；身份/装备维护继续移出实时路径；快照合并刷新、避免反复全量重建；增加逐句“原包→解析→提醒生成→音频开始”诊断，再用相同输入验证，不丢进本后的机制包。</p></section><h2>全部开场 90 秒之后的异常采样</h2><p class="note">可展开每一场；包含队列不足 1 秒但其它环节超过阈值的样本，以免只关注解码漏掉界面发布迟滞。</p>'''
    doc += ''.join(sections) + '<p class="note">原始日志 SHA-256：' + result['sourceSha256'] + '</p></html>'
    path = OUT / '1020竞速_中途解码积压逐场核查.html'
    path.write_text(doc, 'utf-8')
    print(path)


if __name__ == '__main__':
    main()
