"""Correlate existing live latency records with capture wall-clock windows."""
import json
import struct
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/verification/voice_latency_1019'
CAP = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics')
HEADER = struct.Struct('<dBII')


def window(path):
    first, last, origin = float('inf'), 0, None
    with path.open('rb') as handle:
        assert handle.read(6) == b'DNDP1\0'
        while header := handle.read(HEADER.size):
            stamp, adapter, original, size = HEADER.unpack(header)
            handle.seek(size, 1)
            if adapter < 240:
                first, last = min(first, stamp), max(last, stamp)
                if origin is None: origin = stamp
    return first, last, origin


def main():
    logs = []
    for line in (CAP/'overlay_latency.jsonl').read_text(encoding='utf-8').splitlines():
        try:
            row = json.loads(line)
            if row['recordedAt'].startswith('2026-10-19'):
                row['wall_timestamp'] = datetime.fromisoformat(row['recordedAt']).timestamp()
                logs.append(row)
        except (ValueError, KeyError):
            pass
    reports = []
    for path in sorted(CAP.glob('1019噬魂*.dpscap')):
        first, last, origin = window(path)
        rows = [r for r in logs if first <= r['wall_timestamp'] <= last]
        fields = ['decoderQueueDelayMs','npcapReaderLagMs','captureToDecodeMs','snapshotBuildMs','pendingRawFrames']
        result = dict(file=path.name, first=first, last=last, origin=origin,
                      start=datetime.fromtimestamp(first).isoformat(), end=datetime.fromtimestamp(last).isoformat(),
                      logged_samples=len(rows), queue_over_500=sum(r.get('decoderQueueDelayMs',0)>500 for r in rows),
                      maxima={k:max((r.get(k,0) for r in rows),default=0) for k in fields}, rows=rows)
        reports.append(result)
        print(json.dumps({k:v for k,v in result.items() if k not in {'rows','first','last','origin'}}, ensure_ascii=False),flush=True)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'live_logs.json').write_text(json.dumps(reports,ensure_ascii=False),encoding='utf-8')


if __name__ == '__main__': main()
