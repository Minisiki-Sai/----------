"""In-process A/B only; does not modify production parsing or cache defaults."""
import functools
import hashlib
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as wire
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource

def main(path):
    original = wire._v576_wire_marker_pattern
    results = []
    try:
        for capacity in (16, 128, 128, 16):
            wire._v576_wire_marker_pattern = functools.lru_cache(maxsize=capacity)(original.__wrapped__)
            # Same cold protocol caches each round, without touching a running ACT.
            for value in vars(wire).values():
                clear = getattr(value, "cache_clear", None)
                if callable(clear):
                    clear()
            source = V576DiagnosticReplaySource(path, ROOT/"dragon_nest_builder/data")
            try:
                start = time.perf_counter()
                events = source.poll()
                elapsed = time.perf_counter()-start
                digest = hashlib.sha256()
                for event in events:
                    digest.update(type(event).__name__.encode())
                    digest.update(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True).encode())
                result = dict(capacity=capacity, elapsed_seconds=elapsed, events=len(events),
                              digest=digest.hexdigest(), cache=wire._v576_wire_marker_pattern.cache_info()._asdict())
                results.append(result)
                print(json.dumps(result), flush=True)
            finally:
                source.close()
        assert len({r["digest"] for r in results}) == 1, "Event output mismatch"
        dest = ROOT/"outputs/verification/decode_backlog_causes_20261020/marker_cache_ab.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(dict(file=path.name, results=results,
            note="ABBA sequential replay; not live audible latency or a general speed guarantee"),
            ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        wire._v576_wire_marker_pattern = original

if __name__ == "__main__":
    main(Path(sys.argv[1]))
