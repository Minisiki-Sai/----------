"""Offline raw-frame scheduling comparison. No game, score, upload, or live trace writes."""
import dataclasses
import hashlib
import json
import struct
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import sources, capture_storage
from dragon_nest_builder.live_dps.capture_sources import V576NpcapSource
from dragon_nest_builder.live_dps.voice_signals import VoiceSignalFeed

OUT = ROOT / "outputs/verification/opening_catchup_20261021"
HEADER = struct.Struct("<dBII")
CURRENT_LOOP = V576NpcapSource._decode_loop


def frames(path, limit=12000):
    result = []
    with path.open("rb") as handle:
        assert handle.read(6) == b"DNDP1\0"
        while header := handle.read(HEADER.size):
            stamp, adapter, original, length = HEADER.unpack(header)
            payload = handle.read(length)
            assert len(payload) == length
            # This is a raw-frame scheduling fixture, not a full scored replay.
            # Synthetic identity / boundary metadata is excluded from both arms.
            if adapter < 240:
                result.append((stamp, payload, original, adapter))
            if len(result) >= limit:
                break
    return result


class Stream:
    udp_ports = (12345,)
    tcp_ports = ()
    def __init__(self, rows):
        self.rows = list(rows)
    def start_trace(self, path, initial):
        return initial
    def read_packets(self, limit):
        result = self.rows[:limit]
        del self.rows[:limit]
        return result
    @property
    def packet_queue_stats(self):
        return {"pending": len(self.rows)}
    def read_error(self):
        return ""
    def close(self):
        pass


def old_opening(self):
    initial = self._initial_frames
    self._initial_frames = []
    if initial:
        self._decode_frames(initial, interruptible=True)
    sources.set_current_thread_low_priority()
    self._decoder_priority_mode = "low"
    CURRENT_LOOP(self)


def digest(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest()


def run(rows, mode):
    processed = 0
    signals = []
    first_batch = None
    original_decode = V576NpcapSource._decode_frames
    original_publish = VoiceSignalFeed.publish
    started = time.perf_counter()
    def decode(self, batch, **kwargs):
        nonlocal processed, first_batch
        result = original_decode(self, batch, **kwargs)
        processed += len(batch)
        if first_batch is None:
            first_batch = time.perf_counter()
        return result
    def publish(self, row):
        before = self._sequence
        result = original_publish(self, row)
        if self._sequence != before:
            signals.append(dict(row))
        return result
    with tempfile.TemporaryDirectory(prefix="dn-opening-") as temp, \
         patch.object(capture_storage, "capture_directory", return_value=Path(temp) / "traces"), \
         patch.object(V576NpcapSource, "_decode_loop",
                      old_opening if mode == "before" else CURRENT_LOOP), \
         patch.object(V576NpcapSource, "_decode_frames", decode), \
         patch.object(VoiceSignalFeed, "publish", publish):
        source = V576NpcapSource([12345], ROOT / "dragon_nest_builder/data",
                                 stream=Stream(rows[2048:]),
                                 preloaded_frames=tuple(rows[:2048]))
        initialized = time.perf_counter()
        events = []
        try:
            deadline = time.monotonic() + 180
            while processed < len(rows):
                events.extend(source.poll())
                if time.monotonic() > deadline:
                    raise TimeoutError("raw fixture decode did not complete")
                time.sleep(.001)
            events.extend(source.flush_pending())
            ended = time.perf_counter()
            result = {
                "mode": mode, "frames": len(rows), "events": len(events),
                "signals": len(signals),
                "totalSeconds": ended - started,
                "initSeconds": initialized - started,
                "firstBatchFromStartMs": ((first_batch or ended) - started) * 1000,
                "eventHash": digest([dataclasses.asdict(e) for e in events]),
                "signalHash": digest(signals),
            }
        finally:
            source.close()
    return result


if __name__ == "__main__":
    captures = Path.home() / "Documents/DragonNestDpsCapture/live_dps_diagnostics"
    OUT.mkdir(parents=True, exist_ok=True)
    # Warm the catalog once; no cold-vs-warm comparison masquerading as speedup.
    warm = run(frames(captures / "1020竞速第一把.dpscap", 128), "after")
    result = {"scope": "First 12000 Ethernet frames per capture; metadata excluded identically. "
                       "Burst scheduling only, not live audio latency or scoring verification.",
              "warmup": warm, "runs": []}
    for name in ("1020竞速第一把.dpscap", "1020竞速第六把.dpscap"):
        rows = frames(captures / name)
        for mode in ("before", "after", "after", "before"):
            item = dict(file=name, **run(rows, mode))
            result["runs"].append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
        subset = [r for r in result["runs"] if r["file"] == name]
        assert len({r["eventHash"] for r in subset}) == 1, "event/order mismatch"
        assert len({r["signalHash"] for r in subset}) == 1, "voice signal mismatch"
    (OUT / "raw_burst_comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
