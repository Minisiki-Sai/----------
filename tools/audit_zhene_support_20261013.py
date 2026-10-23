"""Read-only public score-history audit for the requested Inquisitor."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.supabase_score_sync import TeamScoreSupabaseSyncService
from dragon_nest_builder.live_dps.v410_score_sync import load_v410_score_sync_config


def main():
    config = load_v410_score_sync_config()
    service = TeamScoreSupabaseSyncService(config.endpoint, config.upload_token,
                                         config.installation_id, timeout_seconds=45)
    history = service.fetch_character_history('丨镇恶丨', limit=30)
    records = [{k:v for k,v in r.items() if not k.startswith('benchmark')} for r in history['items']]
    print(json.dumps(records,ensure_ascii=False),flush=True)
    details = []
    for row in records[:4]:
        detail = service.fetch_character_history_detail('丨镇恶丨', row['historyId'])
        details.append(detail)
        print('DETAIL',row['historyId'],list(detail),flush=True)
        for key,value in detail.items():
            if 'support' in key.lower():
                print(key,json.dumps(value,ensure_ascii=False),flush=True)
    (ROOT/'work/zhene_support_cloud_20261013.json').write_text(
        json.dumps(dict(records=records,details=details),ensure_ascii=False,indent=2),encoding='utf8')


if __name__ == '__main__':
    main()
