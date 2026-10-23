"""Compare legacy tooltip calibration with the recovered client route.

Legacy observations are read from an explicitly supplied catalog and are
never imported by the runtime decoder or catalog generator.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURRENT = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "inspect_item_catalog_v576.json.gz"
)


def load_catalog(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "legacy_catalog",
        type=Path,
        help="older catalog containing engraving_template_record_extras",
    )
    parser.add_argument("--current-catalog", type=Path, default=DEFAULT_CURRENT)
    args = parser.parse_args()

    legacy = load_catalog(args.legacy_catalog.resolve())
    current = load_catalog(args.current_catalog.resolve())
    observations = legacy.get("engraving_template_record_extras", {})
    items = current.get("items", {})
    potences = current.get("engraving_potences", {})
    counts: dict[str, int] = {}

    for raw_key, observed in observations.items():
        template_text, selector_text, auxiliary_text = raw_key.split(":", 2)
        item = items.get(template_text)
        potence_id = int(item[8]) if isinstance(item, list) and len(item) > 8 else 0
        client_key = f"{potence_id}:{selector_text}"
        client_known = client_key in potences
        client_value = potences.get(client_key)
        if not client_known:
            status = "missing"
        elif observed == client_value:
            status = "same"
        elif client_value is None:
            status = "client-empty"
        elif observed is None:
            status = "legacy-empty"
        else:
            status = "different"
        counts[status] = counts.get(status, 0) + 1
        print(
            f"{status}\ttemplate={template_text}\tpotence={potence_id}\t"
            f"selector={selector_text},{auxiliary_text}\t"
            f"legacy={observed!r}\tclient={client_value!r}"
        )

    print("TOTAL " + " ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    return 1 if counts.get("missing", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
