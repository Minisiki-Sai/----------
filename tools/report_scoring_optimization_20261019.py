"""Evidence-labelled local scoring experiments; no uploads or re-ranking."""
from __future__ import annotations
import collections
import html
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/verification/scoring_optimization_20261019'
AUDIT = ROOT/'outputs/verification/all_jobs_published_ab_20261019/detailed_attribution'
NAME = '评分准确性可比性与速度优化_实验报告_20261019.html'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def e(value):
    return html.escape(str(value))


def num(value):
    return '—' if value is None else f'{value:,.2f}'


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+e(x)+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+e(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def link(path, title):
    return '<a href="'+e(path)+'">'+e(title)+'</a>'


def main():
    baseline = read(AUDIT/'attribution.json')
    accuracy = read(OUT/'accuracy_reference/native_speed_experiment.json')
    support = read(OUT/'comparability/comparability_experiment.json')
    window = read(OUT/'window_index_benchmark.json')
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>评分优化实验</title><style>body{font:16px/1.75 system-ui,Microsoft YaHei,sans-serif;color:#19353c;background:#f4f8f9;margin:0}main{max-width:1250px;margin:auto;padding:30px}h1{font-size:29px}h2{margin-top:36px;border-bottom:2px solid #c9dddd;padding-bottom:8px}h3{margin-top:25px}.notice{background:#fff6d8;border-left:5px solid #a67512;padding:15px 20px}.good{background:#e7f4ee;border-left:5px solid #23835d;padding:15px 20px}.scroll{overflow:auto;background:white;border:1px solid #ccdcdc;border-radius:8px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px 12px;border-bottom:1px solid #dce8e8;text-align:left;vertical-align:top}th{background:#e7f1f2;white-space:nowrap}a{color:#087779}small{color:#526c73}details{background:#fff;padding:10px 15px;border:1px solid #dce8e8;margin:10px 0}code{background:#e5edef;padding:2px 5px}li{margin:7px 0}</style><main>']
    parts += ['<h1>评分准确性、职业可比性与速度优化</h1><p>2026-10-19 · 第一阶段：可重复的定位、代码实验与精确优化</p>',
              '<div class="notice"><b>这不是新版正式评分，也没有发布或改写云端。</b>样本为12份抓包、96个角色记录、30个职业。其中0808文件包含多轮战斗；这些不是96名独立玩家，不能当全服职业强弱排名。计价实验固定已有理论轴；角色改变后的完整评分尚未重跑。下文严格区分“精确提速”“修正敏感性”和“未闭合问题”。</div>',
              '<h2>一、先给结论</h2>',
              '<p>可以改进，而且主要应修“实际与理论使用的规则是否一致”，不是把某些职业统一加分。已验证的精确优化可进入当前源码；会改变规则的修正保持实验状态，避免把尚未验证的分数推给用户。</p>',
              table(['方向','确认的问题或结果','本轮处理'],[
                  ['输出准确性','部分加速技能理论完整倍率与逐Hit完整路线不一致','六个职业—技能组合接入共享参考的实验；旋风不一致项拒绝启用'],
                  ['辅助准确性','必修辅助存在实际收益>0、理论收益=0；另有合法额外职责奖励','复现当前已有零分母保护；奖励与完成率分开做敏感性分析'],
                  ['职业特色','理论排轴按打坐曜，最终却按输出曜评分','实现统一角色判定范围的关闭状态实验，不扩大个人输出窗口'],
                  ['职业可比性','少量自身样本被用于降低体型理论上限','导出低样本风险，不直接取消真实体型限制，也不按样本均分拉平'],
                  ['速度','相同拟合反复计算、窗口逐项扫描','保持所有候选和原算式，验证完整结果一致；不通过少算Hit来提速']]),
              '<h2>二、怎样的评分才算“手法不同，分数不同”</h2>',
              '<p>每次释放应在当时的同一套标准装备下计价，使用该时刻自己的Buff、怪物Debuff、加速和实际打出的倍率，再逐次累加；理论也逐次计价。缺少Buff可以让同样次数的总伤害变低，但不能额外乘一个无法解释的职业系数。</p>',
              '<p>例如10次完整命中的技能：理论合计1400，实际因漏一个Buff只有1300，那么次数完成率100%、倍率实现率100%、伤害完成率92.86%。这应是逐次1300÷1400的结果，而不是先决定该职业只给92.86%。</p>',
              '<p>辅助则看Buff实际覆盖到了多少有价值的队友输出，而不只看存在多久。T看当前共同作战的对象与队员；奶看可解决的缺血/救急需求。职责判断、排轴、最终评分必须使用一致的角色定义。</p>',
              '<h2>三、输出计价：共享加速后的完整路线</h2>',
              '<p>旧模型对部分技能直接给理论伤害乘1.4，而逐Hit模型在相同40%加速下不一定增加40%的总倍率。例如有固定起手/收尾、循环次数取整的技能，不能把所有段都乘1.4。实验让排轴与实战计价引用同一完整路线，同时保留逐时刻Buff差异；不是给实战免费补一份加速。</p>',
              f'<div class="good">白名单实验核对 {accuracy["reference_checks"]} 次实际参考，{accuracy["reference_check_matches"]} 次吻合；固定轴下涉及 {accuracy["affected_players"]} 个角色记录、{accuracy["changed_theory_casts"]} 次理论释放。</div>',
              '<p>这里的“吻合”指计价参考与Hit模型内部一致，不代表每次游戏中的真实机制都经过独立实测确认。</p>',
              '<p><b>下表不是完整重排后的新分数：</b>释放时机、次数、爆发分、辅助分、职责权重保持旧结果，只替换理论完整伤害参考。它说明问题的量级，不能直接上传为新评分。</p>']
    changed = sorted((r for r in accuracy['players'] if r['affected']), key=lambda r:-r['frozen_other_total_delta'])
    parts.append(table(['抓包','角色 / 职业','涉及技能','原输出分','固定轴实验输出分','对总分的孤立影响'],[
        [r['case'],r['name']+' / '+r['job'],'、'.join(s['skill'] for s in r['skills']),num(r['old_output']),num(r['frozen_burst_output']),f'+{r["frozen_other_total_delta"]:.2f}'] for r in changed]))
    for r in changed:
        parts.append('<details><summary>'+e(r['case']+' · '+r['name']+'：逐技能分母变化')+'</summary>'+table(['技能','原轴理论次数','理论伤害变化（百万）'], [[s['skill'],s['theory_casts'],num(s['theory_delta']/1e6)] for s in r['skills']])+'</details>')
    parts += ['<h3>没有强行修正的两类情况</h3>',
              '<p><b>旋风：</b>初步核对中12次参考不一致。追查发现拟合后处理会把实际额外命中扩进完整分母，因此它不是纯粹的“加速倍率”。已从实验白名单排除，不能简单把观察到的倍率当理论真值。</p>',
              '<p><b>致命狂镰：</b>118次中53次包含额外Hit。例如46.672秒这一发是3次实际命中，2次主段匹配；权重1＋1＋0.4＝2.4，对照主路线1.4得到171.43%。所以不能见到超过100%就截断。额外段的权重归属仍含估计，不把这种估计说成客户端完全确证。</p>',
              '<h2>四、辅助评分：先修分母，再评价释放时机</h2>',
              f'<p>检查{support["summary"]["support_detail_rows"]}条辅助明细：发现{support["summary"]["required_numerator_only_rows"]}条必修效果只有实际收益而没有理论收益，另有{support["summary"]["optional_numerator_only_rows"]}条可选职责效果。必修零分母保护已在当前源码存在，本轮是量化它对发布版的影响，不冒充本轮新修复。</p>',
              '<p>例如某人应提供减抗，理论却根本没安排这一效果，不能把该效果实际收益加进分子，再除以其他技能的小分母。临时排除并标注这条不可比数据，是防止虚高；最终仍应补齐合法理论计划，不能永久把它当作不需要评价的技能。</p><p>以下辅助对照冻结原收益、排轴、角色、权重及扣分，没有重新计算全场评分。</p>']
    affected = sorted([r for r in support['players'] if abs(r['policies']['required_guard']['support_delta'])>1e-8],key=lambda r:r['policies']['required_guard']['support_delta'])
    parts.append(table(['抓包','角色 / 职业','原辅助分','零分母保护后','固定旧权重总分变化','不完整参考条目'],[
        [r['case'],r['name']+' / '+r['job'],num(r['baseline_support']),num(r['policies']['required_guard']['score']),num(r['policies']['required_guard']['total_delta_fixed_weights']), '；'.join(x[0]+' / '+x[1] for x in r['policies']['required_guard']['excluded'])] for r in affected]))
    optional = [r for r in support['players'] if r['policies']['symmetric_optional']['optional_actual']>0]
    parts += ['<h3>可选辅助：额外奖励不等于必修完成率</h3>',
              '<p>输出曜偶尔为团队治疗，可以有额外贡献；但把这种没有理论职责的收益直接混进必修完成率，容易显示120%甚至更高。下面将可选职责按中性100%单独计池、再与必修职责合成；数学上是将可选实际收益同时加入分子和分母。这是改变计算规则的敏感性实验，不是仅改变展示，也不证明实际收益就是理论最优，尚未启用该规则。</p>',
              table(['抓包 / 角色','原辅助分','可选职责对称实验','固定权重总分变化'],[[r['case']+' / '+r['name'],num(r['baseline_support']),num(r['policies']['symmetric_optional']['score']),num(r['policies']['symmetric_optional']['total_delta_fixed_weights'])] for r in optional]),
              '<h3>雷神为什么修完零分母仍可能很高？</h3>',
              '<p>镇恶的精神爆破，实际时间覆盖约68.2%，理论约68.3%，看时间几乎一样；但相关伤害覆盖为91.2%对65.8%。说明Buff是否盖住伤害高峰很重要。必须让辅助规划与最终评分使用同一个逐时刻团队收益目标，再比较“冷却即放”和合法延后。当前证据不能把剩余高分全部认定为玩家异常，也不能以封顶100代替修复理论轴。</p>',
              '<h2>五、职业特色与可比性</h2>',
              '<h3>曜：同一角色不能用两种身份打分</h3>',
              table(['样本','全队区间覆盖率','个人区间覆盖率','原最终角色','理论角色','一致性实验角色'], [[r['case']+' / '+r['name'],num(r['team_coverage']*100)+'%',num(r['personal_coverage']*100)+'%',r['role_before'],r['planner_job'],r['role_experiment']] for r in support['role_scope']]),
              '<p>0821这一例由38.64%变为统一口径的67.57%，会回到理论使用的“打坐曜”。只是角色判定统一，不能把个人输出时间改成全队时间。因为角色变化会重算治疗职责和各项权重，本报告不填写其修正后总分。实验默认关闭，完整并行评分尚需传递实验配置并做整场验证。</p>',
              '<h3>不能用一次低命中证明这个职业只能打这么少</h3>',
              f'<p>现有样本中有{support["summary"]["reduced_body_reference_rows"]}条Boss—技能记录降低了体型理论上限，其中{support["summary"]["low_sample_body_reference_rows"]}条只有1～2次有效释放。这些是风险条目，不是已证明的错误。下一步应结合相同Boss/技能/形态的更多证据，并给出低置信标记；不能把一次未打满的个人结果直接认定为Boss体型上限，也不能一律以无限大Boss惩罚真实打不满的技能。</p>',
              '<p>跨职业比较应固定算法、巢穴、角色职责和装备口径。不同职业同样完成80%的合理职责，应获得接近的职责分；但不应把各职业当前玩家的平均分强行拉成一样。下面只列样本覆盖，不作为职业排名。</p>']
    jobs = collections.defaultdict(list)
    for r in baseline['players']:
        jobs[r['job']].append(r)
    parts.append(table(['职业','样本记录数','本轮加速参考实验影响记录','零分母保护影响记录'], [[job,len(rows),sum(r['job']==job for r in changed),sum(r['job']==job for r in affected)] for job,rows in sorted(jobs.items())]))
    parts += ['<h2>六、效率：先争取零误差，不急着用掉3分预算</h2>',
              '<p>速度误差必须比较同一套修正算法的“精确模式”和“快速模式”，而不是拿4.78和4.85的分差检验。已增加验收工具：每名队员的总分、输出分、辅助分、特色分分别检查≤3分；不能用一项涨、一项降互相抵消，缺人、缺项、角色变化或非有限数字都判失败。</p>',
              table(['已完成测试','原耗时','优化耗时','结果及范围'], [
                  ['96角色窗口查询内核',num(window['before_seconds']*1000)+'毫秒',num(window['after_seconds']*1000)+'毫秒',f'{window["identical_results"]}项精确一致；内核缩短{window["reduction_percent"]:.1f}%，不是整场提速比例']]),
              '<p>Hit拟合性能数据、完整报告等价性和默认开启状态，见下面独立实测附件。所有局部耗时都不能直接相加推算全队评分时间；这轮未宣称已经达到4分钟。</p>']
    perf_index = OUT/'perf/performance_summary.json'
    if perf_index.exists():
        perf = read(perf_index)
        parts.append(table(['测试范围','原耗时（秒）','优化耗时（秒）','缩短','完整结果一致','采用状态'], [
            [r['label'],num(r['before_seconds']),num(r['after_seconds']),num(r['reduction_percent'])+'%',
             '是' if r['equal'] else '否', '当前源码默认开启' if '仅复用' in r['label'] else '组合搜索实验关闭']
            for r in perf['experiments']]))
        parts.append('<p>“595条”“341条”指输入观察记录；分层样本输出316条施放明细和130条技能汇总。两组只复用拟合的结果分别经ABBA重复及分层对照；不是全职业整场评分实测。</p>')
        parts.append('<p>'+link('perf/performance_summary.json','详细测试、全部重复耗时、数据来源与限制')+'</p>')
    else:
        parts.append('<p>'+link('perf/', 'Hit拟合测试原始结果目录')+'</p>')
    parts += ['<h2>七、采用与暂缓清单</h2>',
              '<ul><li>采用：窗口区间索引；结果保持原累加顺序，精确相同。</li><li>采用：同次施放、同一形态内拟合结果复用，当前源码默认开启；不删除路线或施放。相邻权重搜索跨职业测速不稳定，实验默认关闭。</li><li>共享加速完整路线：代码已接入白名单实验，默认关闭；需要完整重排确认次数、技能选择和爆发窗口改变后再启用。</li><li>曜角色一致性：已实现关闭状态实验；需要完整重算治疗职责与权重，并检查并行路径。</li><li>暂缓：旋风参考、可选辅助奖励重构、低样本体型上限自动调整。不能为了“差不到3分”把已知错误保留下来，也不能在证据不足时直接改分。</li></ul>',
              '<h2>八、证据与复现</h2><ul>',
              '<li>'+link('accuracy_reference/native_speed_experiment.json','共享加速参考：逐角色、逐技能、逐次结果')+'</li>',
              '<li>'+link('comparability/comparability_experiment.json','辅助、角色与低样本体型：全部结果')+'</li>',
              '<li>'+link('window_index_benchmark.json','窗口索引：交替重复测速原始记录')+'</li>',
              '<li>'+link('validation.json','回归测试、证据检查与源码校验记录')+'</li>',
              '<li>'+link('../all_jobs_published_ab_20261019/detailed_attribution/全职业v4.78与v4.85逐技能评分变化详解_20261019.html','前次12抓包、30职业发布版对照')+'</li>',
              '</ul><p><small>不修改安装包，不覆盖发布目录，不上传或替换任何云端评分。分数修正的变化与提速误差属于两类不同问题，不能混为一谈。</small></p></main></html>']
    output=OUT/NAME
    output.write_text('\n'.join(parts),encoding='utf-8')
    assert accuracy['reference_checks']==accuracy['reference_check_matches']
    assert len(baseline['players'])==96 and len(jobs)==30
    assert window['mismatches']==0
    print(output)


if __name__=='__main__':
    main()
