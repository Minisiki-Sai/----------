"""Report the fixed corpus and per-job gates, only after physical acceptance."""
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
    original=read(ROOT/'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json')
    current=read(OUT/'after_late_field36_final/profession_direct_damage_report.json')
    previous=read(OUT/'composed36_after_kali.json')
    proof=read(OUT/'late_field_acceptance.json')
    dup=read(OUT/'self_target_corpus_acceptance.json')
    assert current['successful_captures']==36 and not current['failures']
    assert proof['physical_conserved'] and proof['real_hit_flags_unchanged'] and proof['newly_named_hits']==0
    assert proof['after_signature']==current['analysis_signature'] and dup['all_accepted']
    paths={r['capture_path']for r in original['capture_manifest']}
    assert len(paths)==35
    comp=compare(subset(original,paths),subset(current,paths))
    isolated=compare(subset(previous,paths),subset(current,paths))
    assert comp['same_capture_set'] and all(r['same_capture_bytes']for r in comp['capture_checks'])
    assert isolated['all_conserved'] and isolated['captures_with_real_hit_evidence_changes']==0
    a,b=totals(comp,'before'),totals(comp,'after')
    result=dict(schema_version=32,full_latest_replays=36,fixed_comparison_captures=35,
        coverage_definition='confirmed evidence among named real Hits; not labelled ground-truth accuracy',
        before=a,after=b,passed_jobs=comp['jobs_meeting_named_target'],
        all_jobs_passed=comp['jobs_meeting_named_target']==30,jobs=comp['jobs'],
        current_signature=current['analysis_signature'],
        post_admission_new_confirmations_all36=proof['new_confirmed_hits'])
    (OUT/'confirmed_coverage_v32_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    last={r['job']:r['before']for r in isolated['jobs']}
    lines=['# 确认Hit覆盖：最新完整回放结果','',
        f"固定35份抓包、30个二转职业：总体 {a['confirmed_percent']:.5f}% → {b['confirmed_percent']:.5f}%；逐职业≥95%：{result['passed_jobs']}/30。",
        ('全部30职业通过95%门槛。' if result['all_jobs_passed'] else '**全职业95%目标尚未完成，不能用总体平均值代替逐职业达标。**'),'',
        '当前冻结版本完成36份完整回放，失败0；35份用于旧基线比较，另1份补充样本也完整验收，不临时增删样本提高比例。',
        '确认覆盖率指可追溯证据链覆盖，不是逐条人工标注后的准确率；完整对象链、实体自身技能ID、明确直接技能字段分别统计。','',
        f"确认Hit {a['deterministic_hits']:,} → {b['deterministic_hits']:,}；已命名Hit {a['named_hits']:,} → {b['named_hits']:,}；未知Hit {a['unknown_hits']:,} → {b['unknown_hits']:,}另列。",
        '累计分母减少仅来自此前逐条证实的重复记录删除。本次补充归属不新增、不删除、不改变真实Hit资格。','',
        '|职业|旧基线|本次前|当前确认覆盖|确认/已命名Hit|距离95%还差Hit|',
        '|---|---:|---:|---:|---:|---:|']
    for row in comp['jobs']:
        x,y=row['before'],row['after']; n=y['deterministic_hits']+y['named_inferred_hits']
        lines.append(f"|{row['job']}|{x['named_coverage_percent']:.2f}%|{last[row['job']]['named_coverage_percent']:.2f}%|{y['named_coverage_percent']:.2f}%|{y['deterministic_hits']}/{n}|{max(0,math.ceil(n*.95)-y['deterministic_hits'])}|")
    lines+=['','## 本次补充归属','',
        '- 天昏地暗5717与怨恨之像5508的生成参数为10000ms，原始包第8～10秒仍有同主人、同完整对象ID的命中。只读查询原对象代次，再核对完整伤害记录的来源、目标、金额、暴击、结果组、对象ID及固定技能槽。',
        '- 保留原8秒主命中判定和父子关系生命周期，仅在主流程处理完成后，补充原先已命名且已有真实Hit证据的技能归属；未知、附加伤害、周期伤害和没有真实Hit标记的记录不升格。',
        f"- 36份完整回放净新增确认{proof['new_confirmed_hits']}次；金额、来源、目标、物理记录多重集、真实Hit标记多重集完全不变；未命名Hit数量也不变。",
        '- 曾试验直接延长主保留期，但它改变了部分真实Hit标记，已否决并还原。不能把那个未完成试验的数字当作成果；相关目录附REJECTED.md。',
        '- 此前重磅鸭符文攻击与噬魂者11076父对象链的闭合补充继续保留；11076三份增量完整回放新增16次确认。','',
        '## 守恒与测试','',
        '- 本次36份全量A/B：late_field_acceptance.json、late_field36_comparison.json；原始和汇总层分别比对每条物理记录与真实Hit资格的哈希，不只比较总伤害。',
        '- 已授权的重复修复另行逐条验收：原始层381条、汇总层372条，两层不可相加；最大单份汇总伤害下降约0.532%，无唯一伤害遗漏。见self_target_corpus_acceptance.json。',
        '- 相关回归3022项通过、7项已有预期失败、111项子测试通过；记录见late_field_tests.xml及late_field_extra_tests.xml。已补Boss目标显示ID携带模板后缀的正例，物理核对始终使用原包目标ID。',
        '- 本次冻结数据完全一致；累计旧基线期间有其他任务修改过治疗模型，累计变化不冒称解析器单变量实验。','',
        '## 尚未解决的主要缺口','',
        '- 岩浆墙EX32022：正常根动作已解出，但具体实体出生缺独立创建选择键，静态Clone分支尚未闭合，不能将整个实体模板一律升为确认。',
        '- 根据用户补充的“柱子生成后短暂延迟再爆炸”表现，另行核查两份原始包：同一柱子出生后0.0470/0.0771秒先命中，1.1868/1.1992秒再次命中，1.9967/1.9990秒出现1B02结束候选记录；四条结果对象字段均为FFFFFFFF，应查实体链而非伪造Projectile关联。结束字段不擅自视为已证明删除，创建选择缺口仍保留。见outputs/magma_burst_lifecycle_v34_20260908/REPORT.md；小窗口不计入覆盖增量。',
        '- 部分圣物及重磅鸭：自身技能槽为FFFFFFFF或缺少动作分支证据；同一个实体可以有多种攻击，不能直接用创建者技能替代。',
        '- 新增原帧反例：0821机械鸭138的对象101687，先后出现260408、260608及FFFFFFFF自身攻击槽；同对象不能继承前一个已知技能。定点动作/信号字段检查仍未补出后三条的精确连接。见outputs/duck_result_action_v34_20260908/REPORT.md。',
        '- 两份柱子窗口和重磅鸭/创建上下文字段的补充诊断共48项测试通过、16项子测试通过；这是只读证据核验，不冒充新的生产归因增量。',
        '- 重磅鸭261101已从它自己的ACT next_action补出Start到Loop的静态边，71项独立测试通过；固定语料含工程职业的6份抓包完整必要字节筛查未发现精确261101自身槽值，因此本轮不接入生产、不计提升。静态闭合不等于已经找到了这些实战Hit的来源。',
        '- 部分玩家直接伤害：未找到同主人、同代次的完整对象注册；圣骑士三个原帧锚点中同号09属于其他主人，已验证不能借用。仍需破解其它记录形式。',
        '未确认项保留，不据旧技能名称、时间邻近或伤害大小填成确认；没有修改评分算法、界面或发布安装包。','']
    (OUT/'RUNTIME_RESULT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items()if k not in('jobs','current_signature')},ensure_ascii=False))


if __name__=='__main__':main()
