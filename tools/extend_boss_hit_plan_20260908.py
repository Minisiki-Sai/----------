from pathlib import Path
import sys,json
from concurrent.futures import ProcessPoolExecutor,as_completed
from analyze_boss_hit_distribution_20260908 import analyze_one,code_signature,dump,summarize,SkillHitTemplateLibrary

def main():
    out=Path(sys.argv[1]); plan=json.loads((out/'plan.json').read_text(encoding='utf8'))
    if code_signature()!=plan['signature']: raise RuntimeError('Code changed')
    contents={r['content'] for r in plan['captures']}; pending=[]
    for text in sys.argv[2:]:
        p=Path(text);content,evidence=SkillHitTemplateLibrary.file_fingerprints(p)
        if content in contents:continue
        item={'path':str(p),'content':content,'evidence':evidence,'black_dragon_name_selection':'黑经' in p.name}
        plan['captures'].append(item);plan['supplementary_captures'].append(p.name);contents.add(content)
        pending.append(item)
    dump(out/'plan.json',plan)
    with ProcessPoolExecutor(max_workers=3,max_tasks_per_child=1) as pool:
        tasks={pool.submit(analyze_one,r['path'],str(out/'shards'/f"{r['content'][:24]}.json.gz"),plan['signature']):r for r in pending}
        for future in as_completed(tasks):
            r=future.result();print(json.dumps({'done':Path(r['capture']).name,'observations':r['observations']},ensure_ascii=False),flush=True)
    summarize(out,plan)

if __name__=='__main__':main()
