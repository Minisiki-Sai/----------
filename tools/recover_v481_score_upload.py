"""Retry existing score rows after cloud codec fix; never rescore or upload captures."""
import dataclasses
import json
import sqlite3
from pathlib import Path

from dragon_nest_builder.live_dps.team_score_sync import TeamScoreSyncStore, default_team_score_sync_path
from dragon_nest_builder.live_dps.supabase_score_sync import TeamScoreSupabaseSyncService
from dragon_nest_builder.live_dps.v410_score_sync import load_v410_score_sync_config

root = Path(__file__).resolve().parents[1]
out = root / 'outputs/verification/v481_upload_recovery'
out.mkdir(parents=True, exist_ok=True)
backup = out / 'before.sqlite3'
if not backup.exists():
    with sqlite3.connect(default_team_score_sync_path()) as source, sqlite3.connect(backup) as dest:
        source.backup(dest)
cfg = load_v410_score_sync_config()
assert cfg.ready
service = TeamScoreSupabaseSyncService(cfg.endpoint, cfg.upload_token, cfg.installation_id, timeout_seconds=120)
with TeamScoreSyncStore() as store:
    pending = store.pending_supabase_captures(limit=100)
    print('Pending captures:', len(pending), flush=True)
    result = service.sync_store(store, max_batches=50, upload_capture_files=False)
    report = {'result':dataclasses.asdict(result), 'verified':[]}
    for capture in pending:
        row = capture.rows[0]
        history_id = f'{capture.capture_hash}:{row.sample_ordinal}'
        detail = service.fetch_profession_history_detail(row.job, history_id)
        assert detail, f'Cloud detail missing: {history_id}'
        report['verified'].append({'source_name':capture.source_name,'history_id':history_id,'job':row.job})
        print('Cloud detail verified:',capture.source_name,flush=True)
    (out / 'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
