"""Generate UI smoke fixtures from real calculation code, without capture/cloud."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.v410_native_core import NativeSimulatorCore
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.live_dps.team_score_sync import CURRENT_SCORE_SCHEMA_VERSION

def main():
    store=DataStore.load()
    core=object.__new__(NativeSimulatorCore)
    core.store=store
    core.calculator=BuildCalculator(store)
    core.legacy_timeline=LegacyTimelineService(store,core.calculator)
    seed=deepcopy(store.defaults['build1'])
    host=core.legacy_timeline.extreme_runtime(seed)
    build=host._extreme_team_score_build_for_job('狂战士')
    for item in build['equipment']:
        base=next(row for row in store.tables['base'] if row['职业']=='战神' and row['等级']=='70L' and row['部位']==item['slot'])
        item['name']=base['装备名称']
    rows,summary=core.calculator.calculate(build)
    details=core.build_details(build)
    skills=core.skill_rows(build,True)
    swing=next(row for row in skills['rows'] if row['skill']=='旋转冲击')
    build['skill_sim']=[{key:swing[key] for key in ('skill','level','multiplier')}]
    fixture=dict(build=build,catalog=core.build_catalog('战神','狂战士'),details=details,
                 skillRows=skills,skillSimRows=core.skill_sim_rows(build,build['skill_sim']),
                 scoreEquipment=dict(sourceId='barbarian',sourceHash='fixture',source='default',
                                     build=build,summary=summary,preview=core.score_build_preview(build)),
                 calculation=dict(rows=[asdict(row) for row in rows],summary=summary,elapsedMs=0),
                 bootstrap=dict(jobs=dict(first=store.first_jobs(),second={j:store.second_jobs(j) for j in store.first_jobs()})))
    out=ROOT/'work/update_20261017'
    out.mkdir(parents=True,exist_ok=True)
    (out/'ui-fixture.json').write_text(json.dumps(fixture,ensure_ascii=False),encoding='utf8')
    runtime=core.legacy_timeline._runtime(build,{'mode':'long'})
    effects=runtime._buff_attrs_for_team_effect('战吼',5,False,'队员1 毁灭者')
    assert effects['无属性%']==12, effects
    report=dict(score_schema=CURRENT_SCORE_SCHEMA_VERSION,neutral_percent=summary['无属性%'],
                resonance=details['resonance'],buffs=details['buffs'],battle_howl_lv5=effects,
                fashion={r['slot']:r['name'] for r in build['fashion']},
                engraving_extras=sorted({r['extra'] for r in build['engravings']}))
    (out/'runtime-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('resonance','buffs')},ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
