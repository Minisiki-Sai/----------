"""Re-fit the saved, decoded original observations; never edits source capture."""
from audit_hit_matching_20261020 import *
from skill_hit_tool.completion_report import compare_all_skills

def run():
    obs,_,_,speeds=pickle.loads((OUT/'observations.pkl').read_bytes())
    focus=[o for o in obs if str(o.skill_id)in {'5214','5216'} and o.job=='噬魂者'
           and record_kind(o)=='施放' and o.hit_times]
    repo=repository()
    report=compare_all_skills(focus,repo,speeds,context_observations=obs)
    rows=report['cast_rows']
    assert rows
    for row in rows:
        hits,weight=(24,33.6)if str(row['skill_id'])=='5216'else(16,8.)
        assert row['expected_hits']==hits, row
        assert abs(row['expected_weight']-weight)<1e-8,row
    gates={r['cast_timestamp_ms']:r for r in rows if str(r['skill_id'])=='5216'}
    assert abs(gates[26753]['realized_weight']-12.4)<1e-8
    assert abs(gates[82106]['realized_weight']-33.6)<1e-8
    assert abs(gates[306976]['realized_weight']-21.2)<1e-8
    dest=OUT/'required_brand_policy_validation.json'
    dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('validated casts',len(rows))
    for t in (26753,82106,306976):
        r=gates[t]
        print(t,r['actual_hits'],r['realized_weight'],r['expected_weight'],r['multiplier_rate_percent'])

if __name__=='__main__':run()
