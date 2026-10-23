"""Reproducible local acceptance of phase-one experiments, not a release gate."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/verification/scoring_optimization_20261019'


def main():
    tests = [
        'test_score_window_power_index', 'test_score_optimization_validation',
        'test_native_speed_reference_experiment', 'test_score_comparability_experiments',
        'test_hit_fit_exact_cache', 'test_score_performance_revision',
        'test_output_yao_scoring', 'test_support_overflow_output_weight',
        'test_score_v2_theory_injection', 'test_score_hit_realization',
        'test_action_hit_timing_fallback',
    ]
    command=[sys.executable,'-X','utf8','-B','-m','pytest',*[f'tests/{x}.py' for x in tests],'-q']
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    accuracy=json.loads((OUT/'accuracy_reference/native_speed_experiment.json').read_text('utf-8'))
    perf=json.loads((OUT/'perf/performance_summary.json').read_text('utf-8'))
    report=OUT/'评分准确性可比性与速度优化_实验报告_20261019.html'
    broken=[]
    for value in re.findall(r'href="([^"]+)"',report.read_text('utf-8')):
        url=urlsplit(value)
        if not url.scheme and url.path and not (report.parent/unquote(url.path)).exists():
            broken.append(value)
    files=[
        'dragon_nest_builder/live_dps/score_burst.py',
        'dragon_nest_builder/live_dps/score_engine.py',
        'dragon_nest_builder/live_dps/score_experiments.py',
        'dragon_nest_builder/live_dps/score_v2.py',
        'dragon_nest_builder/live_dps/score_rotation.py',
        'dragon_nest_builder/mixins/skill_timeline_state.py',
        'third_party/skill_hit_tool/app/act_hit_timeline_comparator/core.py',
    ]
    validation=dict(
        phase='local experiments and exact kernel optimization only',
        tests_command=command,tests_returncode=result.returncode,
        tests_output=result.stdout+result.stderr,broken_links=broken,
        native_reference_checks=accuracy['reference_checks'],
        native_reference_matches=accuracy['reference_check_matches'],
        exact_fit_reports_all_equal=all(x['equal'] and len(set(x['report_hashes']))==1 for x in perf['experiments']),
        fit_cache_default_enabled=perf['default_enabled'],
        approximate_scoring_enabled=False,
        full_score_pairs_rerun_this_phase=0,
        three_point_gate_status='unit tested; no approximate end-to-end mode accepted',
        production_file_sha256={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},
        published=False,cloud_scores_modified=False,
    )
    validation['passed']=(result.returncode==0 and not broken and validation['exact_fit_reports_all_equal']
                          and accuracy['reference_checks']==accuracy['reference_check_matches'])
    (OUT/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(validation,ensure_ascii=False,indent=2))
    return 0 if validation['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
