"""Exercise ACT's parallel consumer and the standalone isolated runtime."""
from pathlib import Path
import sys, os, json, pickle, hashlib, time

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'third_party/skill_hit_tool/app'
OUT=ROOT/'outputs/verification/hit_matching_fix_20261020'

def main():
    standalone='--standalone' in sys.argv
    if standalone:
        sys.path.insert(0,str(APP))
        os.environ['DN_ACT_SOURCE_ROOT']=str(APP)
    else:
        sys.path.insert(0,str(ROOT))
        from dragon_nest_builder.live_dps.score_hit_evidence import addon_root
        addon_root()
        os.environ['DN_HIT_WORKERS']='2'
    from act_hit_timeline_comparator.core import TimelineRepository
    from skill_timeline_viewer.resource_paths import default_timeline_path
    from skill_hit_tool.confirmed_components import annotate_components
    from skill_hit_tool.completion_report import compare_all_skills
    import act_hit_timeline_comparator.stage_estimator as stage
    import dragon_nest_builder.hit_analyzer.analyzer as analyzer
    source=ROOT/'outputs/verification/hit_matching_20261020'
    obs,_,_,speeds=pickle.loads((source/'observations.pkl').read_bytes())
    decoded=pickle.loads((source/'decoded.pkl').read_bytes())
    obs=annotate_components(obs,decoded)
    repo=TimelineRepository(default_timeline_path())
    if '--gui' in sys.argv:
        import tkinter as tk
        from skill_hit_tool.app import UnifiedSkillHitApp
        from skill_hit_tool.transformed_forms import variants_for_observation
        from skill_hit_tool.document_routes import select_cast_routes
        from act_hit_timeline_comparator.core import best_comparison,calculate_hit_realization
        window=tk.Tk();window.withdraw()
        rendered=[]
        try:
            app=UnifiedSkillHitApp(window);ui=app.comparator
            for sid,ms in [('2215',111233),('2215',255941),('5216',26753),('5216',82106)]:
                o=next(o for o in obs if str(o.skill_id)==sid and o.cast_timestamp_ms==ms)
                speed=speeds.get((o.source_id,ms),0.)
                vv=variants_for_observation(repo,o,speed,condition_choice='自动/无条件选择',include_optional=False)
                vv=select_cast_routes(vv,o,obs,speeds)
                best,_=best_comparison(vv,o.hit_times,o.hit_amounts,o.critical_flags,
                                       external_speed_percent=speed,observation=o)
                ui.current_observation=o
                ui._show_comparison(best)
                deadline=time.monotonic()+5
                while ui._filling_trees and time.monotonic()<deadline:
                    window.update();time.sleep(.002)
                assert not ui._filling_trees
                assert len(ui.result_tree.get_children())==len(best.rows)
                rate=calculate_hit_realization(best,o).multiplier_rate_percent
                assert f'{rate:.1f}%' in ui.summary_vars['multiplier_rate'].get()
                rendered.append(dict(skill=sid,time=ms,rows=len(best.rows),
                    hit_label=ui.summary_vars['hit_rate'].get(),
                    multiplier_label=ui.summary_vars['multiplier_rate'].get()))
            result=dict(ok=True,rendered=rendered,isolated=bool(sys.flags.isolated))
            (OUT/'targeted_gui.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
            print(json.dumps(result,ensure_ascii=False))
        finally:
            for callback in window.tk.call('after','info'):window.tk.call('after','cancel',callback)
            window.destroy()
        return
    start=time.perf_counter()
    if standalone:
        report=compare_all_skills(obs,repo,speeds)
    else:
        from dragon_nest_builder.live_dps.hit_parallel import compare_parallel,shutdown
        try:report=compare_parallel(obs,repo,speeds)
        finally:shutdown()
    rows=report['cast_rows']
    gates={r['cast_timestamp_ms']:r for r in rows if str(r['skill_id'])=='5216'}
    for t,w in ((26753,12.4),(82106,33.6),(306976,21.2)):
        assert gates[t]['expected_hits']==24
        assert abs(gates[t]['expected_weight']-33.6)<1e-8
        assert abs(gates[t]['realized_weight']-w)<1e-8
    fires=[r for r in rows if str(r['skill_id'])=='2215']
    assert len(fires)==4
    assert abs(fires[0]['realized_weight']-1.48)<1e-8
    mode='standalone'if standalone else'act'
    result=dict(mode=mode,isolated=bool(sys.flags.isolated),python=sys.executable,
        stage_module=stage.__file__,decoder_module=analyzer.__file__,
        seconds=time.perf_counter()-start,casts=len(rows),
        sha256=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        report=report)
    (OUT/f'entrypoint_{mode}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({k:v for k,v in result.items()if k!='report'},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
