"""Generate fail-closed player-root action/signal attribution specs.

The input audit has already resolved an A90370 record's signed action and
signal fields against the merged client ACT manifest.  A row is emitted only
when all of the following are true:

* the resolved signal is type 15;
* signal payload[0:4] exactly repeats the wire config row;
* the resolved ACT action belongs to one canonical skill for that job.

The generated runtime table contains no timing information.  It is an exact
``job + action index + signal index + config row`` lookup and therefore fails
closed on another client resource revision or an unseen action branch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT
    / "outputs/merged_player_action_indices_v101_20260910/"
    "merged_player_action_indices_v99.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "dragon_nest_builder/live_dps/"
    "generated_player_root_action_signal_specs_v101.py"
)


def build(input_path: Path) -> tuple[dict[tuple[str, int, int, int], int], str]:
    raw = input_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    result: dict[tuple[str, int, int, int], int] = {}
    for row in payload.get("rows", ()):
        if not row.get("exact_spawn"):
            continue
        if int(row.get("signal_type", -1)) != 15:
            continue
        if row.get("payload_matches_config") is not True:
            continue
        skill_id = int(row.get("candidate_skill_id", 0) or 0)
        config_row = row.get("config_row")
        if skill_id <= 0 or config_row is None:
            continue
        key = (
            str(row["job"]),
            int(row["action_index_i16"]),
            int(row["signal_key_i8"]),
            int(config_row),
        )
        previous = result.get(key)
        if previous not in (None, skill_id):
            raise ValueError(f"conflicting action/signal spec {key}: {previous}/{skill_id}")
        result[key] = skill_id
    return result, hashlib.sha256(raw).hexdigest()


def write_module(
    rows: dict[tuple[str, int, int, int], int],
    input_sha256: str,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '"""Generated exact A90370 action/signal rules; do not edit by hand."""',
        "from types import MappingProxyType",
        "",
        f'INPUT_SHA256 = "{input_sha256}"',
        "PLAYER_ROOT_ACTION_SIGNAL_SKILLS = MappingProxyType({",
    ]
    for key, skill_id in sorted(rows.items()):
        lines.append(f"    {key!r}: {skill_id},")
    lines.extend(("})", ""))
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows, digest = build(args.input)
    if not rows:
        raise SystemExit("no admissible action/signal rows")
    write_module(rows, digest, args.output)
    print(json.dumps({"rows": len(rows), "input_sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
