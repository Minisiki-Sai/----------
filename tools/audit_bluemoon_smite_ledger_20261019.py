"""Per-cast/per-hit evidence ledger, preserving raw damage and modeled values separately."""
import dataclasses as dc
import html
import json
import pickle
from bisect import bisect_right
from collections import Counter
from pathlib import Path

ROOT=Path('outputs/verification/bluemoon_smite_deep_20261019')
OLD=Path('outputs/verification/crusader_completion_20261019')
ACTOR='0x4D78152C'
BUFF_NAMES={'审判力量','神圣化身','判定光环','无情打击','光明之呼吸','战吼',
    '精神爆发','鸡尾酒','鹰怒','痊愈之手','能量之轮','查克拉圆环','阿兹那之息','神秘冥想','突袭'}


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    p=pickle.loads((OLD/'1011硬核bluemoon十字.pkl').read_bytes())
    s=p['snapshot']; plan=p['plans'][ACTOR]; r=next(r for r in p['rows'] if r.source_id==ACTOR)
    offset=plan['_score_time_offset']
    casts=[e for e in s.skill_cast_events if e.source_id==ACTOR and e.skill_id in ('3203','3356')]
    casts.sort(key=lambda x:x.timestamp_ms)
    times=[e.timestamp_ms/1000 for e in casts]
    rawhits=[e for e in s.combat_events if e.source_id==ACTOR and e.skill_id=='3203' and not e.healing
             and e.attribution_method=='packet_skill_id']
    extras=[e for e in s.combat_events if e.source_id==ACTOR and e.skill_id=='3203' and not e.healing
            and e.attribution_method!='packet_skill_id']
    buffrows=[b for b in s.player_buff_series if b.target_id==ACTOR and b.skill_name in BUFF_NAMES]
    def buffs(t):
        return [dict(skill=b.skill_name,key=b.key,value=b.value,source=b.source_id,
                     interval=[a,z],state_source=b.state_source)
                for b in buffrows for a,z in b.intervals if a<=t<z]
    def debuffs(t,target):
        return [dict(skill=b.skill_name,key=b.key,value=b.value,source=b.source_id,
            interval=[a,z],effective=any(x<=t<y for x,y in b.effective_intervals))
            for b in s.boss_effects if b.target_id==target for a,z in b.intervals if a<=t<z]
    pre={round((x['start']+offset)*1000):x['_score_actual_damage']
         for x in plan['_score_actual_cast_segments'][ACTOR] if x['skill']=='重击'}
    scored={round(x.seconds*1000):dc.asdict(x) for x in r.actual_timeline if x.skill_name=='重击'}
    v2=plan['_score_v2']['profiles']['重击']['casts']
    rows=[]
    for i,e in enumerate(casts):
        t=e.timestamp_ms/1000
        b=buffs(t)
        rows.append(dict(index=i+1,t=t,cast=dc.asdict(e),
            recognized_judgement=any(x['skill']=='审判力量' for x in b),
            recognized_avatar=any(x['skill']=='神圣化身' for x in b),
            buffs_at_cast=b,raw_hits=[],attributed_extra_hits=[],
            scoring_row=scored.get(e.timestamp_ms),pre_v2=pre.get(e.timestamp_ms),
            v2=v2.get(str(e.timestamp_ms))))
    unmatched=[]
    for e in rawhits+extras:
        t=e.timestamp_ms/1000
        idx=bisect_right(times,t)-1
        if idx<0 or t-times[idx]>5:
            unmatched.append(dc.asdict(e));continue
        b=buffs(t);d=debuffs(t,e.target_id)
        hit=dict(**dc.asdict(e),seconds=t,delay=t-times[idx],buffs=b,debuffs=d,
                 recognized_judgement=any(x['skill']=='审判力量' for x in b),
                 recognized_avatar=any(x['skill']=='神圣化身' for x in b))
        rows[idx]['raw_hits' if e.attribution_method=='packet_skill_id' else 'attributed_extra_hits'].append(hit)
    for row in rows:
        row['raw_hits'].sort(key=lambda e:e['timestamp_ms'])
        row['raw_sum']=sum(e['amount'] for e in row['raw_hits'])
        row['extra_sum']=sum(e['amount'] for e in row['attributed_extra_hits'])
        row['targets']=list(dict.fromkeys(e['target_name'] for e in row['raw_hits']))
        for i,e in enumerate(row['raw_hits']):e['ordinal']=i+1
    theory=[]
    for i,x in enumerate(x for x in r.theory_timeline if x.skill_name=='重击'):
        t=x.seconds
        effects=[w for w in plan['effect_windows'] if w.get('start',0)<=t-offset<w.get('end',0)]
        theory.append(dict(index=i+1,**dc.asdict(x),effects=effects))
    result=dict(capture=p['source'],actor=ACTOR,rows=rows,theory=theory,unmatched=unmatched,
        raw_hit_count=len(rawhits),raw_total=sum(x.amount for x in rawhits),
        scored_skill=dc.asdict(next(x for x in r.skill_details if x.skill_name=='重击')),
        actor_buff_events=[dc.asdict(x) for x in s.player_buff_events if x.target_id==ACTOR and x.skill_id=='3351'],
        judgement_parameter_commands=[dc.asdict(x) for x in s.skill_parameter_events if x.source_id==ACTOR and '3351,' in x.parameters],
        limitations=['按相邻重击释放和5秒窗口绑定，保留未绑定；不把附加伤害当原生Hit。',
          'Buff缺席仅指当前解析未显示，不能直接认定实际没有。',
          'effective_intervals沿用现有状态分组规则，亦是待验证项。',
          '评分重放目前按释放时刻取普通技能状态，原始Hit按各自发生时刻列状态。'])
    (ROOT/'smite_ledger.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    for row in rows:
        hittext=' '.join(str(round(h['amount']/10000,1))+('暴' if h['critical'] else '普') for h in row['raw_hits'])
        deb=Counter((x['key'],x['value']) for h in row['raw_hits'][:1] for x in h['debuffs'] if x['effective'])
        print(row['index'],row['t'],'审',int(row['recognized_judgement']),'化',int(row['recognized_avatar']),
            'raw',round(row['raw_sum']/1e6,2),'pre',round((row['pre_v2']or 0)/1e6,2),
            'score',round((row['scoring_row']or {}).get('power',0)/1e6,2),','.join(row['targets']),hittext,'DB',list(deb))
    print('COUNTS',len(casts),len(rawhits),'unmatched',len(unmatched),'commands',len(result['judgement_parameter_commands']))


if __name__=='__main__':main()
