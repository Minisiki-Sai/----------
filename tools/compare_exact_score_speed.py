"""Compare full score rows/axes, not rounded displayed totals."""
import argparse
import hashlib
import json
import html
import pickle
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def differences(a, b, path=''):
    if type(a) is not type(b):
        yield dict(path=path, before=a, after=b)
    elif isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                yield dict(path=path+'/'+str(key), before=a.get(key), after=b.get(key))
            else:
                yield from differences(a[key], b[key], path+'/'+str(key))
    elif isinstance(a, list):
        if len(a) != len(b):
            yield dict(path=path+'/length', before=len(a), after=len(b))
        for index, (left, right) in enumerate(zip(a, b)):
            yield from differences(left, right, path+'/'+str(index))
    elif a != b:
        yield dict(path=path, before=a, after=b)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('before',type=Path);p.add_argument('after',type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--json-only',action='store_true')
    a=p.parse_args()
    before=json.loads(a.before.read_text('utf8'));after=json.loads(a.after.read_text('utf8'))
    assert before['status']==after['status']=='ok'
    assert Path(before['capture']).resolve()==Path(after['capture']).resolve() and before['workers']==after['workers']
    diff=list(differences({k:before[k] for k in ('rows','plans')},
                          {k:after[k] for k in ('rows','plans')}))
    result={'capture':before['capture'],'workers':before['workers'],
            'all_score_fields_and_plans_identical':not diff,'difference_count':len(diff),
            'differences':diff[:100], 'timings':{}, 'players':[]}
    score_fields = ('total_score','output_score','support_score','specialty_score','survival_penalty','grade')
    result['score_components_identical'] = all(all(old.get(k) == new.get(k) for k in score_fields)
                                               for old,new in zip(before['rows'],after['rows']))
    result['plans_identical'] = before['plans'] == after['plans']
    for key in ('decode_seconds','score_seconds','elapsed_seconds'):
        old,new=before[key],after[key]
        result['timings'][key]={'before':old,'after':new,'seconds_saved':old-new,
                               'time_reduction_percent':100*(1-new/old),
                               'speed_factor':old/new}
    for old,new in zip(before['rows'],after['rows']):
        result['players'].append({k:old[k] for k in ('source_id','name','job','total_score','output_score','support_score','specialty_score')}
                                 | {'identical':old==new,'skills_checked':len(old['skill_details'])})
    for key in ('before','after'):
        report=getattr(a,key)
        result[key+'_file_sha256']=hashlib.sha256(report.read_bytes()).hexdigest()
    raw_hashes=[]; evidence_hashes=[]; prepared_hashes=[]
    for report in (a.before,a.after):
        directory=report.parent/(report.stem+'_evidence')
        raw_hashes.append(sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in (directory/'decoded').glob('*.bin')))
        prepared_hashes.append([hashlib.sha256(p.read_bytes()).hexdigest() for p in (directory/'prepared').glob('*.bin')])
        evidence_hashes.append(sorted(hashlib.sha256(json.dumps(json.loads(p.read_text('utf8')),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                                      for p in (directory/'evidence').glob('*.json')))
    result['raw_cache_byte_identical']=bool(raw_hashes[0]) and raw_hashes[0]==raw_hashes[1]
    result['hit_evidence_reports_identical']=bool(evidence_hashes[0]) and evidence_hashes[0]==evidence_hashes[1]
    result['hit_evidence_report_count']=len(evidence_hashes[1])
    result['prepared_artifact_counts'] = [len(h) for h in prepared_hashes]
    result['prepared_unique_content_identical'] = bool(prepared_hashes[0]) and set(prepared_hashes[0]) == set(prepared_hashes[1])
    from dragon_nest_builder.live_dps.score_hit_evidence import addon_root
    addon_root()
    prepared_values = []
    for report in (a.before, a.after):
        directory = report.parent / (report.stem + '_evidence') / 'prepared'
        prepared_values.append([pickle.loads(zlib.decompress(p.read_bytes())) for p in directory.glob('*.bin')])
    result['prepared_decoded_fields_identical'] = bool(prepared_values[0] and prepared_values[1]) and all(
        value == prepared_values[0][0] for rows in prepared_values for value in rows)
    result['theory_segments_checked']=sum(len(v.get('segments')or[])for v in after['plans'].values())
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    if a.json_only:
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return int(bool(diff))
    timings=''.join(f'<tr><td>{label}</td><td>{result["timings"][key]["before"]:.2f} 秒</td><td>{result["timings"][key]["after"]:.2f} 秒</td><td>{result["timings"][key]["time_reduction_percent"]:.2f}%</td></tr>'
                    for key,label in [('decode_seconds','加载与统计投影'),('score_seconds','完整评分'),('elapsed_seconds','启动＋加载＋评分合计')])
    players=''.join(f'<tr><td>{html.escape(p["name"])}</td><td>{p["job"]}</td><td>{p["total_score"]:.6f}</td><td>{p["output_score"]:.6f}</td><td>{p["support_score"]:.6f}</td><td>{p["skills_checked"]}</td><td>{"全部一致" if p["identical"] else "存在差异"}</td></tr>' for p in result['players'])
    document=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>评分无损提速实测 · 20261019</title>
<style>body{{font:16px/1.7 system-ui,"Microsoft YaHei";color:#183a40;background:#f3f7f8;max-width:1150px;margin:35px auto;padding:24px}}section{{background:white;padding:22px;margin:20px 0;border:1px solid #d5e2e4;border-radius:12px}}h1,h2{{line-height:1.4}}table{{border-collapse:collapse;width:100%}}th,td{{padding:11px;text-align:left;border-bottom:1px solid #dce5e6}}th{{background:#edf5f5}}.note{{color:#48646b}}strong{{color:#007d83}}code{{word-break:break-all}}</style>
<h1>评分不变前提下的提速实测</h1><p>2026-10-19 · 本地源码实验，未打包、未发布、未上传数据库</p>
<section><h2>结论：评分耗时减少约 14.9%，尚未达到 50%</h2><p>1011 硬核 BlueMoon 十字军：完整评分由 <strong>9 分 17 秒 → 7 分 54 秒</strong>，省约 83 秒。包含加载的总等待由 11 分 25 秒降至 9 分 50 秒。</p>
<table><tr><th>阶段</th><th>优化前</th><th>优化后</th><th>耗时减少</th></tr>{timings}</table>
<p class="note">同机、同抓包、各自新进程、独立空证据缓存、不复用完整评分/回放缓存。前后固定 4 个评分工作进程、4 个 Hit 工作进程；禁止联网和上传。不是拿旧版安装包与新版比较。仅完成一组整场 A/B，文件系统热缓存、系统负载会带来波动，不能承诺所有抓包都有同样比例。实际应用会根据内存和是否正在战斗选择进程数，本表不是所有机器的默认耗时。</p></section>
<section><h2>评分不变：不是只比较显示出来的小数</h2><p>比较了 8 人完整评分对象的所有字段、101 条逐技能明细，以及全部实际/理论时间轴和 {result['theory_segments_checked']} 条理论片段。<strong>差异数：{len(diff)}</strong>，使用原始数值比较，没有设置“允许相差 3 分”或四舍五入容差。</p>
<p>原始证据缓存逐字节一致：{result['raw_cache_byte_identical']}；{result['hit_evidence_report_count']} 份 Hit 证据报告全部一致：{result['hit_evidence_reports_identical']}。</p>
<table><tr><th>角色</th><th>职业</th><th>两版总分</th><th>两版输出分</th><th>两版辅助分</th><th>技能数</th><th>全部字段</th></tr>{players}</table></section>
<section><h2>实施的优化</h2><ol><li>离线加载时同步记录原始 Hit 证据，省去评分时的第二次完整解码。若实际使用过本地角色提示、数据目录不一致、读取失败，则回退原有独立解析。不接入实时抓包链路。</li>
<li>不可变效果行复用解析标识；命名空间、对象身份和强引用共同隔离缓存。逐次审判力量等 Buff 快照仍独立计算。</li>
<li>纯时间轴读取常驻 Buff 时，不再复制包含完整战斗数据的整份配置；只读现有私有配置。</li>
<li>相同输入的辅助候选允许在同一次评分中复用，键包含准确输入与目标伤害流，不四舍五入时刻或数值；返回独立副本。本抓包该项没有显著整段收益。</li>
<li>Hit 拟合保留相同数学运算与首次最优规则，去除完全相同的拟合尺度；大矩阵可使用可选 NumPy 路径，小矩阵及无 NumPy 环境保留原算法。</li></ol>
<p>不减少技能/Hit、不删理论候选、不缩短搜索预算、不改变评分公式，也没有提高并行进程数。</p></section>
<section><h2>为什么还没有减半</h2><p>主开销仍是理论轴与辅助对照轴：本次相关阶段计时由 329.57 秒到 337.05 秒，并没有实测变快。消除重复解析能省一段时间，不能使剩余计算自然减少一半。不能把各个局部优化的百分比直接相加。</p>
<p>下一步应聚焦候选重放和逐时刻状态求值的重复工作，并在多个职业/抓包上保持完整结果一致；不能用漏算候选、合并不同 Buff 状态或放宽误差来达到时间目标。本轮不宣称已完成 50% 提速。</p></section>
<section><h2>测试及边界</h2><p>本次相关测试 90 项通过；扩展运行合计 102 项通过、5 项失败。5 项在关闭全部新优化后仍复现：减抗边界测试 3 项、旧工作进程预算预期 1 项、辅助测试替身缺字段 1 项。未修改这些既有规则来使测试通过。</p><p>全场 A/B 两次均正常完成。可选单轴探针只用于性能定位，不作为整场验收结果。未验证所有职业和全部抓包。</p>
<p><a href="{html.escape(a.output.name)}">完整机器可读对比</a> · <a href="{html.escape(a.before.name)}">优化前原始评分</a> · <a href="{html.escape(a.after.name)}">优化后原始评分</a></p></section></html>'''
    a.output.with_name('评分无损提速实测_20261019.html').write_text(document,'utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(bool(diff))


if __name__=='__main__':raise SystemExit(main())
