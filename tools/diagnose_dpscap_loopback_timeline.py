from __future__ import annotations

import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

from clean_dpscap_game_traffic import FRAME_HEADER, MAGIC, _transport_tuple


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        raise SystemExit("usage: diagnose_dpscap_loopback_timeline.py CAPTURE.dpscap")
    path = Path(sys.argv[1])
    first_timestamp: float | None = None
    first_loopback: float | None = None
    last_loopback: float | None = None
    totals: Counter[str] = Counter()
    buckets: defaultdict[int, Counter[str]] = defaultdict(Counter)
    with path.open("rb", buffering=4 * 1024 * 1024) as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError("不是有效的 Dragon Nest .dpscap 统计记录")
        while True:
            header = handle.read(FRAME_HEADER.size)
            if not header:
                break
            if len(header) != FRAME_HEADER.size:
                break
            timestamp, _adapter, _original_length, captured_length = FRAME_HEADER.unpack(header)
            head_length = min(captured_length, 192)
            frame_head = handle.read(head_length)
            if len(frame_head) != head_length:
                break
            remaining = captured_length - head_length
            if remaining:
                handle.seek(remaining, 1)
            if first_timestamp is None:
                first_timestamp = timestamp
            elapsed = max(0.0, timestamp - first_timestamp)
            bucket = int(elapsed // 100) * 100
            transport = _transport_tuple(frame_head)
            if transport is None:
                kind = "unsupported"
            else:
                protocol, source_ip, source_port, destination_ip, destination_port = transport
                if source_ip.startswith("127.") or destination_ip.startswith("127."):
                    kind = "loopback_48710" if 48710 in {source_port, destination_port} else "loopback_other"
                    first_loopback = timestamp if first_loopback is None else first_loopback
                    last_loopback = timestamp
                else:
                    kind = "game_tcp" if protocol == 6 else "game_udp"
            totals[kind] += 1
            totals[f"{kind}_bytes"] += captured_length
            buckets[bucket][kind] += 1
            buckets[bucket][f"{kind}_bytes"] += captured_length
    print(json.dumps({
        "totals": totals,
        "first_loopback_seconds": None if first_loopback is None or first_timestamp is None else first_loopback - first_timestamp,
        "last_loopback_seconds": None if last_loopback is None or first_timestamp is None else last_loopback - first_timestamp,
        "buckets": {str(key): value for key, value in sorted(buckets.items()) if 2300 <= key <= 4100 or value.get("loopback_48710")},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
