"""Read effective static skill definitions from the actual release bytecode/data."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import compare_published_scores_20261019 as isolation

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--version',required=True);a=p.parse_args()
    from dragon_nest_builder.data_loader import DataStore
    store=DataStore.load()
    index=json.loads((isolation.ENGINE/'dragon_nest_builder/data/modular/tables/skills/skill_damage_tiny_jobs/index.json').read_text(encoding='utf-8'))
    report={'version':a.version,'engine':str(isolation.ENGINE),'jobs':{}}
    for row in index['jobs']:
        job=row['job_name'];skills={}
        for entry in store._skill_damage_entries(job):
            skill=entry['name']
            levels=store.skill_damage_levels(job,skill)
            skills[skill]={'entry':entry,'levels':levels,'multiplier':store.skill_damage_multiplier(job,skill),
                           'element':store.skill_damage_element(job,skill),
                           'adjustment':store.skill_damage_adjustment(job,skill),
                           'cast_time':store.skill_cast_time(skill,(job,)),
                           'level_data':{str(lv):store.skill_damage_level_data(job,skill,lv) for lv in levels}}
        report['jobs'][job]=skills
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(a.version,len(report['jobs']),sum(map(len,report['jobs'].values())))
if __name__=='__main__':main()
