from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


def _audit_capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    ledger = source._decoder.damage_object_origins
    rows: Counter[tuple[int, int, bool, bool, bool]] = Counter()
    original = ledger.attribute_direct37

    def attribute_direct37(**kwargs):
        result = original(**kwargs)
        if result is not None:
            rows[
                (
                    int(result.projectile_template_id),
                    int(result.skill_id),
                    bool(result.action_backed),
                    bool(result.packet_validated),
                    bool(result.client_act_validated),
                )
            ] += 1
        return result

    ledger.attribute_direct37 = attribute_direct37
    try:
        source.poll()
    finally:
        source.close()
    return {
        "capture": str(path),
        "rows": [
            {
                "projectile_template_id": key[0],
                "skill_id": key[1],
                "action_backed": key[2],
                "packet_validated": key[3],
                "client_act_validated": key[4],
                "hits": count,
            }
            for key, count in rows.items()
        ],
    }


def _captures(inputs: list[Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for item in inputs:
        if item.is_file() and item.suffix.casefold() == ".dpscap":
            found[str(item.resolve()).casefold()] = item.resolve()
        elif item.is_dir():
            for path in item.rglob("*.dpscap"):
                found[str(path.resolve()).casefold()] = path.resolve()
    return sorted(found.values(), key=lambda value: str(value).casefold())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit exact owner+damage_object_id links and measure whether the "
            "registration is also backed by a same-owner skill action."
        )
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    captures = _captures(args.inputs)
    if not captures:
        raise SystemExit("no .dpscap captures found")

    combined: Counter[tuple[int, int, bool, bool, bool]] = Counter()
    capture_sets: dict[int, set[str]] = defaultdict(set)
    failures: list[dict[str, str]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(_audit_capture, str(path)): path for path in captures
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                report = future.result()
            except Exception as exc:  # pragma: no cover - diagnostic boundary
                failures.append({"capture": str(path), "error": repr(exc)})
                continue
            for row in report["rows"]:
                key = (
                    int(row["projectile_template_id"]),
                    int(row["skill_id"]),
                    bool(row["action_backed"]),
                    bool(row["packet_validated"]),
                    bool(row["client_act_validated"]),
                )
                combined[key] += int(row["hits"])
                capture_sets[key[0]].add(str(path))

    by_template: dict[int, Counter[str]] = defaultdict(Counter)
    skill_sets: dict[int, set[int]] = defaultdict(set)
    for key, hits in combined.items():
        template_id, skill_id, action_backed, packet_validated, client_validated = key
        bucket = by_template[template_id]
        bucket["hits"] += hits
        bucket["action_backed_hits" if action_backed else "unbacked_hits"] += hits
        bucket["already_deterministic_hits" if (packet_validated or client_validated) else "provisional_hits"] += hits
        skill_sets[template_id].add(skill_id)

    rows = []
    for template_id, counts in by_template.items():
        spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template_id)
        total = int(counts["hits"])
        backed = int(counts["action_backed_hits"])
        rows.append(
            {
                "projectile_template_id": template_id,
                "spec_key": spec.key if spec is not None else "",
                "canonical_skill_id": (
                    int(spec.canonical_skill_id) if spec is not None else 0
                ),
                "observed_skill_ids": sorted(skill_sets[template_id]),
                "hits": total,
                "action_backed_hits": backed,
                "action_backed_percent": round(backed / max(1, total) * 100.0, 6),
                "provisional_hits": int(counts["provisional_hits"]),
                "already_deterministic_hits": int(
                    counts["already_deterministic_hits"]
                ),
                "captures": len(capture_sets[template_id]),
                "packet_validated": (
                    template_id
                    in PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
                ),
                "client_act_validated": (
                    template_id
                    in CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
                ),
            }
        )
    rows.sort(key=lambda row: (-row["provisional_hits"], -row["hits"]))
    result = {
        "schema_version": 1,
        "captures": len(captures),
        "failures": failures,
        "definition": (
            "action_backed means the exact owner+damage_object_id generation "
            "was registered within 2.5 seconds of a same-owner action from "
            "the configured canonical skill family"
        ),
        "rows": rows,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
