"""Local, network-free Hit-fit benchmark using already decoded evidence."""
from pathlib import Path
import argparse, cProfile, hashlib, json, os, pickle, pstats, sys, time, zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.score_hit_evidence import addon_root, repository
addon_root()

def main():
    def offline(event, args):
        if event == 'socket.connect':
            raise PermissionError('Offline Hit-fit benchmark: network disabled')
    sys.addaudithook(offline)
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', required=True)
    parser.add_argument('--actor', default='0x4D188526')
    parser.add_argument('--skill', default='6004')
    parser.add_argument('--limit', type=int, default=1)
    parser.add_argument('--output', required=True)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--reconcile', action='store_true')
    parser.add_argument('--compare', action='store_true')
    parser.add_argument('--stratified', type=int, default=0)
    parser.add_argument('--rounds', type=int, choices=(2,4), default=4)
    parser.add_argument('--save-input')
    parser.add_argument('--neighbors', action='store_true')
    args = parser.parse_args()
    data = pickle.loads(zlib.decompress(Path(args.prepared).read_bytes()))
    repo = repository()
    if args.reconcile and not data.get('benchmark_reconciled'):
        from dragon_nest_builder.data_loader import DataStore
        from dragon_nest_builder.hit_analyzer.analyzer import SkillHitAnalyzer
        from skill_hit_tool.hit_ledger import reconcile
        from skill_hit_tool.projectile_completion import annotate_observations
        from skill_hit_tool.lifecycle_completion import annotate_lifecycles
        obs = reconcile(data['obs'], data['events'], SkillHitAnalyzer(DataStore.load()), repo, data['end'],
                        target_filter=lambda e: True, entity_births=data['births'], object_proofs=data['proofs'], hammer_lifecycle=data['life'])
        obs = annotate_observations(obs,data['proofs'],data['events'],data['decoded'],data['registrations'])
        data['obs'] = annotate_lifecycles(obs,data['decoded'],data['casts'],data['end'])
    if args.save_input:
        saved=Path(args.save_input);saved.parent.mkdir(parents=True,exist_ok=True)
        saved.write_bytes(zlib.compress(pickle.dumps(dict(obs=data['obs'],speed=data['speed'],
            benchmark_reconciled=bool(data.get('benchmark_reconciled') or args.reconcile)),protocol=5),1))
    from skill_hit_tool.route_components import annotate_route_components
    from skill_hit_tool.completion_report import compare_all_skills, _skill_id
    context = annotate_route_components([o for o in data['obs'] if args.actor == '*' or str(o.source_id).lower() == args.actor.lower()])
    rows = [o for o in context if args.skill == '*' or _skill_id(o.skill_id) == args.skill]
    if args.stratified:
        from collections import defaultdict
        groups=defaultdict(list)
        for row in rows: groups[(row.source_id,row.job,_skill_id(row.skill_id))].append(row)
        selected=set()
        for group in groups.values():
            for i in range(min(args.stratified,len(group))):
                selected.add(id(group[round(i*(len(group)-1)/max(1,args.stratified-1))]))
        rows=[r for r in rows if id(r)in selected]
    if args.limit: rows = rows[:args.limit]
    if not rows: raise RuntimeError('No observations selected')
    if args.compare:
        runs = []
        for flag in ('0','1','1','0')[:args.rounds]:
            os.environ['DN_HIT_EXACT_FIT_CACHE'] = flag
            os.environ['DN_HIT_EXACT_WEIGHT_NEIGHBORS'] = flag if args.neighbors else '0'
            began = time.perf_counter()
            payload = compare_all_skills(rows,repo,data['speed'],context_observations=context)
            elapsed = time.perf_counter()-began
            digest = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
            runs.append(dict(enabled=flag=='1',seconds=elapsed,sha256=digest))
            print(json.dumps(runs[-1]),flush=True)
        result = dict(rows=len(rows),neighbor_experiment=args.neighbors,all_fields_identical=len({r['sha256']for r in runs})==1,runs=runs,
                      jobs=sorted({o.job for o in rows}),skills=len({(o.job,_skill_id(o.skill_id))for o in rows}),
                      cast_rows=len(payload['cast_rows']),skill_rows=len(payload['skill_rows']),
                      timeline=str(repo.path),timeline_sha256=hashlib.sha256(repo.path.read_bytes()).hexdigest(),
                      source=str(ROOT),prepared=str(Path(args.prepared).resolve()),scope='Hit fitting only; no decode, axis or support scoring')
        result['runtime_modules']={name:str(module.__file__) for name,module in sys.modules.items()
                                   if name in {'act_hit_timeline_comparator.core','act_hit_timeline_comparator.stage_estimator','skill_hit_tool.completion_report'}}
        result['baseline_mean_seconds']=sum(r['seconds']for r in runs if not r['enabled'])/sum(not r['enabled']for r in runs)
        result['optimized_mean_seconds']=sum(r['seconds']for r in runs if r['enabled'])/sum(r['enabled']for r in runs)
        result['reduction_percent']=(1-result['optimized_mean_seconds']/result['baseline_mean_seconds'])*100
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
        if not result['all_fields_identical']: raise RuntimeError('Fit output changed')
        return
    profile = cProfile.Profile()
    started = time.perf_counter()
    if args.profile: profile.enable()
    report = compare_all_skills(rows, repo, data['speed'], context_observations=context)
    if args.profile: profile.disable()
    elapsed = time.perf_counter()-started
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(seconds=elapsed, rows=len(rows), report=report), ensure_ascii=False), 'utf8')
    if args.profile:
        with output.with_suffix('.profile.txt').open('w',encoding='utf8') as handle:
            pstats.Stats(profile, stream=handle).sort_stats('cumulative').print_stats(45)
    print(json.dumps(dict(seconds=elapsed,rows=len(rows),output=str(output)), ensure_ascii=False))

if __name__ == '__main__': main()
