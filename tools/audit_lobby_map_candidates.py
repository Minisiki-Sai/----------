from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path

from dragon_nest_builder.live_dps.dungeon_map_catalog import (
    load_dungeon_map_catalog,
)
from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    _decode_v576_body,
    decode_captured_frame,
    decode_v576_map_identity,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


@dataclass(frozen=True, slots=True)
class Candidate:
    path: str
    time: float
    signature: str
    phase: int
    map_id: int
    map_name: str
    marker: int
    previous_leave_map_id: int | None = None
    previous_leave_delta: float | None = None
    next_enter_delta: float | None = None


def scan(path: Path) -> tuple[Candidate, ...]:
    catalog = load_dungeon_map_catalog()
    hub_ids = {
        map_id
        for map_id, entry in catalog.items()
        if int(entry.map_type) in {1, 2}
    }
    transport = V576TransportReassembler()
    origin: float | None = None
    found: list[Candidate] = []
    exact_events: list[tuple[float, bool, int]] = []

    def inspect(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = float(packet.timestamp_seconds)
        relative_time = max(
            0.0,
            float(packet.timestamp_seconds) - float(origin),
        )
        exact = decode_v576_map_identity(
            packet.payload,
            packet.inferred_direction(),
        )
        if exact is not None:
            exact_events.append(
                (relative_time, bool(exact.active), int(exact.map_id))
            )
        if (
            packet.transport.upper() != "UDP"
            or packet.inferred_direction() != "server_to_client"
            or len(packet.payload) != 14
        ):
            return
        signature = packet.payload[5:9].hex()
        for phase in range(13):
            decoded = _decode_v576_body(packet.payload, phase)
            map_id = int.from_bytes(decoded[10:14], "little")
            entry = catalog.get(map_id)
            if map_id not in hub_ids or entry is None:
                continue
            found.append(
                Candidate(
                    path=str(path),
                    time=relative_time,
                    signature=signature,
                    phase=phase,
                    map_id=map_id,
                    map_name=entry.display_name,
                    marker=int(decoded[9]),
                )
            )

    for timestamp, adapter, original_length, frame in read_trace(path):
        if adapter in {254, 255}:
            continue
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in transport.feed(packet):
            inspect(logical)
    for logical in transport.flush():
        inspect(logical)
    annotated: list[Candidate] = []
    for row in found:
        previous_leaves = [
            (event_time, map_id)
            for event_time, active, map_id in exact_events
            if not active and event_time <= row.time
        ]
        next_enters = [
            event_time
            for event_time, active, _map_id in exact_events
            if active and event_time >= row.time
        ]
        previous_leave = max(previous_leaves) if previous_leaves else None
        annotated.append(
            replace(
                row,
                previous_leave_map_id=(
                    previous_leave[1] if previous_leave is not None else None
                ),
                previous_leave_delta=(
                    row.time - previous_leave[0]
                    if previous_leave is not None
                    else None
                ),
                next_enter_delta=(
                    min(next_enters) - row.time
                    if next_enters
                    else None
                ),
            )
        )
    return tuple(annotated)


def _scan(path_text: str) -> tuple[Candidate, ...]:
    return scan(Path(path_text))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit candidate 14-byte server UDP lobby-map records."
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--occurrences", action="store_true")
    args = parser.parse_args()
    paths = sorted(args.root.rglob("*.dpscap"), key=str)
    if not paths:
        parser.error("no .dpscap captures found")
    rows: list[Candidate] = []
    workers = max(1, min(int(args.workers), len(paths)))
    if workers == 1:
        for index, path in enumerate(paths, 1):
            rows.extend(scan(path))
            if not args.quiet:
                print(f"[{index}/{len(paths)}] {path.name}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            pending = {executor.submit(_scan, str(path)): path for path in paths}
            for index, future in enumerate(as_completed(pending), 1):
                rows.extend(future.result())
                if not args.quiet:
                    print(
                        f"[{index}/{len(paths)}] {pending[future].name}",
                        flush=True,
                    )
    groups = Counter(
        (row.signature, row.phase, row.map_id, row.map_name)
        for row in rows
    )
    print(f"summary captures={len(paths)} candidates={len(rows)} groups={len(groups)}")
    for key, count in groups.most_common():
        print(f"count={count} key={key}")
    leave_groups = Counter(
        row.previous_leave_map_id
        for row in rows
        if row.previous_leave_map_id is not None
    )
    print("previous_leave_map_ids=" + repr(dict(leave_groups.most_common())))
    if args.occurrences:
        print("occurrences:")
        for row in sorted(rows, key=lambda item: (item.path, item.time)):
            print(
                f"{Path(row.path).name} t={row.time:.6f} sig={row.signature} "
                f"phase={row.phase} marker={row.marker} "
                f"map={row.map_id}:{row.map_name} "
                f"previous_leave_map={row.previous_leave_map_id} "
                f"after_leave={row.previous_leave_delta} "
                f"before_enter={row.next_enter_delta}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
