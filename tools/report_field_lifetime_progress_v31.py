"""Current report: all 36 replays, all 30 jobs, no averaging-away failed jobs."""
from collections import Counter
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_profession_attribution_audits import compare
from tools.report_confirmed_coverage_v30 import read,subset
OUT=ROOT/'outputs/compact_entity_selectors_20260908'


def totals(comparison,side):
    c=Counter()
    for row in comparison['jobs']:
        c.update({k:row[side][k]for k in ('deterministic_hits','named_inferred_hits','unknown_hits')})
    c['named_hits']=c['deterministic_hits']+c['named_inferred_hits']
    return dict(c,confirmed_percent=100*c['deterministic_hits']/c['named_hits'])


def main():
    raise RuntimeError('Rejected primary-lifetime experiment. Use report_late_field_v32.py after verify_late_field_corpus_v32.py instead.')
    original=read(ROOT/'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')
    current=read(OUT/'after_field_lifetime36_frozen/profession_direct_damage_report.json')
    previous=read(OUT/'composed36_after_kali.json')
    proof=read(OUT/'field_lifetime_acceptance.json')
    self_proof=read(OUT/'self_target_corpus_acceptance.json')
    summary30=read(OUT/'confirmed_coverage_v30_summary.json')
    assert current['successful_captures']==36 and not current['failures']
    assert proof['physical_conserved'] and proof['real_hit_flags_unchanged']
    assert proof['after_signature']==current['analysis_signature']
    assert self_proof['all_accepted']
    paths={r['capture_path']for r in original['capture_manifest']}
    assert len(paths)==35
    comp=compare(subset(original,paths),subset(current,paths))
    isolated=compare(subset(previous,paths),subset(current,paths))
    assert comp['same_capture_set'] and all(r['same_capture_bytes']for r in comp['capture_checks'])
    assert isolated['all_conserved'] and isolated['captures_with_real_hit_evidence_changes']==0
    a,b=totals(comp,'before'),totals(comp,'after')
    result=dict(schema_version=31,full_latest_replays=36,fixed_comparison_captures=35,
        coverage_definition='confirmed evidence among named real Hits; not ground-truth accuracy',
        before=a,after=b,passed_jobs=comp['jobs_meeting_named_target'],
        all_jobs_passed=comp['jobs_meeting_named_target']==30,jobs=comp['jobs'],
        current_signature=current['analysis_signature'],
        new_lifetime_confirmations_all36=proof['new_confirmed_hits'],
        new_lifetime_named_hits_all36=proof['newly_named_hits'])
    (OUT/'confirmed_coverage_v31_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    last={r['job']:r['before']for r in isolated['jobs']}
    title=('全部30职业均达到95%确认覆盖。' if result['all_jobs_passed']
           else '**全职业95%目标尚未完成，不能用总体平均值代替逐职业达标。**')
    lines=['# 已命名真实 Hit 确认覆盖：最新完整回放验收','',
        f"同一批35份、30个二转职业：总体 {a['confirmed_percent']:.5f}% → {b['confirmed_percent']:.5f}%。",
        f"逐职业≥95%：{result['passed_jobs']}/30。{title}",'',
        '最新版完成36份完整回放，失败0；固定35份用于与原基线比较。另1份补充样本保留在完整报告中，不为提高数字临时增删样本。',
        '这是可追溯证据链的覆盖率，不是逐条人工标注后的准确率。确定性路线包括完整对象链、实体自身技能ID及明确直接技能字段，不是全都走同一种对象链。','',
        f"已命名Hit {a['named_hits']:,} → {b['named_hits']:,}；确认Hit {a['deterministic_hits']:,} → {b['deterministic_hits']:,}；未命名Hit {a['unknown_hits']:,} → {b['unknown_hits']:,} 另列。",
        '分母变化分开解释：已证实重复记录被移除；原先未命名但新证据闭合的Hit被正常命名。没有隐藏或删除未确认Hit来提高比例。','',
        '|职业|旧基线|本次补保留时间前|当前确认覆盖|确认/已命名Hit|还差多少Hit到95%|',
        '|---|---:|---:|---:|---:|---:|']
    for row in comp['jobs']:
        x,y=row['before'],row['after'];n=y['deterministic_hits']+y['named_inferred_hits']
        needed=max(0,math.ceil(n*.95)-y['deterministic_hits'])
        lines.append(f"|{row['job']}|{x['named_coverage_percent']:.2f}%|{last[row['job']]['named_coverage_percent']:.2f}%|{y['named_coverage_percent']:.2f}%|{y['deterministic_hits']}/{n}|{needed}|")
    dup=summary30['duplicate_removal_full36']
    lines+=['','## 本轮补通的原因','',
        '- 天昏地暗5717、怨恨之像5508：此前使用默认8秒对象保留期，实际typed15生成参数为10000ms；天昏地暗技能说明也明确10秒。原始包中第8～10秒仍有同主人、同完整对象ID的命中，因此仅将这两个模板改为10秒。',
        f"- 此保留期修正在36份完整回放中净新增确认{proof['new_confirmed_hits']}次，其中原先未命名、新证据闭合后命名{proof['newly_named_hits']}次。伤害金额、来源、目标、真实命中标记均不变。超出10秒的原帧仍拒绝，例如天昏地暗10.036秒的一条，没有加宽容差凑数。",
        '- 重磅鸭的部分符文攻击：新增实体自身技能槽→AI/DNT/ACT的闭合证据。不拿FFFFFFFF或创建者技能字段代替自身攻击。机械大师前一阶段的提升还包含旧重复记录修复，不能全部声称是纯新增证据。',
        '- 噬魂者11076：新增完整父对象ID、主人、动作/信号和代次约束，前一增量完整回放新增16次确认。去动作、错主、对象复用、重置、截断等反例继续拒绝。','',
        '## 伤害守恒及重复修复','',
        '重复修复阶段36份完整前后回放：14份物理指纹一致，22份有变化并逐条核对。所有唯一物理伤害键保留，删除项均有完整原帧和正确保留行证据。',
        f"原始事件层删除重复{dup['raw_physical_rows']['rows']}条；汇总层删除重复{dup['aggregated_physical_rows']['rows']}条，两层不能相加。单份汇总伤害最大下降{dup['aggregated_physical_rows']['largest_single_capture_damage_change_percent']:.6f}%。",
        '0816的审计时间基准改为重组后的首个有效逻辑包，与正式解析相同；没有采用时间模糊匹配。',
        '本次保留期修正没有再增删任何物理伤害；36份原始/汇总伤害多重集和真实命中标记全部一致。','',
        '## 验证记录','',
        '- 相关协议、对象生命周期、召唤物、原始伤害及验收测试：2585通过、7个已有预期失败；新增审计时间基准2项通过，共2587项通过。',
        '- 保留期真实原帧及负例15项、重复验收11项均已包含在上述计数中，不重复计数。',
        '- 最新完整逐职业、逐技能、归因路线明细：after_field_lifetime36_frozen/profession_direct_damage_report.json。',
        '- 保留期A/B：field_lifetime_acceptance.json、field_lifetime36_comparison.json。',
        '- 重复逐条证据：self_target_corpus_acceptance.json、self_target_pair/*_comparison.json。',
        '- 固定副本与数据哈希均校验；累计旧基线跨过其他任务的治疗模型变更，不把累计变化冒称为解析器单变量实验。本轮各隔离A/B数据完全一致。','',
        '## 仍未确认的主要原因','',
        '- 岩浆墙EX32022：正常EX根动作能解出，但具体出生缺独立创建选择键，静态Clone入口未闭合；不能将整个模板直接升为确认。',
        '- 掌握/痊愈等圣物：部分自身技能槽为FFFFFFFF，实体却存在多种可能攻击动作；创建技能和最近施法都不足以证明是哪种攻击。',
        '- 部分玩家直接攻击没有找到同主人、同代的完整对象注册。三个圣骑士原帧锚点首次出现就是直接Hit，后续同号09属于其他主人，已作为跨主人反例保留；这不证明其他编码形式的证据一定不存在。',
        '以上均保留未确认，不据旧技能名、伤害大小或时间邻近强行填写。程序功能未打包、未上传评分；全职业95%不标记为完成。','']
    (OUT/'RUNTIME_RESULT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items()if k not in('jobs','current_signature')},ensure_ascii=False))


if __name__=='__main__':main()
