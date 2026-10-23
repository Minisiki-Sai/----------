"""Read-only production ranking through the real local HTTP/native/sync chain.

Uses an isolated ephemeral port without starting capture or touching the open ACT.
Only aggregate verification results are printed, never credentials or identities.
"""
from __future__ import annotations
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import v420_backend
from dragon_nest_builder.live_dps import v410_score_sync as sync
from dragon_nest_builder.native.replay_score_service import NativeReplayScoreMixin

manager = object.__new__(sync.V410ScoreSyncManager)
manager._state_lock = threading.RLock()
manager._history_request_lock = threading.Lock()
manager._history_cache = {}
manager._history_cache_ttl_seconds = sync._HISTORY_CACHE_TTL_SECONDS
manager._history_cache_max_entries = sync._HISTORY_CACHE_MAX_ENTRIES
native = NativeReplayScoreMixin()
native._score_sync = manager
v420_backend.ENGINE = SimpleNamespace(native=native)

class QuietHandler(v420_backend.ApiHandler):
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

def query(section, **filters):
    url = f"http://127.0.0.1:{server.server_port}/api/database-ranking?" + urlencode(dict(section=section, **filters))
    with urlopen(url, timeout=110) as response:
        value = json.load(response)
    assert value["state"] in ("ready", "empty"), "Ranking did not complete"
    return value

try:
    professions = query("professions")["rows"]
    assert professions
    distributions = query("distribution", job=professions[0]["job"])["rows"]
    assert len(distributions) == 4
    assert all(sum(b["count"] for b in row["bins"]) == row["sampleCount"] for row in distributions)
    teams = query("teams")["rows"]
    assert teams
    captures = query("team-captures", composition=teams[0]["composition"])["rows"]
    assert captures
    filters = dict(job="冰灵", jobs=json.dumps(["冰灵"], ensure_ascii=False), nest="黑龙经典",
                   completionStatus="cleared", sortKey="actualDps", sortDirection="desc", includeFacets="0")
    url = f"http://127.0.0.1:{server.server_port}/api/score-history/profession?" + urlencode(filters)
    with urlopen(url, timeout=110) as response:
        ranking = json.load(response)
    assert ranking["state"] == "ready", "Cleared Dragon profession ranking unavailable"
    items = ranking["items"]
    assert items and all(row["job"] == "冰灵" and row["nest"] == "黑龙经典" and row["completionStatus"] == "cleared" for row in items)
    values = [row["actualDps"] for row in items if row.get("actualDps") is not None]
    assert values == sorted(values, reverse=True), "Profession ranking not sorted by DPS"
    assert all(key in items[0] for key in ("outputSeconds", "score_schema_version", "totalScore", "outputScore", "supportScore", "specialtyScore", "survivalPenalty"))
    print(json.dumps({"localHttpNativeSyncCloudChain": "passed", "professionCount": len(professions),
                      "distributionMetrics": len(distributions), "teamCount": len(teams),
                      "selectedCompositionBattles": len(captures), "clearedDragonProfessionRankingRows": len(items),
                      "clearedDragonRankingSortedByDps": True}, ensure_ascii=False))
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
