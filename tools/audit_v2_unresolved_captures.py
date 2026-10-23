from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.damage_ledger import (  # noqa: E402
    SkillAttributionLedger,
)
from dragon_nest_builder.live_dps.models import (  # noqa: E402
    CombatEvent,
    SkillCastEvent,
)


_STORE: DataStore | None = None


def _worker_init() -> None:
    global _STORE
    _STORE = DataStore.load()


def _is_unresolved(event: CombatEvent) -> bool:
    return str(event.skill_id or "").strip() == "-1" or str(
        event.skill_name or ""
    ).strip() == "待识别伤害"


def _analyze(path_text: str, root_text: str) -> dict[str, Any]:
    started = time.perf_counter()
    path = Path(path_text)
    capture_root = Path(root_text)
    store = _STORE or DataStore.load()
    source = V576DiagnosticReplaySource(path, DATA_DIR)
    try:
        decoded = source.poll()
    finally:
        source.close()
    casts = tuple(row for row in decoded if isinstance(row, SkillCastEvent))
    hits = tuple(
        row
        for row in decoded
        if isinstance(row, CombatEvent) and not row.healing and row.amount > 0
    )
    strict = SkillAttributionLedger(store, mode="evidence_v2").reconcile(
        hits, casts
    )
    outgoing = tuple(
        row
        for row in strict.events
        if not row.healing
        and row.source_id != "__incoming_damage__"
        and row.amount > 0
    )
    real_hits = tuple(
        row for row in outgoing if row.has_real_hit_evidence is True
    )
    unresolved = tuple(row for row in outgoing if _is_unresolved(row))
    unresolved_real = tuple(row for row in real_hits if _is_unresolved(row))
    special = tuple(
        row for row in outgoing if str(row.skill_id) == "733042"
    )
    basic = tuple(
        row
        for row in outgoing
        if str(row.skill_id).casefold() in {"4294967295", "0xffffffff"}
        or str(row.skill_name).strip() in {
            "普通/特殊攻击",
            "弩系普通攻击",
            "卡莉扇刃普通攻击",
        }
    )
    jobs = sorted(
        {
            str(row.job or "未知职业").strip() or "未知职业"
            for row in outgoing
            if row.source_id != "__incoming_damage__"
        }
    )
    return {
        "capture": str(path.relative_to(capture_root)),
        "size_bytes": path.stat().st_size,
        "jobs": "、".join(jobs),
        "decoded_casts": len(casts),
        "outgoing_damage_records": len(outgoing),
        "real_hits": len(real_hits),
        "unresolved_damage_records": len(unresolved),
        "unresolved_real_hits": len(unresolved_real),
        "unresolved_damage": sum(row.amount for row in unresolved),
        "dungeon_special_733042_hits": len(special),
        "dungeon_special_733042_damage": sum(row.amount for row in special),
        "proven_basic_attack_hits": len(basic),
        "unresolved_real_hit_rate_percent": round(
            len(unresolved_real) / max(1, len(real_hits)) * 100.0, 4
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay dpscap files through ACT evidence_v2 and count unresolved damage."
    )
    parser.add_argument("capture_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    capture_root = args.capture_root.resolve()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(capture_root.rglob("*.dpscap"), key=lambda row: str(row))
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        raise SystemExit(f"no .dpscap files under {capture_root}")

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    workers = max(1, int(args.workers))
    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as pool:
        pending = {
            pool.submit(_analyze, str(path), str(capture_root)): path
            for path in paths
        }
        for index, future in enumerate(as_completed(pending), 1):
            path = pending[future]
            try:
                row = future.result()
                rows.append(row)
                print(
                    f"[{index}/{len(paths)}] {row['capture']} "
                    f"unknown={row['unresolved_real_hits']}/{row['real_hits']} "
                    f"special733042={row['dungeon_special_733042_hits']} "
                    f"{row['elapsed_seconds']}s",
                    flush=True,
                )
            except Exception as exc:  # pragma: no cover - audit diagnostics
                errors.append({"capture": path.name, "error": repr(exc)})
                print(f"[{index}/{len(paths)}] ERROR {path.name}: {exc!r}", flush=True)

    rows.sort(key=lambda row: str(row["capture"]))
    total_real = sum(int(row["real_hits"]) for row in rows)
    total_unknown = sum(int(row["unresolved_real_hits"]) for row in rows)
    summary = {
        "schema": "dragon-nest-v2-unresolved-capture-audit-v1",
        "capture_root": str(capture_root),
        "captures_requested": len(paths),
        "captures_completed": len(rows),
        "captures_failed": len(errors),
        "total_real_hits": total_real,
        "total_unresolved_real_hits": total_unknown,
        "total_unresolved_real_hit_rate_percent": round(
            total_unknown / max(1, total_real) * 100.0, 4
        ),
        "total_dungeon_special_733042_hits": sum(
            int(row["dungeon_special_733042_hits"]) for row in rows
        ),
        "total_proven_basic_attack_hits": sum(
            int(row["proven_basic_attack_hits"]) for row in rows
        ),
        "rows": rows,
        "errors": errors,
    }
    json_path = output_dir / "v2_unresolved_by_capture.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    csv_path = output_dir / "v2_unresolved_by_capture.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"done captures={len(rows)} unknown={total_unknown}/{total_real} "
        f"json={json_path} csv={csv_path}",
        flush=True,
    )
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
