from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from dragon_nest_builder.live_dps.dungeon_map_catalog import (
    resolve_dungeon_map_id,
)
from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    decode_captured_frame,
    decode_v576_map_identity,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


@dataclass(frozen=True, slots=True)
class MapObservation:
    relative_time: float
    map_id: int
    map_name: str
    official_name: str
    transport: str
    direction: str
    layout: str
    configured: bool
    active: bool
    authoritative: bool


@dataclass(frozen=True, slots=True)
class CaptureAudit:
    path: str
    size: int
    observations: tuple[MapObservation, ...]
    error: str = ""


def audit_capture(path: Path) -> CaptureAudit:
    transport = V576TransportReassembler()
    origin: float | None = None
    observations: list[MapObservation] = []

    def observe(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = float(packet.timestamp_seconds)
        decoded = decode_v576_map_identity(
            packet.payload,
            packet.inferred_direction(),
        )
        if decoded is None:
            return
        entry = resolve_dungeon_map_id(decoded.map_id)
        observation = MapObservation(
            relative_time=max(
                0.0,
                float(packet.timestamp_seconds) - float(origin),
            ),
            map_id=int(decoded.map_id),
            map_name=entry.display_name if entry is not None else "",
            official_name=entry.official_name if entry is not None else "",
            transport=str(packet.transport or ""),
            direction=str(packet.inferred_direction() or ""),
            layout=decoded.layout,
            configured=bool(entry is not None and entry.display_name),
            active=bool(decoded.active),
            authoritative=bool(
                getattr(decoded, "authoritative", True)
            ),
        )
        if (
            observations
            and observations[-1].map_id == observation.map_id
            and observations[-1].layout == observation.layout
            and observations[-1].active == observation.active
            and observations[-1].authoritative
            == observation.authoritative
        ):
            return
        observations.append(observation)

    try:
        for timestamp, adapter, original_length, frame in read_trace(path):
            # 254/255 are appended identity metadata, not original scene
            # traffic, and must not be treated as a map transition.
            if adapter in {254, 255}:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical_packet in transport.feed(packet):
                observe(logical_packet)
        for logical_packet in transport.flush():
            observe(logical_packet)
        return CaptureAudit(
            path=str(path),
            size=path.stat().st_size,
            observations=tuple(observations),
        )
    except Exception as exc:  # pragma: no cover - diagnostic resilience
        return CaptureAudit(
            path=str(path),
            size=path.stat().st_size if path.exists() else 0,
            observations=tuple(observations),
            error=f"{type(exc).__name__}: {exc}",
        )


def _audit_path(path_text: str) -> CaptureAudit:
    return audit_capture(Path(path_text))


def _capture_paths(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        if item.is_dir():
            paths.extend(item.rglob("*.dpscap"))
        elif item.suffix.lower() == ".dpscap" and item.exists():
            paths.append(item)
    return sorted(set(path.resolve() for path in paths), key=str)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit exact map-ID packets in one or more DNDP1 captures and "
            "report IDs missing from the bundled map catalog."
        )
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    paths = _capture_paths(args.inputs)
    if not paths:
        parser.error("no .dpscap files found")

    results: list[CaptureAudit] = []
    worker_count = max(1, min(int(args.workers), len(paths)))
    if worker_count == 1:
        for index, path in enumerate(paths, 1):
            results.append(audit_capture(path))
            print(f"[{index}/{len(paths)}] {path.name}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            pending = {
                executor.submit(_audit_path, str(path)): path for path in paths
            }
            for index, future in enumerate(as_completed(pending), 1):
                result = future.result()
                results.append(result)
                print(
                    f"[{index}/{len(paths)}] {Path(result.path).name}",
                    flush=True,
                )
    results.sort(key=lambda row: row.path)

    unknown_ids = sorted(
        {
            observation.map_id
            for result in results
            for observation in result.observations
            if not observation.configured
        }
    )
    exact_files = sum(
        any(row.authoritative for row in result.observations)
        for result in results
    )
    transition_files = sum(
        len({row.map_id for row in result.observations}) > 1
        for result in results
    )
    errors = [result for result in results if result.error]
    print(
        "summary "
        f"captures={len(results)} exact_map_files={exact_files} "
        f"transition_files={transition_files} "
        f"unknown_ids={unknown_ids} errors={len(errors)}"
    )
    for result in results:
        if not result.observations and not result.error:
            continue
        sequence = " -> ".join(
            (
                f"{row.map_id}:{row.map_name or '<UNCONFIGURED>'}"
                f"@{row.relative_time:.3f}s"
                f"[{('enter' if row.active else 'leave') if row.authoritative else 'select'}:"
                f"{row.layout}]"
            )
            for row in result.observations
        )
        print(f"{result.path}: {sequence or result.error}")

    if args.json:
        payload = {
            "summary": {
                "captures": len(results),
                "exact_map_files": exact_files,
                "transition_files": transition_files,
                "unknown_ids": unknown_ids,
                "errors": len(errors),
            },
            "captures": [asdict(result) for result in results],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
