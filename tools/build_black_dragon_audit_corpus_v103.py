from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.capture_sources import _V576PacketEventDecoder  # noqa: E402
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger  # noqa: E402
from dragon_nest_builder.live_dps.npcap import (  # noqa: E402
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
    decode_captured_frame,
)


DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DATA_ROOT = ROOT / "dragon_nest_builder" / "data"
MAGIC = b"DNDP1\x00"
FRAME_HEADER = struct.Struct("<dBII")
RELIABLE_NEST_CONFIDENCE = frozenset(
    {"exact_map_id", "entry_template", "template", "template_set", "dominant_template_set"}
)


def _is_black_dragon(name: object) -> bool:
    text = str(name or "").strip()
    return "黑龙" in text and ("经典" in text or "硬核" in text)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_until_reliable_nest(path: Path) -> dict[str, object]:
    """Decode only until the first reliable formal-map identity is available."""

    decoder = _V576PacketEventDecoder(DATA_ROOT, {})
    transport = V576TransportReassembler()
    ledger = V576EventLedger()
    frame_count = 0
    stopped_early = False
    with path.open("rb") as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"不是 DNDP1 抓包：{path}")
        while True:
            header = stream.read(FRAME_HEADER.size)
            if not header:
                break
            if len(header) != FRAME_HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = FRAME_HEADER.unpack(header)
            frame = stream.read(captured_length)
            if len(frame) != captured_length:
                break
            frame_count += 1
            if adapter in {
                IDENTITY_METADATA_ADAPTER_INDEX,
                IDENTITY_TRACE_ADAPTER_INDEX,
                SESSION_EVIDENCE_ADAPTER_INDEX,
            }:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            logical_packets = [
                *transport.feed(packet),
                *transport.flush_stale(float(timestamp)),
            ]
            for logical_packet in logical_packets:
                if ledger.accept(logical_packet):
                    decoder.feed_events(logical_packet)
            if (
                decoder.detected_nest_name
                and decoder.detected_nest_confidence in RELIABLE_NEST_CONFIDENCE
            ):
                stopped_early = True
                break
    return {
        "file": str(path),
        "frames_scanned": frame_count,
        "stopped_early": stopped_early,
        "nest": decoder.detected_nest_name,
        "confidence": decoder.detected_nest_confidence,
        "mapHistory": [dict(row) for row in decoder.detected_map_history],
    }


def _audit(path: Path) -> dict[str, object]:
    try:
        return {"ok": True, **_audit_until_reliable_nest(path)}
    except Exception as exc:  # corpus discovery must keep every failure visible
        return {"ok": False, "file": str(path), "error": repr(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按抓包内场景证据建立黑龙经典/硬核伤害归因审计语料。"
    )
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--exclude-report", type=Path, required=True)
    parser.add_argument(
        "--candidate-list",
        type=Path,
        help=(
            "可选的候选路径清单。只用于减少重放量；最终是否入选仍只看抓包内场景证据。"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    previous = json.loads(args.exclude_report.read_text(encoding="utf-8"))
    excluded_paths = {
        str(Path(row["capture_path"]).resolve()).casefold()
        for row in previous.get("capture_manifest", ())
    }
    excluded_hashes = {
        str(row.get("capture_sha256") or "").strip().casefold()
        for row in previous.get("capture_manifest", ())
        if str(row.get("capture_sha256") or "").strip()
    }
    if args.candidate_list:
        discovered = [
            Path(line.strip()).resolve()
            for line in args.candidate_list.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    else:
        discovered = [path.resolve() for path in sorted(args.capture_root.glob("*.dpscap"))]
    candidates = [
        path
        for path in discovered
        if path.is_file() and str(path).casefold() not in excluded_paths
    ]

    rows: list[dict[str, object]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(_audit, path): path for path in candidates}
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: str(row.get("file") or "").casefold())

    selected: list[str] = []
    duplicate_previous: list[dict[str, str]] = []
    selected_hashes: set[str] = set()
    duplicate_current: list[dict[str, str]] = []
    first_by_hash: dict[str, str] = {}
    for row in rows:
        if not row.get("ok") or not _is_black_dragon(row.get("nest")):
            continue
        resolved = str(Path(str(row["file"])).resolve())
        digest = _hash_file(Path(resolved)).casefold()
        row["capture_sha256"] = digest
        if digest in excluded_hashes:
            duplicate_previous.append({"file": resolved, "capture_sha256": digest})
            continue
        if digest in selected_hashes:
            duplicate_current.append(
                {"file": resolved, "kept": first_by_hash[digest], "capture_sha256": digest}
            )
            continue
        selected_hashes.add(digest)
        first_by_hash[digest] = resolved
        selected.append(resolved)
    payload = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_policy": (
            "current decoder must report a nest name containing 黑龙 and 经典/硬核; "
            "filenames are never admission evidence"
        ),
        "excluded_report": str(args.exclude_report.resolve()),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "selected_captures": selected,
        "duplicates_of_previous_corpus": duplicate_previous,
        "duplicates_within_new_corpus": duplicate_current,
        "audits": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
