import html
import json
from pathlib import Path
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/verification/voice_latency_1019'
live=json.loads((OUT/'live_logs.json').read_text(encoding='utf-8'))
reports=[json.loads(p.read_text(encoding='utf-8')) for p in OUT.glob('1019*.json')]
names={50318:'狮蝎·飞天',325423:'狮蝎·飞天砸（跳）',50311:'狮蝎·砸地',50313:'狮蝎·黑洞',50408:'天启·旋转激光',50409:'天启·旋转激光',50456:'天启·触手',50457:'天启·地面激光',50458:'天启·地面激光',50459:'天启·地面激光',50411:'天启·六芒星',50407:'天启·旋转',50460:'天启·连续追踪',325447:'海龙·冰眼',325450:'海龙·龙息'}
matches=[]
for data in reports:
    context=next(r for r in live if r['file']==data['file'])
    origin=data.get('origin',context['origin'])
    for signal in data['signals']:
        if not signal['skill']:continue
        stamp=signal.get('packet_wall_seconds',origin+signal['timestamp']/1000)
        for row in context['rows']:
            if row.get('pendingRawFrames',0)>0 and row['decodedFrameTimestamp'] < stamp <= row['rawFrameTimestamp']:
                matches.append(dict(file=data['file'],skill=signal['skill'],name=names.get(signal['skill'],str(signal['skill'])),
                                    signal_seconds=signal['timestamp']/1000,log_time=row['recordedAt'],
                                    packet_time=datetime.fromtimestamp(stamp).isoformat(),
                                    sample_after_packet_ms=(row['wall_timestamp']-stamp)*1000,
                                    decoder_queue_ms=row['decoderQueueDelayMs'],pending=row['pendingRawFrames']))
summary=dict(captures=len(reports),packets=sum(r['packets'] for r in reports),signals=sum(len(r['signals']) for r in reports),
             max_packet_ms=max(r['packet_ms']['max'] for r in reports),
             max_signal_ms=max(r['signal_publish_ms']['max'] for r in reports),
             live_samples=sum(r['logged_samples'] for r in live),
             captures_with_queue_over_500=sum(r['queue_over_500']>0 for r in live),
             samples_queue_over_500=sum(r['queue_over_500'] for r in live),
             local_loss_statuses=sorted(set(r['capture_integrity']['status'] for r in reports)),
             signal_queue_candidates=matches)
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False),flush=True)
def esc(value):return html.escape(str(value))
def table(headers,rows):
    return '<table><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in r)+'</tr>' for r in rows)+'</table>'
body='''<!doctype html><meta charset="utf-8"><title>1019噬魂海龙语音延迟核查</title><style>body{font:16px/1.7 system-ui;margin:35px auto;max-width:1250px;color:#233d45;padding:0 20px}table{border-collapse:collapse;width:100%;margin:18px 0}th,td{border:1px solid #ccdadd;padding:8px;text-align:left}th{background:#eaf4f3}aside{padding:16px;background:#fff5df}h2{margin-top:32px}small{color:#54686e}</style><h1>1019噬魂海龙：语音延迟核查</h1><p>范围：19份原始抓包、昨天21:39–23:43的本机延迟日志，以及当前播放代码的独立静音浏览器测试。抓包和当日后端日志均确认v4.87。本次只新增诊断工具，未修改生产逻辑、打包或上传。</p><aside>结论：现场存在真实的捕获/解码积压；但旧日志没有“某句语音开始发声”的时刻，因此不能给每句话补造端到端延迟，也不能把队列峰值直接说成语音迟了同样久。离线解码没有复现1秒的单包计算耗时。精确阻塞函数仍缺现场堆栈。</aside>'''
body+='<h2>1．全部抓包回放</h2><p>逐包通过现有有序解码器，记录收到逻辑包到发布轻量语音事件的耗时。每份独立进程，不运行全队评分、页面或真正发声。回放包含时间回退/补录，因此不把按时间戳模拟的队列等待当作现场延迟。初始化耗时与打本中的单包耗时分开。</p>'
body+=table(['项目','结果'],[['抓包数',len(reports)],['逻辑包数',summary['packets']],['语音通道事件（含血量状态）',summary['signals']],['最慢单包解析',f"{summary['max_packet_ms']:.2f} ms"],['包内发布语音事件最慢',f"{summary['max_signal_ms']:.2f} ms"],['采集完整性',', '.join(summary['local_loss_statuses'])]])
body+='<h2>2．昨天实际运行的日志</h2><p>以下是抓包时间窗口内的异常采样，不是连续监控。只在某项超过500ms时记录，并限频10秒一次；不能据此计算卡顿占比。快照耗时包含等待锁和调度，不等于纯CPU耗时。较大峰值可能发生在进图/切关，并不表示每次技能都晚了这么久。</p>'
body+=table(['抓包','日志样本','解码队列最大落后(s)','驱动到读取最大延迟(s)','快照构建/等待最大(s)'],[[r['file'],r['logged_samples'],f"{r['maxima']['decoderQueueDelayMs']/1000:.3f}",f"{r['maxima']['npcapReaderLagMs']/1000:.3f}",f"{r['maxima']['snapshotBuildMs']/1000:.3f}"] for r in sorted(live,key=lambda r:r['first'])])
body+='<h2>3．技能与积压窗口对齐</h2><aside>最接近用户描述的例子：第14把357.860秒的325431海龙拉冰动作，原包时间约23:00:51.734。23:00:52.894的现场采样显示解码时间水位仍在23:00:51.515，队列尚有110个包，落后1182ms；该轮悬浮状态构建/等待为1957ms。这直接说明当时链路有约1秒积压，但不能声称音频精确迟了1160ms。</aside><p>筛选条件：该动作包时间落在采样的“已解码时间水位”之后、“最新捕获时间水位”之前，且队列非空。这是可能尚在队列中的动作事件，不是已证实的音频延迟。多网卡、补录和重传使时间水位不是逐包完成凭证，必须保留此限制。</p>'
body+=table(['抓包','技能','抓包内秒数','日志时间','采样时距包时间(ms)'],[[m['file'],m['name'],m['signal_seconds'],m['log_time'],round(m['sample_after_packet_ms'])] for m in matches]) if matches else '<p>本轮稀疏日志没有直接命中可识别动作包的积压区间；不能把相邻异常强行归因到某一句。</p>'
body+='''<h2>4．为什么独立语音仍可能晚</h2><ol><li><b>即时语音仍依赖有序解码先读到该包。</b>它绕过了统计快照，但并不绕过解码前的队列。现场日志已证明这一段会落后。多线程不等于不同Python线程互不争用；后台实现率任务也没有因开始下一把而自动暂停。昨天22:29:55、22:40:06仍有上一阶段实现率HTTP请求被客户端取消的日志，但不能由此证明任务就是全部积压的原因。</li><li><b>倒计时不是独立走时。</b>前端reminders.due使用voice_status返回的_active_effect_elapsed_seconds；该值由采集统计循环更新，循环需要_collector_lock。悬浮快照持同一把锁，构建或锁等待很久时，倒计时也可能变迟。</li><li><b>播放仍在ACT主页面。</b>页面每100ms轮询；主页面卡顿、HTTP响应等待都会影响语音。用户确认没有最小化，因此没有把最小化当成原因。</li><li><b>同批多条预警实际串行播放。</b>单独的play调用可重叠，但一次play(texts)内部仍用when += buffer.duration。一批两句话时，第二句会排在第一句后。</li></ol>'''
browser=json.loads((OUT/'browser.json').read_text(encoding='utf-8'))
body+='<h2>5．独立播放验证（静音，不操作现有ACT）</h2><p>真实浏览器AudioContext，音量0。这里测到播放调度，不包含声卡/扬声器输出；人工阻塞测试只是验证结构，不代表昨晚发生了相同的具体页面任务。</p>'
body+=table(['音色','冷启动调度(ms)','预加载后(ms)','同批第二句排队(ms)'],[[voice,next(r['request_to_schedule_ms'] for r in browser['samples'] if r['voice']==voice and r['mode']=='cold'),round(next(r['request_to_schedule_ms'] for r in browser['samples'] if r['voice']==voice and r['mode']=='warm'),2),round(next(r['starts'][1]['scheduled_ms'] for r in browser['samples'] if r['voice']==voice and r['mode']=='batched'))] for voice in ['Vivian','Serena','Uncle_Fu','Dylan']])
body+='<p>同批测试句子为“六芒星已好”与“马上拉冰”，是受控测试，不声称此组合昨天实际同时发生。19场在理想100ms送达模型下未发现普通海龙HP/CD预警同时成批（不含暗影独立时钟），所以串行问题是潜在放大因素，不列为昨天全部延迟的直接原因。四种音色全部资源的首个超过−45dBFS的10ms音频块，最晚110ms。5次只读网卡枚举为18–28ms，未复现秒级耗时。GC较长暂停出现在新进程初始化阶段，不能据此归因于打本中GC。</p>'
body+='<h2>6．建议的修复与验证顺序</h2><ol><li>补齐“原包到达→解码开始→事件发布→前端收到→音频调度”时间及慢阶段堆栈，区别队列、锁、GIL、页面和音频等待。</li><li>让倒计时使用单调时钟锚点独立推进，死亡、阶段结束和HP缩短冷却规则仍保持有效。</li><li>实时播报从主统计页面分离；独立预警不在同一个数组中排队，复合短句保持内部顺序。</li><li>实时抓包期间隔离/暂停非必要的上一把后台实现率任务，采集与有序解码保留；先做端到端测量再决定是否拆到独立进程。不靠丢包或缩窄端口换速度。</li></ol><h2>证据文件</h2><p><a href="summary.json">汇总与候选事件</a> · <a href="live_logs.json">19场现场日志</a> · <a href="browser.json">浏览器静音测试</a> · <a href="dependencies.json">音频起声与网卡枚举</a></p>'
body+='<small>本报告区分了已证实事实、可复现结构问题和待定位原因。没有声称已完整复现昨天每次实际发声，也没有修改生产逻辑。</small>'
(OUT/'1019噬魂海龙语音延迟核查.html').write_text(body,encoding='utf-8')
