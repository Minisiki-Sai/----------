"""Combat-only glyph audit; never scores, uploads, or changes runtime inference."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import gc
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.data_loader import DataStore, DATA_DIR
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.combat_glyph_inference import infer_combat_skill_glyphs
from dragon_nest_builder.live_dps.event_platform import event_platform_for
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.timeline_evidence import is_authoritative_timeline_cast


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def runtime_digest():
    h = hashlib.sha256()
    for p in sorted((ROOT/'dragon_nest_builder').rglob('*')):
        if p.is_file() and p.suffix in {'.py', '.json'} and '__pycache__' not in p.parts:
            h.update(str(p.relative_to(ROOT)).encode('utf-8'))
            h.update(digest(p).encode('ascii'))
    return h.hexdigest()


def inspect(path, output, store):
    dest = output/(path.stem+'.json')
    detail_path = output/(path.stem+'.parameters.jsonl.gz')
    if dest.exists() or detail_path.exists():
        raise FileExistsError(dest)
    capture_hash = digest(path)
    code_hash = runtime_digest()
    started = tick = time.monotonic()
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    agg = CombatAggregator()
    count = 0
    print(json.dumps({'start':path.name}, ensure_ascii=False), flush=True)
    try:
        for batch in source.iter_batches():
            for event in batch:
                agg.ingest(event)
                count += 1
            if time.monotonic()-tick > 25:
                print(json.dumps({'capture':path.name,'events':count,'elapsed':round(time.monotonic()-started,1)},ensure_ascii=False),flush=True)
                tick = time.monotonic()
    finally:
        source.close()
    snapshot = agg.snapshot(include_events=True)
    inferred, diagnostics = infer_combat_skill_glyphs(snapshot, store)
    platform = event_platform_for(snapshot)
    parameters = tuple(platform.stream('skill_parameter'))
    casts = tuple(platform.stream('skill_cast'))
    grouped = {}
    with gzip.open(detail_path, 'xt', encoding='utf-8') as out:
        for e in parameters:
            out.write(json.dumps(asdict(e),ensure_ascii=False)+'\n')
            key = (e.source_id,e.skill_id,e.raw_skill_id,e.skill_name,e.skill_level_row_id,e.effect_class_id,e.effect_code,e.parameters,e.duration_ms,e.permanent)
            if key not in grouped:
                grouped[key] = {'source_id':e.source_id,'job':e.job,'skill_id':e.skill_id,'raw_skill_id':e.raw_skill_id,'skill_name':e.skill_name,'level_row':e.skill_level_row_id,'effect_class':e.effect_class_id,'code':e.effect_code,'parameter':e.parameters,'duration_ms':e.duration_ms,'permanent':e.permanent,'count':0,'timestamps_ms':[],'targets':Counter(),'instances':set()}
            row = grouped[key]
            row['count'] += 1
            row['timestamps_ms'].append(e.timestamp_ms)
            row['targets'][e.target_id] += 1
            row['instances'].add(e.effect_instance_id)
    for row in grouped.values():
        row['instances'] = sorted(row['instances'])
        row['targets'] = dict(row['targets'])
    cast_groups = defaultdict(list)
    for e in casts:
        if is_authoritative_timeline_cast(e):
            cast_groups[(e.source_id,e.skill_id,e.skill_name,e.packet_kind)].append(e.timestamp_ms)
    buff_rows = []
    for series in snapshot.player_buff_series:
        row = asdict(series)
        if any(x in str(row.get('skill_name',''))+' '+str(row.get('label','')) for x in ('瞄准','精神','时间加速','鸡尾酒')):
            buff_rows.append(row)
    result = {'capture':str(path),'capture_sha256':capture_hash,'runtime_sha256':code_hash,
              'completed':True,'seconds':round(time.monotonic()-started,3),
              'recording_seconds':snapshot.elapsed_seconds,'total_damage':snapshot.total_damage,
              'players':[{'source_id':p.source_id,'name':p.name,'job':p.job,'glyphs':inferred.get(p.source_id,[])} for p in snapshot.players],
              'diagnostics':diagnostics,'parameter_groups':list(grouped.values()),
              'casts':[{'source_id':k[0],'skill_id':k[1],'skill_name':k[2],'packet_kind':k[3],'timestamps_ms':v} for k,v in cast_groups.items()],
              'buff_series':buff_rows,'parameter_details':str(detail_path),'parameter_details_sha256':digest(detail_path),
              'scope':'Battle parameter/cast/Buff evidence only; equipment/profile glyph fields never supplied to inference. Existing confidence numbers are heuristic, not calibrated accuracy.'}
    if capture_hash != digest(path) or code_hash != runtime_digest():
        raise RuntimeError('Capture/runtime changed during audit; output not accepted')
    with dest.open('x',encoding='utf-8') as out:
        json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps({'done':path.name,'seconds':result['seconds'],'players':len(result['players']),'glyphs':sum(len(p['glyphs']) for p in result['players'])},ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('captures',nargs='+',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    store=DataStore.load()
    for path in args.captures:
        inspect(path.resolve(),args.output.resolve(),store)
        gc.collect()


if __name__=='__main__':
    main()
