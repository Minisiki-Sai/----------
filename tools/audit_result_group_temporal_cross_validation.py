from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_cast_damage_tokens import WireRow, audit_capture  # noqa: E402


WINDOWS_MS = (5, 10, 20, 50, 100, 250, 500, 1000)


def _group(row: WireRow) -> int:
    offset = row.marker_offset + 4
    return int.from_bytes(row.body[offset : offset + 4], "little")


def audit(paths: list[Path]) -> dict[str, Any]:
    totals: dict[int, Counter[str]] = {
        window: Counter() for window in WINDOWS_MS
    }
    per_capture: list[dict[str, Any]] = []
    run_totals: Counter[str] = Counter()
    for path in paths:
        decoded = audit_capture(path)
        exact: list[WireRow] = decoded["exact_damage"]
        nonexact: list[WireRow] = decoded["nonexact_damage"]
        anchors: dict[tuple[int, int], list[WireRow]] = defaultdict(list)
        for row in exact:
            anchors[(row.source_actor_id, _group(row))].append(row)
        capture_windows: dict[str, dict[str, int]] = {}
        for window_ms in WINDOWS_MS:
            stats: Counter[str] = Counter()
            window_seconds = window_ms / 1000.0
            for row in exact:
                peers = [
                    peer
                    for peer in anchors[(row.source_actor_id, _group(row))]
                    if peer is not row
                    and abs(peer.timestamp - row.timestamp) <= window_seconds
                ]
                identities = {peer.skill_id for peer in peers}
                if len(identities) == 1:
                    predicted = next(iter(identities))
                    stats[
                        "exact_support" if predicted == row.skill_id else "exact_conflict"
                    ] += 1
                elif len(identities) > 1:
                    stats["exact_ambiguous"] += 1
                else:
                    stats["exact_unvalidated"] += 1
            for row in nonexact:
                peers = [
                    peer
                    for peer in anchors.get(
                        (row.source_actor_id, _group(row)), ()
                    )
                    if abs(peer.timestamp - row.timestamp) <= window_seconds
                ]
                identities = {peer.skill_id for peer in peers}
                if len(identities) == 1:
                    stats["recoverable_nonexact"] += 1
                elif len(identities) > 1:
                    stats["ambiguous_nonexact"] += 1
                else:
                    stats["unanchored_nonexact"] += 1
            totals[window_ms].update(stats)
            capture_windows[str(window_ms)] = dict(stats)
        per_capture.append(
            {"capture": str(path), "windows": capture_windows}
        )
        ordered = sorted(
            (*exact, *nonexact), key=lambda row: row.packet_ordinal
        )
        exact_ordinals = {row.packet_ordinal for row in exact}
        actor_rows: dict[int, list[WireRow]] = defaultdict(list)
        for row in ordered:
            actor_rows[row.source_actor_id].append(row)
        capture_runs: Counter[str] = Counter()
        for rows in actor_rows.values():
            runs: list[list[WireRow]] = []
            for row in rows:
                if not runs or _group(runs[-1][-1]) != _group(row):
                    runs.append([row])
                else:
                    runs[-1].append(row)
            for run in runs:
                exact_rows = [
                    row for row in run
                    if row.packet_ordinal in exact_ordinals
                ]
                nonexact_rows = [
                    row for row in run
                    if row.packet_ordinal not in exact_ordinals
                ]
                identities = {row.skill_id for row in exact_rows}
                if len(identities) == 1:
                    capture_runs["unique_anchor_runs"] += 1
                    capture_runs["recoverable_nonexact"] += len(nonexact_rows)
                elif len(identities) > 1:
                    capture_runs["conflicting_anchor_runs"] += 1
                    capture_runs["ambiguous_nonexact"] += len(nonexact_rows)
                else:
                    capture_runs["unanchored_runs"] += 1
                    capture_runs["unanchored_nonexact"] += len(nonexact_rows)
                for row in exact_rows:
                    peers = [peer for peer in exact_rows if peer is not row]
                    peer_identities = {peer.skill_id for peer in peers}
                    if len(peer_identities) == 1:
                        predicted = next(iter(peer_identities))
                        capture_runs[
                            "exact_support"
                            if predicted == row.skill_id
                            else "exact_conflict"
                        ] += 1
                    elif len(peer_identities) > 1:
                        capture_runs["exact_ambiguous"] += 1
                    else:
                        capture_runs["exact_unvalidated"] += 1
        run_totals.update(capture_runs)
        per_capture[-1]["contiguous_runs"] = dict(capture_runs)
    return {
        "schema_version": 1,
        "truth_policy": (
            "Leave-one-out validation uses only another fixed-slot exact skill "
            "with the same actor and full direct37 result-group value."
        ),
        "captures": len(paths),
        "windows": {
            str(window): dict(stats) for window, stats in totals.items()
        },
        "contiguous_runs": dict(run_totals),
        "reports": per_capture,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit([path.resolve() for path in args.captures])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
