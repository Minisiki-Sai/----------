from __future__ import annotations
from collections import defaultdict
import html
import json
from pathlib import Path
import statistics
from run_published_comparison_batch_20261019 import OUT, CASES

e=lambda x:html.escape(str(x))
def fmt(x, digits=1):
    return '—' if x is None else f'{x:,.{digits}f}'
def change(a,b):
    if a is None or b is None:return '—'
    return f'{b-a:+.1f}'
def pair(a,b,digits=1):return fmt(a,digits)+' → '+fmt(b,digits)
def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+e(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def avg(rows,key):
    values=[r[key] for r in rows if r.get(key) is not None]
    return statistics.mean(values) if values else None
def window_length(rows):
    intervals=sorted((float(a),float(b)) for row in rows for a,b in row.get('output_windows',[]) if b>a)
    merged=[]
    for a,b in intervals:
        if merged and a<=merged[-1][1]:merged[-1][1]=max(b,merged[-1][1])
        else:merged.append([a,b])
    return sum(b-a for a,b in merged)

def main():
    overview=[];sections=[];all_pairs=[];summary=[]
    for key,name in CASES:
        reports={}
        for version in ('4.78','4.85'):
            path=OUT/f'{key}_v{version}.json'
            if path.exists():reports[version]=json.loads(path.read_text(encoding='utf-8'))
        if len(reports)!=2 or any(r.get('status')!='ok' or r.get('score',{}).get('theoryPlanner')!='v401-full-team-score' or r.get('score',{}).get('theoryPlannerError') for r in reports.values()):
            overview.append([e(name),'尚未完成或计算失败','—','—','—','—'])
            continue
        a,b=reports['4.78'],reports['4.85']
        ar=a.get('scoreRowsDetailed',[]);br=b.get('scoreRowsDetailed',[])
        old={r['source_id']:r for r in ar};new={r['source_id']:r for r in br}
        ids=list(dict.fromkeys([*new,*old]))
        comparisons=[];skills=[]
        for sid in ids:
            x,y=old.get(sid),new.get(sid)
            r=y or x
            if not x or not y:
                comparisons.append([e(r['name']),e(r['job']),'仅一版识别到此角色']+['—']*8)
                continue
            item={'capture':name,'case':key,'source_id':sid,'name':r['name'],'job_old':x['job'],'job_new':y['job'],
                  'metrics':{k:{'old':x.get(k),'new':y.get(k),'delta':y[k]-x[k] if x.get(k) is not None and y.get(k) is not None else None}
                             for k in ('total_score','output_score','support_score','specialty_score','survival_penalty','actual_standard_power','theory_standard_power','theory_damage_completion')}}
            all_pairs.append(item)
            comparisons.append([e(r['name']),e(x['job'] if x['job']==y['job'] else x['job']+' → '+y['job']),
                e(x['grade'])+' → '+e(y['grade']),pair(x['total_score'],y['total_score']),change(x['total_score'],y['total_score']),
                pair(x['output_score'],y['output_score']),change(x['output_score'],y['output_score']),pair(x['support_score'],y['support_score']),
                pair(x.get('specialty_score'),y.get('specialty_score')),pair(x.get('survival_penalty'),y.get('survival_penalty')),
                pair(sum(bb-aa for aa,bb in x.get('output_windows',[])),sum(bb-aa for aa,bb in y.get('output_windows',[])))])
            osk={s['skill_name']:s for s in x.get('skill_details',[])};nsk={s['skill_name']:s for s in y.get('skill_details',[])}
            detail=[]
            for sn in dict.fromkeys([*nsk,*osk]):
                u=osk.get(sn,{});v=nsk.get(sn,{})
                detail.append([e(sn),pair(u.get('actual_count'),v.get('actual_count')),pair(u.get('theory_count'),v.get('theory_count')),
                  pair(u.get('completion'),v.get('completion')),pair(u.get('actual_power',0)/1e6,v.get('actual_power',0)/1e6),
                  pair(u.get('theory_power',0)/1e6,v.get('theory_power',0)/1e6)])
            support=[]
            ou={(z['skill_name'],z['effect_key']):z for z in x.get('support_details',[])};nu={(z['skill_name'],z['effect_key']):z for z in y.get('support_details',[])}
            for sn,ek in dict.fromkeys([*nu,*ou]):
                u=ou.get((sn,ek),{});v=nu.get((sn,ek),{})
                support.append([e(sn),e(ek),pair(u.get('actual_coverage'),v.get('actual_coverage'),3),pair(u.get('theory_coverage'),v.get('theory_coverage'),3),
                   pair(u.get('actual_contribution'),v.get('actual_contribution')),pair(u.get('theory_contribution'),v.get('theory_contribution'))])
            diagnostics=[[label,pair(x.get(field),y.get(field),3)] for label,field in [('输出权重','output_weight'),('辅助权重','support_weight'),('特色权重','specialty_weight'),('整场伤害完成率 %','theory_damage_completion'),('爆发窗口兑现率 %','burst_usage'),('治疗兑现率','healing_effective_realization'),('危险血量救急','healing_rescue_completion'),('挑衅覆盖率','tank_taunt_coverage'),('蛋壳覆盖率','tank_shell_coverage')]]
            ps=a.get('plans',{}).get(sid,{}).get('summary') or {}; qs=b.get('plans',{}).get(sid,{}).get('summary') or {}
            panels=[[label,pair(ps.get(field),qs.get(field),2)] for label,field in [('生命','HP'),('物攻平均','physical_average'),('魔攻平均','magic_average'),('最终 %','final_percent'),('致命 %','crit_percent'),('火攻 %','火攻%'),('水攻 %','水攻%'),('光攻 %','光攻%'),('暗攻 %','暗攻%'),('无属性 %','无属性%')]]
            skills.append('<details><summary>'+e(r['name']+' · '+r['job'])+'：技能与辅助分项</summary>'+table(['诊断指标（权重/覆盖率为原始比例）','旧版 → 新版'],diagnostics)+'<details><summary>理论轴基准面板摘要（不是逐时刻 Buff 面板）</summary>'+table(['属性','旧版 → 新版'],panels)+'</details><h4>输出分项（旧版 → 新版）</h4>'+table(['技能','实战次数','理论次数','伤害完成率 %','实战标准伤害 M','理论标准伤害 M'],detail)+
                '<h4>辅助分项（覆盖率为原始比例；贡献保留程序原单位）</h4>'+table(['技能','效果','实际覆盖','理论覆盖','实际贡献','理论贡献'],support)+'</details>')
        metadata=[]
        recognition=[]
        for version,r,rows in [('4.78',a,ar),('4.85',b,br)]:
            rp=r['replay'];score=r['score']
            metadata.append([version,str(r['schema']),'V2' if 'dragon_nest_builder.live_dps.score_v2' in r.get('moduleOrigins',{}) else '旧版 V1',
                e((rp.get('dungeon_map') or {}).get('nest_name','未知')),e((rp.get('instance_completion') or {}).get('label','未知')),
                str(len(rows)),fmt(rp.get('elapsed_seconds')),fmt(window_length(rows)),fmt(r.get('decodeSeconds')),fmt(r.get('scoreSeconds'))])
            recognition.append([version,fmt(rp.get('total_damage',0)/1e6,3),str(len(rp.get('incoming_damage_events',[]))),fmt(rp.get('total_healing',0)/1e6,3),'<br>'.join(e(z.get('name','?')) for z in rp.get('boss_encounters',[]))])
        overview.append(['<a href="#'+key+'">'+e(name)+'</a>',str(len(ar))+' / '+str(len(br)),pair(avg(ar,'total_score'),avg(br,'total_score')),
                         pair(avg(ar,'output_score'),avg(br,'output_score')),pair(avg(ar,'support_score'),avg(br,'support_score')),pair(a.get('scoreSeconds'),b.get('scoreSeconds'))])
        summary.append({'case':key,'capture':name,'players_old':len(ar),'players_new':len(br),'average_total_old':avg(ar,'total_score'),'average_total_new':avg(br,'total_score'),
                       'average_output_old':avg(ar,'output_score'),'average_output_new':avg(br,'output_score'),'average_support_old':avg(ar,'support_score'),'average_support_new':avg(br,'support_score'),
                       'score_seconds_old':a.get('scoreSeconds'),'score_seconds_new':b.get('scoreSeconds')})
        sections.append('<section id="'+key+'"><h2>'+e(name)+'</h2>'+table(['版本','算法日期','规则','识别巢穴','通关','人数','记录时长 s','个人输出区间并集 s','解析 s','评分 s'],metadata)+
            '<details><summary>解析与 Boss 识别对照</summary><p>治疗／回血总量是回放统计字段，不等于某位奶的个人治疗贡献；新版会额外恢复协议血量变化。</p>'+table(['版本','回放总伤害 M','受伤事件数','治疗／回血 M','Boss 列表'],recognition)+'</details>'+
            '<h3>逐角色评分</h3>'+table(['角色','职业','评级','总分','总分变化','输出分','输出变化','辅助分','特色分','承伤扣分','个人有效输出 s'],comparisons)+''.join(skills)+'</section>')
    jobs=defaultdict(list)
    for row in all_pairs:jobs[('黑龙硬核' if row['case'].startswith('black') else '梦魇海龙',row['job_new'])].append(row)
    job_rows=[]
    for (nest,job),rows in sorted(jobs.items()):
        cells=[e(nest),e(job),str(len(rows))]
        for metric in ('total_score','output_score','support_score','specialty_score'):
            valid=[r['metrics'][metric] for r in rows if r['metrics'][metric]['old'] is not None and r['metrics'][metric]['new'] is not None]
            cells.append(pair(statistics.mean(v['old'] for v in valid),statistics.mean(v['new'] for v in valid)) if valid else '—')
        job_rows.append(cells)
    result={'battles':summary,'players':all_pairs}
    (OUT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    content=('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>v4.78 与 v4.85 六场抓包评分对照</title><style>
body{font:15px/1.6 system-ui,"Microsoft YaHei";color:#18353b;background:#eef5f5;margin:0;padding:28px}main{max-width:1700px;margin:auto}h1{font-size:27px}h2{font-size:21px}section,.card{background:white;border:1px solid #cbdcde;border-radius:12px;padding:22px;margin:22px 0}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:9px 11px;text-align:left;border-bottom:1px solid #dce6e7;white-space:nowrap}th{background:#e8f2f3;color:#245e66;position:sticky;top:0}tr:nth-child(even){background:#f6f9fa}.scroll{overflow:auto}details{border:1px solid #cfdddf;border-radius:7px;margin:12px 0;padding:12px}summary{cursor:pointer;color:#006f78;font-weight:600}a{color:#00747d}.muted{color:#526a70}.warning{border-left:5px solid #b47d22;padding:12px;background:#fff7e7}h4{margin:14px 0 5px}</style><main>
<h1>v4.78 与 v4.85：3 场黑龙硬核 + 3 场梦魇海龙</h1>
<p>所有箭头均为 <b>v4.78 → v4.85</b>。总分含承伤扣分；变化为分值差，不是相对百分比。平均分仅用于描述本场队员变化，不是新的队伍评分。</p>
<div class="card"><h2>对照方法与边界</h2><p>载入实际发布版本的原始 Python 字节码和随包数据，使用 Python 3.12 调用同一回放、全队评分入口。不是改当前代码的版本号，也不是读取旧缓存。两版分别隔离用户配置与缓存，并阻断网络、禁用上传。发布清单已核对 v4.78 的 1013 个、v4.85 的 1043 个代码和数据文件。</p>
<p>各版保留原默认装备、技能数据、解析规则、理论排轴和评分规则。v4.78 为 20261013 的旧评分；v4.85 为 20261017 的 V2 评分。后者新增逐次 Hit/倍率完成度，并包含后续职业、装备、无属性攻击、召唤物和识别更新，因此变化不能全部归因于 Hit 修正。</p>
<p>标准伤害采用各版自己的评分面板，不是原始抓包伤害；跨版本的 M 数值变化不能直接当作游戏内技能加强比例。应结合实战与理论两边的变化、次数、完成率和权重一起阅读。本对照记录程序真实结果，但不能据此宣布新版每条规则均正确。</p>
<p class="warning">本报告是旧版与新版整体行为对照，不是“同装备、同理论轴、仅切换 Hit 系数”的单变量实验。旧版先于梦魇海龙发布，对新巢穴的名称、Boss、机制识别可能不完整；这也是版本差异的一部分。最多两场同时计算，每场最多 4 个排轴工作进程；最后两场新版利用旧版结束后的空闲资源并行。耗时受并行负载影响，仅为本次记录，不是独占机器性能测试。缺失分项显示 —。</p></div>
<div class="card"><h2>六场总览</h2>'''+table(['抓包','评分人数 旧/新','平均总分','平均输出分','平均辅助分','评分耗时 s'],overview)+'</div><div class="card"><h2>按巢穴、职业汇总本次样本</h2><p>同一角色不同场次分别计入；样本有限，不用于推断全服职业强弱。</p>'+table(['巢穴','职业','人次','平均总分','平均输出分','平均辅助分','平均特色分'],job_rows)+'</div>'+''.join(sections)+
    '<p class="muted">原始结果：同目录各 *_v4.78.json / *_v4.85.json；发布来源核验：provenance.json；结构化对比：comparison.json。未改动安装包、生产代码或云端记录。</p></main></html>')
    path=OUT/'v4.78与v4.85六场评分对照_20261019.html'
    path.write_text(content,encoding='utf-8')
    print(json.dumps(result['battles'],ensure_ascii=False,indent=2))
    print(path)

if __name__=='__main__':main()
