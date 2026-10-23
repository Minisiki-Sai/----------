"""Read-only cloud verification of the two already authorized uploads."""
from __future__ import annotations
import json
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dragon_nest_builder.live_dps.supabase_score_sync import TeamScoreSupabaseSyncService
from dragon_nest_builder.live_dps.team_score_sync import default_team_score_sync_path
from dragon_nest_builder.live_dps.v410_score_sync import load_v410_score_sync_config
from .retry_v477_timeline_pending_20261012 import TARGETS, EXPECTED_SERVICE, ROOT


def main():
    config = load_v410_score_sync_config()
    service = TeamScoreSupabaseSyncService(config.endpoint, config.upload_token,
        config.installation_id, timeout_seconds=120)
    if service.health_check() != EXPECTED_SERVICE:
        raise RuntimeError("Unexpected deployed service")
    connection = sqlite3.connect(default_team_score_sync_path().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        local = [dict(row) for row in connection.execute(
            "SELECT capture_hash,sample_ordinal,job,total_score,output_score,support_score,score_detail_json "
            "FROM score_rows WHERE capture_hash IN (?,?) ORDER BY capture_hash,sample_ordinal", tuple(TARGETS))]
    finally:
        connection.close()

    def verify(row):
        history_id = row['capture_hash'] + ':' + str(row['sample_ordinal'])
        detail = service.fetch_profession_history_detail(row['job'], history_id)
        if not detail or detail.get('historyId') != history_id:
            raise RuntimeError(f"Cloud history missing: {history_id}")
        if detail.get('scoreSchemaVersion') != 20261012:
            raise RuntimeError(f"Wrong cloud algorithm: {history_id}")
        for local_key, remote_key in [('total_score', 'totalScore'), ('output_score', 'outputScore'), ('support_score', 'supportScore')]:
            if not math.isclose(float(row[local_key]), float(detail[remote_key]), rel_tol=1e-10, abs_tol=1e-8):
                raise RuntimeError(f"Cloud score mismatch: {history_id} {remote_key}")
        timeline = detail.get('compactTimeline') or {}
        context = timeline.get('displayContext') or {}
        local_detail = json.loads(row['score_detail_json'])
        local_timeline = local_detail.get('compact_timeline') or {}
        # Cloud removes the compressed bytes after decoding, but preserves
        # each compact track and the bounded display context.
        for key in ['v','q','d','n','a','t','e','s','o']:
            if key in local_timeline and timeline.get(key) != local_timeline[key]:
                raise RuntimeError(f"Cloud timeline mismatch: {history_id} {key}")
        if not context or context.get('score', {}).get('sourceId') != 'self':
            raise RuntimeError(f"Cloud display context missing: {history_id}")
        buffs = context.get('snapshot', {}).get('player_buff_series', [])
        return {'capture':TARGETS[row['capture_hash']], 'ordinal':row['sample_ordinal'],
            'job':row['job'], 'algorithm':detail['scoreSchemaVersion'], 'scores_match':True,
            'timeline_matches':True, 'display_buff_count':len(buffs),
            'buff_evidence_preserved':all('effect_code' in buff and 'state_source' in buff for buff in buffs)}

    with ThreadPoolExecutor(max_workers=2) as executor:
        verified = list(executor.map(verify, local))
    if len(verified) != 16 or not all(row['buff_evidence_preserved'] for row in verified):
        raise RuntimeError("Verification incomplete")
    report = {'service_version':EXPECTED_SERVICE,'algorithm':20261012,'verified_rows':len(verified),
        'records':verified,'installer_changed':False,'cloud_scores_read_only':True}
    output = ROOT / 'outputs' / 'v477_timeline_aura_cloud_fix_20261012' / 'cloud-verification.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
