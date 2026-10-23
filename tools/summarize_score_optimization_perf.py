"""Aggregate measured local Hit-fit experiments, without extrapolating score time."""
import hashlib,json,re,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/verification/scoring_optimization_20261019/perf'

def main():
    experiments=[]
    for filename,label in (
        ('actor_cache_ab.json','单角色595条：仅复用同次时间偏移的伤害倍率拟合'),
        ('eight_jobs_stratified_ab.json','全队7职业分层341条：仅复用拟合'),
        ('actor_combined_ab.json','单角色595条：精确复用＋相邻权重搜索'),
        ('seven_jobs_combined_ab.json','全队7职业分层341条：精确复用＋相邻权重搜索'),
    ):
        path=OUT/filename
        if not path.exists():continue
        data=json.loads(path.read_text('utf8'))
        if data.get('cast_rows') is None and filename=='actor_cache_ab.json':
            reference=json.loads((OUT/'actor_combined_ab.json').read_text('utf8'))
            if {r['sha256']for r in data['runs']} == {r['sha256']for r in reference['runs']}:
                for field in ('cast_rows','skill_rows','jobs','runtime_modules'):
                    data[field]=reference[field]
        before=[r['seconds']for r in data['runs']if not r['enabled']]
        after=[r['seconds']for r in data['runs']if r['enabled']]
        b=sum(before)/len(before);a=sum(after)/len(after)
        experiments.append(dict(label=label,input_rows=data['rows'],output_cast_rows=data.get('cast_rows'),
            output_skill_rows=data.get('skill_rows'),before_seconds=b,after_seconds=a,reduction_percent=(1-a/b)*100,
            equal=data['all_fields_identical'],report_hashes=[r['sha256']for r in data['runs']],
            before_runs=before,after_runs=after,source=str(path),scope=data['scope'],jobs=data.get('jobs'),
            runtime_modules=data.get('runtime_modules',{}),timeline_sha256=data['timeline_sha256']))
    baseline=ROOT/'outputs/verification/scoring_optimization_20261019/baseline/act_hit_timeline_comparator/core.py'
    published=ROOT/'outputs/build_v485_20261019_004033/engine_dist/DragonNestEngine/_internal/third_party/skill_hit_tool/app/act_hit_timeline_comparator/core.py'
    test_args=[sys.executable,'-X','utf8','-B','-m','pytest','tests/test_hit_fit_exact_cache.py',
               'tests/test_score_hit_realization.py','tests/test_action_hit_timing_fallback.py','-q']
    test_run=subprocess.run(test_args,cwd=ROOT,capture_output=True,text=True,encoding='utf8')
    if test_run.returncode:raise RuntimeError(test_run.stdout+test_run.stderr)
    passed=int(re.search(r'(\d+) passed',test_run.stdout).group(1))
    current=ROOT/'third_party/skill_hit_tool/app/act_hit_timeline_comparator/core.py'
    payload=dict(schema='exact_hit_fitting_optimization_v1',experiments=experiments,
        tests=dict(command='py -3.12 -X utf8 -B -m pytest tests/test_hit_fit_exact_cache.py tests/test_score_hit_realization.py tests/test_action_hit_timing_fallback.py -q',
                   passed=passed,stdout=test_run.stdout,random_cases=10000,random_float_comparison='exact equality, not tolerance'),
        default_enabled="get('DN_HIT_EXACT_FIT_CACHE', '1')" in current.read_text('utf8'),
        neighbor_experiment_default_enabled=False,
        rollback_environment='DN_HIT_EXACT_FIT_CACHE=0',
        baseline_matches_published_source=baseline.read_bytes()==published.read_bytes(),
        baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
        changed_files=[dict(path=str(ROOT/relative),sha256=hashlib.sha256((ROOT/relative).read_bytes()).hexdigest())
            for relative in ('third_party/skill_hit_tool/app/act_hit_timeline_comparator/core.py',
                             'tests/test_hit_fit_exact_cache.py','tools/benchmark_score_optimization_perf.py',
                             'tools/summarize_score_optimization_perf.py')],
        limitations=[
            '只测Hit匹配步骤，不含解包、理论排轴、辅助评分；不能直接外推整场提速或4分钟目标。',
            '样本为0808多轮长抓包，8名角色但只有7种职业；不是全职业全抓包覆盖。',
            '全部JSON字段一致证明本步骤输出零差异；不是完整评分3分阈值实测。',
            '墙钟耗时受本机其他负载影响；列出全部重复测量，ABBA取两轮均值，不挑最好一轮。',
            '没有改变Boss范围、输入路线、倍率候选、匈牙利分配或舍弃Hit；没有启用近似评分。',
            '相邻权重搜索虽然精确，但跨职业实测提速不稳定，因此独立实验开关DN_HIT_EXACT_WEIGHT_NEIGHBORS默认关闭，不随精确缓存启用。',
            '进度日志停顿不能定位某一技能耗时。扇之刃69次原始观察复测0.36秒（含profiler），此前600秒归因未证实。',
            '评分进程退出清理挂起根因未复现；未改变池shutdown或强杀未完成任务。'])
    destination=OUT/'performance_summary.json'
    destination.write_text(json.dumps(payload,ensure_ascii=False,indent=2),'utf8')
    print(destination)

if __name__=='__main__':main()
