"""Read-only production checks; never prints credentials or player identities."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.supabase_score_sync import TeamScoreSupabaseSyncService
from dragon_nest_builder.live_dps.v410_score_sync import load_v410_score_sync_config

config = load_v410_score_sync_config()
service = TeamScoreSupabaseSyncService(config.endpoint, config.upload_token, config.installation_id, timeout_seconds=100)
checks = []
def run(name, operation):
    start = time.monotonic()
    result = operation()
    checks.append({"check": name, "seconds": round(time.monotonic() - start, 3), **result})
    print(json.dumps(checks[-1], ensure_ascii=False))

profession = service.fetch_database_profession_ranking()
jobs = [row["job"] for row in profession.get("rows", [])]
assert jobs, "Existing profession ranking unavailable"
run("existing profession API", lambda: {"jobCount": len(jobs), "ok": True})

def distributions():
    rows = service.fetch_database_profession_ranking(section="distribution", job=jobs[0])["rows"]
    assert {row["metric"] for row in rows} == {"totalScore", "outputScore", "supportScore", "actualDps"}
    for row in rows:
        assert sum(bin["count"] for bin in row["bins"]) == row["sampleCount"]
        if row["sampleCount"]:
            assert len(row["bins"]) == 20
            widths = [item["upper"] - item["lower"] for item in row["bins"]]
            assert all(abs(width - widths[0]) <= max(1e-8, widths[0] * 1e-9) for width in widths)
            assert all(abs(left["upper"] - right["lower"]) < 1e-8
                       for left, right in zip(row["bins"], row["bins"][1:]))
    return {"metrics": 4, "bins": 20, "binTotalsMatchSamples": True, "ok": True}
run("real histogram API", distributions)

def support_benchmarks():
    result = service.fetch_database_profession_ranking(section="support", job="冰灵",
                                                      completion_status="cleared")
    rows = result["rows"]
    assert rows and result["benchmarkScope"] == "same-effect-kind-per-character-skill"
    assert all(row.get("allProfessionCharacterCount", 0) >= row["characterCount"] for row in rows)
    for row in rows:
        assert row["allProfessionContributionP90"] >= row["allProfessionContributionMedian"]
        assert "actualCoverageP90" in row and "completionP90" in row
        for metric in ("actualCoverage", "completion"):
            median, p90 = row[metric + "Median"], row[metric + "P90"]
            assert (median is None) == (p90 is None)
            if median is not None:
                assert p90 >= median
    return {"effectCount": len(rows), "realCrossProfessionBenchmarks": True,
            "coverageAndCompletionP90": True, "ok": True}
run("all-profession auxiliary benchmarks", support_benchmarks)

teams = service.fetch_database_profession_ranking(section="teams")["rows"]
values = [row["totalDpsMedian"] for row in teams if row.get("totalDpsMedian") is not None]
assert values == sorted(values, reverse=True)
run("DPS-first team ranking", lambda: {"combinationCount": len(teams), "sorted": True, "ok": True})

def details():
    captures = service.fetch_database_profession_ranking(section="team-captures", composition=teams[0]["composition"])["rows"]
    assert captures
    first = captures[0]
    detail = service.fetch_profession_history_detail(first["job"], first["historyId"])
    assert detail and detail.get("teamMembers")
    assert detail.get("bossDebuffs") or detail.get("bossDebuffsMessage")
    return {"battleCount": len(captures), "teamMembers": len(detail["teamMembers"]),
            "oldDebuffMissingExplicit": bool(detail.get("bossDebuffsMessage")), "ok": True}
run("team fight and legacy history detail", details)
