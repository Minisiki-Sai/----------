"""Read-only field-log audit; writes a local report, never scores or uploads."""
import json
import re
import struct
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAP = Path.home() / "Documents/DragonNestDpsCapture/live_dps_diagnostics"
OUT = ROOT / "outputs/verification/voice_latency_1020"
HEADER = struct.Struct("<dBII")
OUT.mkdir(parents=True, exist_ok=True)


def stamp(t):
    return datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


def bounds(path):
    timestamps = []
    with path.open("rb") as handle:
        assert handle.read(6) == b"DNDP1\0"
        while head := handle.read(HEADER.size):
            if len(head) != HEADER.size:
                break
            t, adapter, original, size = HEADER.unpack(head)
            handle.seek(size, 1)
            if 1792425600 < t < 1792598400:
                timestamps.append(t)
    return min(timestamps), max(timestamps), len(timestamps)


def value(row, key):
    val = row
    for part in key.split("."):
        val = val.get(part, {}) if isinstance(val, dict) else {}
    return float(val or 0) if isinstance(val, (int, float)) else 0


keys = [
    "decoderQueueDelayMs", "captureToDecodeMs", "npcapReaderLagMs",
    "snapshotBuildMs", "snapshotLockWaitMs", "snapshotLockHeldMs", "snapshotOutsideLockMs",
    "decodeBatchWallMs", "decodeBatchCpuMs", "pendingRawFrames",
    "statisticsPipeline.statisticsLockWaitMs", "statisticsPipeline.statisticsApplyMs",
    "statisticsPipeline.identityAndProfileMs", "historyWriter.lastWriteMs",
]
rows = [json.loads(line) for line in (CAP / "overlay_latency.jsonl").read_text("utf8").splitlines() if line.strip()]
rows = [r for r in rows if r["recordedAt"].startswith("2026-10-20")]
for row in rows:
    row["_epoch"] = datetime.fromisoformat(row["recordedAt"]).timestamp()
with urllib.request.urlopen("http://127.0.0.1:48710/api/jobs", timeout=15) as response:
    jobs = json.load(response)["jobs"]
jobs = [{k: v for k, v in r.items() if k != "result"} for r in jobs]
first_score = min(r["createdAt"] for r in jobs if r.get("kind") == "replay-score")
desktop = (CAP.parent / "crash_diagnostics/desktop_1792502239_46764.log").read_text("utf8")
shows = [int(t) / 1000 for t in re.findall(
    r"\[(\d+)\] \[overlay-native-visibility\]\s+requested_visible=true", desktop)]
report = {"note": "Only threshold-triggered samples, at most once per 10 s; not all-event latency percentiles.",
          "firstScoreCreatedAt": stamp(first_score), "jobs": jobs, "captures": []}
captures = sorted(((p, bounds(p)) for p in CAP.glob("1020竞速*.dpscap")), key=lambda item: item[1][1])
previous_end = 0
for path, (earliest_evidence, end, count) in captures:
    # Files prepend historical identity packets. Their earliest timestamp is
    # NOT capture start. Pair independent visibility transitions to each run.
    candidates = [t for t in shows if previous_end < t < end]
    assert len(candidates) == 1, (path.name, candidates)
    start = candidates[0]
    previous_end = end
    selected = [r for r in rows if start <= r["_epoch"] <= end + 2]
    opening = [r for r in selected if r["_epoch"] <= start + 90]
    item = {"file": path.name, "overlayShownAt": stamp(start), "end": stamp(end),
            "earliestPrependedEvidence": stamp(earliest_evidence),
            "packets": count, "loggedSamples": len(selected), "opening90sSamples": len(opening),
            "maxima": {key: max((value(r, key) for r in selected), default=0) for key in keys},
            "openingMaxima": {key: max((value(r, key) for r in opening), default=0) for key in keys},
            "beforeAnyScore": [r for r in selected if r["_epoch"] < first_score],
            "worstDecode": sorted(selected, key=lambda r: value(r, "decoderQueueDelayMs"), reverse=True)[:4],
            "openingSamples": opening}
    report["captures"].append(item)
    print(json.dumps({k: v for k, v in item.items() if k not in ("beforeAnyScore", "worstDecode", "openingSamples")}, ensure_ascii=False), flush=True)
(OUT / "field_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf8")
print("firstScore", stamp(first_score), flush=True)
