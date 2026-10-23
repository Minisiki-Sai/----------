"""Read-only production replay; diagnostic exports only, no scoring changes."""
from pathlib import Path
import sys, json, pickle, time
from dataclasses import asdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.score_hit_evidence import addon_root, repository
addon_root()
from act_hit_timeline_comparator.app import analyze_capture
from act_hit_timeline_comparator.core import best_comparison, comparison_to_dict
from skill_hit_tool.transformed_forms import variants_for_observation
from skill_hit_tool.document_routes import select_cast_routes
from skill_hit_tool.hit_ledger import record_kind

OUT = ROOT / 'outputs/verification/hit_matching_20261020'
OUT.mkdir(parents=True, exist_ok=True)
CAP = Path(r'C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics\1019噬魂竞速第十七把.dpscap')

def main():
    repo = repository()
    cache = OUT / 'observations.pkl'
    if cache.exists():
        obs, audit, timelines, speeds = pickle.loads(cache.read_bytes())
    else:
        began = time.monotonic()
        last = [0]
        def progress(*args):
            now = time.monotonic()
            if now-last[0] > 10:
                print('decode', args, round(now-began, 2), flush=True)
                last[0] = now
        obs, audit, timelines, speeds = analyze_capture(CAP, repo, progress_callback=progress)
        cache.write_bytes(pickle.dumps((obs, audit, timelines, speeds), protocol=5))
    print('observations', len(obs), flush=True)
    index = []
    for i, o in enumerate(obs):
        if record_kind(o) != '施放' or not o.hit_times:
            continue
        speed = speeds.get((o.source_id, o.cast_timestamp_ms), 0.)
        variants = variants_for_observation(repo, o, speed,
            condition_choice='自动/无条件选择', include_optional=False)
        variants = select_cast_routes(variants, o, obs, speeds)
        best, candidates = best_comparison(variants, o.hit_times, o.hit_amounts,
            o.critical_flags, external_speed_percent=speed, observation=o)
        if best is None:
            continue
        detail = comparison_to_dict(best, o)
        name = f'{i:05d}_{o.skill_id}_{o.cast_timestamp_ms}.json'
        candidate_rows = []
        for c in candidates:
            d = comparison_to_dict(c, o)
            d['score'] = c.score
            d['expected_slots'] = [asdict(h) for h in c.variant.hits]
            candidate_rows.append(d)
        payload = dict(observation=asdict(o), speed=float(speed),
            selected=detail, candidates=candidate_rows)
        (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        index.append(dict(file=name, source=o.source_name, job=o.job,
            skill=o.skill_name, skill_id=str(o.skill_id), time=o.cast_timestamp_ms,
            summary=detail['summary'], realization=detail['realization'],
            form=detail['variant']['form'], route=detail['variant']['route'],
            candidate_counts=sorted(set(d['summary']['expected'] for d in candidate_rows))))
        if len(index) % 25 == 0:
            print('compared', len(index), '/', len(obs), flush=True)
    (OUT / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    print('done',len(index), flush=True)

if __name__ == '__main__':
    main()
