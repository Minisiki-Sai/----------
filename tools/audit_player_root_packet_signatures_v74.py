from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_player_object_creation_v53 import (  # noqa: E402
    OBJECT_ID_MAX,
    PLAYER_ID_MIN,
    _parse_a90370,
)


DEFAULT_MANIFEST = (
    ROOT
    / "outputs"
    / "direct_damage_v73_fixed35_20260909"
    / "profession_direct_damage_report.json"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "player_root_packet_signatures_v74_20260910"


def _scan(path_text: str) -> dict[str, object]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    signatures: Counter[str] = Counter()
    layouts: Counter[str] = Counter()
    configs: Counter[str] = Counter()
    packet_count = 0
    record_count = 0
    owners: set[int] = set()

    def feed(packet):
        nonlocal packet_count, record_count
        if packet.transport.upper() == "TCP":
            return []
        inventory = inspect_udp_damage_records(
            packet.payload, packet.inferred_direction()
        )
        if not inventory.complete:
            return []
        matched_packet = False
        for typed in inventory.typed_records:
            if int(typed.record_type) not in (9, 56):
                continue
            record = bytes(typed.record_bytes)
            try:
                parsed = _parse_a90370(record)
            except ValueError:
                continue
            owner = int.from_bytes(record[:4], "little")
            if (
                owner < PLAYER_ID_MIN
                or not 0 < int(parsed.object_id) <= OBJECT_ID_MAX
                or int(parsed.reference_branch_i8) != 0
                or int(parsed.reference_id) != owner
            ):
                continue
            matched_packet = True
            record_count += 1
            owners.add(owner)
            declared = int.from_bytes(record[5:7], "little")
            layouts[f"kind={int(typed.record_type)}|declared={declared}"] += 1
            configs[str(int(parsed.config_row or 0))] += 1
        if matched_packet:
            packet_count += 1
            signatures[
                f"len={len(packet.payload)}|sig={packet.payload[5:9].hex()}|"
                f"direction={packet.inferred_direction()}"
            ] += 1
        return []

    source._decoder.feed_events = feed
    try:
        source.poll()
    finally:
        source.close()
    return {
        "capture": path.name,
        "path": str(path),
        "packets": packet_count,
        "records": record_count,
        "owners": len(owners),
        "signatures": dict(signatures),
        "record_layouts": dict(layouts),
        "config_rows": dict(configs),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    paths = [str(Path(row["capture_path"])) for row in manifest["capture_manifest"]]
    rows = []
    with ProcessPoolExecutor(max_workers=max(1, min(4, args.workers))) as pool:
        futures = {pool.submit(_scan, path): path for path in paths}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(f"[{len(rows)}/{len(paths)}] {row['capture']}", flush=True)
    rows.sort(key=lambda row: str(row["path"]))
    signatures: Counter[str] = Counter()
    layouts: Counter[str] = Counter()
    configs: Counter[str] = Counter()
    signature_captures: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        signatures.update(row["signatures"])
        layouts.update(row["record_layouts"])
        configs.update(row["config_rows"])
        for key in row["signatures"]:
            signature_captures[key].add(str(row["capture"]))
    result = {
        "schema": "player-root-packet-signatures-v74",
        "captures": rows,
        "aggregate": {
            "packets": sum(int(row["packets"]) for row in rows),
            "records": sum(int(row["records"]) for row in rows),
            "signatures": [
                {
                    "key": key,
                    "packets": count,
                    "captures": len(signature_captures[key]),
                }
                for key, count in signatures.most_common()
            ],
            "record_layouts": dict(layouts.most_common()),
            "config_rows": dict(configs.most_common()),
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "REPORT.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 玩家根伤害对象包签名审计 v74",
        "",
        f"- 固定抓包：{len(rows)}",
        f"- 根对象包：{result['aggregate']['packets']:,}",
        f"- 根对象记录：{result['aggregate']['records']:,}",
        "",
        "|外层签名|包数|抓包数|",
        "|---|---:|---:|",
    ]
    for row in result["aggregate"]["signatures"]:
        lines.append(f"|`{row['key']}`|{row['packets']:,}|{row['captures']}|")
    (args.output / "REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
