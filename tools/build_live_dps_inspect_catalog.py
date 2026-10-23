from __future__ import annotations

import argparse
import gzip
import json
import re
import struct
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "inspect_item_catalog_v576.json.gz"
)
WANTED_FIELDS = {
    "_NameID",
    "_DescriptionID",
    "_NameIDParam",
    "_DescriptionIDParam",
    "_LevelLimit",
    "_NeedJobClass",
    "_Rank",
    "_EnchantID",
    "_EnchantLevel",
    "_PotentialID",
    "_PotentialNo",
    "_PotentialRatio",
    "_PrefixNameID",
    "_ApplicableValue",
    "_SkillIndex",
    "_SkillLevel",
    "_SkillExplanationIDParam",
    "_StaticName",
    "_TreeSlotIndex",
    "_StateType",
    "_IsHide",
    "_TypeParam2",
    "_TypeParam1",
    "_TypeParam3",
    "_potenceID",
    "_potenceNo",
    "_gradeicon",
    "_state",
    "_state_min",
    "_state_max",
}

# Talisman DNT rows use the legacy talisman-state order for the five crowd
# control values rather than the generic item-state order used elsewhere.
# Normalize them here so both the main stat and the B-grade extra stat are
# presented with the same labels as the in-game tooltip.
_TALISMAN_STATE_REMAP = {
    10: 11,  # 束缚：硬直
    11: 15,  # 不屈：硬直抵抗
    13: 14,  # 幸运：致命一击抵抗
    14: 10,  # 冲击：眩晕
    15: 13,  # 意志：眩晕抵抗
}
for _state_index in range(1, 17):
    WANTED_FIELDS.update(
        {
            f"_State{_state_index}",
            f"_State{_state_index}_Min",
            f"_State{_state_index}_Max",
            f"_State{_state_index}Value",
        }
    )


def parse_dnt(path: Path):
    data = path.read_bytes()
    offset = 4
    column_count = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    row_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    fields: list[tuple[str, int]] = []
    for _ in range(column_count):
        name_size = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        name = data[offset : offset + name_size].decode("ascii")
        offset += name_size
        field_type = data[offset]
        offset += 1
        fields.append((name, field_type))

    for _ in range(row_count):
        row = {"RowId": struct.unpack_from("<I", data, offset)[0]}
        offset += 4
        for name, field_type in fields:
            if field_type == 1:
                value_size = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                raw = data[offset : offset + value_size]
                offset += value_size
                value = raw.decode("utf-8", errors="replace") if name in WANTED_FIELDS else None
            elif field_type in (2, 3, 4):
                value = struct.unpack_from("<i", data, offset)[0] if name in WANTED_FIELDS else None
                offset += 4
            elif field_type == 5:
                value = struct.unpack_from("<f", data, offset)[0] if name in WANTED_FIELDS else None
                offset += 4
            else:
                raise ValueError(f"Unknown DNT field type {field_type}: {path.name}/{name}")
            if name in WANTED_FIELDS:
                row[name] = value
        yield row
    if offset != len(data):
        raise ValueError(f"DNT size mismatch: {path} ({offset} != {len(data)})")


def load_ui_strings(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        int(message_id): body.replace("\\n", "\n")
        for message_id, body in re.findall(
            r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>',
            text,
            re.S,
        )
    }


def clean_text(value: str) -> str:
    value = value.replace("<br/>", "；").replace("<br>", "；")
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"#[A-Za-z]", "", value)
    value = value.replace("\r", " ").replace("\n", "；")
    value = re.sub(r"\s+", " ", value).strip(" ；")
    return re.sub(r"；{2,}", "；", value)


def latest_table_paths(extract_root: Path, pattern: str) -> list[Path]:
    """Keep only the newest copy of each same-named unpacked table.

    Update archives can contain several ResourceXX trees.  Concatenating two
    copies of a potential table shifts the one-based item-instance selector,
    so the selected enchant/glyph option resolves to a different row.
    """
    selected: dict[str, Path] = {}
    for path in extract_root.rglob(pattern):
        # Resource patch trees also contain zero-byte placeholders for tables
        # owned by another archive.  They are not a newer table revision.
        if path.stat().st_size < 10:
            continue
        key = path.name.casefold()
        current = selected.get(key)
        if current is None or (
            path.stat().st_mtime_ns,
            path.stat().st_size,
            str(path).casefold(),
        ) > (
            current.stat().st_mtime_ns,
            current.stat().st_size,
            str(current).casefold(),
        ):
            selected[key] = path
    return sorted(selected.values(), key=lambda value: value.name.casefold())


def render_text(row: dict, prefix: str, ui: dict[int, str]) -> str:
    try:
        template = ui.get(int(row.get(f"{prefix}ID") or 0), "")
    except (TypeError, ValueError):
        template = ""
    raw_params = [
        part.strip()
        for part in str(row.get(f"{prefix}IDParam") or "").split(",")
        if part.strip()
    ]
    for index, raw_value in enumerate(raw_params):
        resolved = raw_value
        reference = re.fullmatch(r"\{(\d+)\}", raw_value)
        if reference:
            resolved = ui.get(int(reference.group(1)), raw_value)
        elif prefix == "_Name" and raw_value.isdigit():
            resolved = ui.get(int(raw_value), raw_value)
        template = template.replace("{" + str(index) + "}", resolved)
    return clean_text(template)


def table_category(path: Path) -> tuple[str, int]:
    stem = path.stem.casefold()
    if stem == "itemtable_talisman":
        return "t", 130
    if stem == "itemtable_glyph":
        return "g", 130
    if stem == "itemtable_rune":
        return "r", 130
    if stem in {"itemtable_imprintequip", "itemtable_imprinting"}:
        return "i", 130
    if stem == "itemtable_equipment":
        return "e", 120
    if stem == "itemtable":
        return "e", 110
    return "e", 80


def parse_job_codes(value: object) -> list[int]:
    return sorted(
        {
            int(raw_value)
            for raw_value in re.findall(r"\d+", str(value or ""))
            if int(raw_value) > 0
        }
    )


def static_attributes(row: dict) -> list[list[int | float]]:
    attributes: list[list[int | float]] = []
    for index in range(1, 11):
        state_name = f"_State{index}"
        if state_name not in row:
            continue
        try:
            state_id = int(row.get(state_name) or 0)
            minimum = float(row.get(f"{state_name}_Min") or 0.0)
            maximum = float(row.get(f"{state_name}_Max") or 0.0)
        except (TypeError, ValueError):
            continue
        if abs(minimum) < 1e-7 and abs(maximum) < 1e-7:
            continue
        attributes.append([state_id, minimum, maximum])
    return attributes


def enchant_attributes(row: dict) -> list[list[int | float]]:
    attributes: list[list[int | float]] = []
    for index in range(1, 11):
        state_name = f"_State{index}"
        if state_name not in row:
            continue
        try:
            state_id = int(row.get(state_name) or 0)
            value = float(row.get(f"{state_name}Value") or 0.0)
        except (TypeError, ValueError):
            continue
        if abs(value) < 1e-7:
            continue
        attributes.append([state_id, value])
    return attributes


def potential_attributes(row: dict) -> list[list[int | float]]:
    attributes: list[list[int | float]] = []
    for index in range(1, 17):
        state_name = f"_State{index}"
        try:
            raw_state_id = row.get(state_name)
            state_id = (
                -1
                if raw_state_id is None or raw_state_id == ""
                else int(raw_state_id)
            )
            value = float(row.get(f"{state_name}Value") or 0.0)
        except (TypeError, ValueError):
            continue
        if state_id < 0 or abs(value) < 1e-7:
            continue
        attributes.append([state_id, value])
    return attributes


def talisman_attributes(
    attributes: list[list[int | float]],
) -> list[list[int | float]]:
    return [
        [_TALISMAN_STATE_REMAP.get(int(row[0]), int(row[0])), *row[1:]]
        for row in attributes
    ]


def build_catalog(extract_root: Path) -> dict:
    ui_candidates = [
        path
        for path in extract_root.rglob("uistring*.xml")
        if "server" not in path.stem.casefold()
    ]
    if not ui_candidates:
        raise FileNotFoundError(f"uistring*.xml not found below {extract_root}")
    # Patch archives contain zero-byte placeholder copies in later ResourceXX
    # directories.  Choosing only by mtime selected an empty placeholder and
    # silently dropped every item name, including the new talisman table.
    ui = load_ui_strings(
        max(
            ui_candidates,
            key=lambda path: (path.stat().st_size, path.stat().st_mtime_ns),
        )
    )

    items: dict[int, tuple[int, list[object]]] = {}
    used_enchant_ids: set[int] = set()
    for path in latest_table_paths(extract_root, "itemtable*.dnt"):
        category, priority = table_category(path)
        for row in parse_dnt(path):
            name = render_text(row, "_Name", ui)
            if not name:
                continue
            template_id = int(row["RowId"])
            description = render_text(row, "_Description", ui)
            level = max(0, int(row.get("_LevelLimit") or 0))
            job_codes = parse_job_codes(row.get("_NeedJobClass"))
            attributes = static_attributes(row)
            enchant_id = max(0, int(row.get("_EnchantID") or 0))
            if category == "t":
                attributes = talisman_attributes(attributes)
                potential_id = max(0, int(row.get("_TypeParam1") or 0))
                glyph_potential_id = 0
                type_param3_id = 0
            else:
                potential_id = max(0, int(row.get("_TypeParam2") or 0))
                glyph_potential_id = max(0, int(row.get("_TypeParam1") or 0))
                type_param3_id = max(0, int(row.get("_TypeParam3") or 0))
            if enchant_id:
                used_enchant_ids.add(enchant_id)
            existing = items.get(template_id)
            if existing is not None and existing[0] > priority:
                continue
            items[template_id] = (
                priority,
                [
                    name,
                    category,
                    level,
                    description,
                    job_codes,
                    attributes,
                    enchant_id,
                    potential_id,
                    glyph_potential_id,
                    type_param3_id,
                ],
            )

    enchants: dict[str, list[list[int | float]]] = {}
    for path in latest_table_paths(extract_root, "enchanttable.dnt"):
        for row in parse_dnt(path):
            try:
                enchant_id = int(row.get("_EnchantID") or 0)
                enchant_level = int(row.get("_EnchantLevel") or 0)
            except (TypeError, ValueError):
                continue
            if enchant_id not in used_enchant_ids or enchant_level <= 0:
                continue
            attributes = enchant_attributes(row)
            if attributes:
                enchants[f"{enchant_id}:{enchant_level}"] = attributes

    # The client table manager returns matching rows in DNT physical order.
    # RowId sorting changes the one-based instance selector for interleaved
    # groups, most visibly glyph and talisman options.
    potential_rows: dict[int, list[dict]] = {}
    for path in latest_table_paths(extract_root, "potentialtable_potentialjewel.dnt"):
        for row in parse_dnt(path):
            try:
                potential_id = int(row.get("_PotentialID") or 0)
            except (TypeError, ValueError):
                continue
            if potential_id <= 0:
                continue
            potential_rows.setdefault(potential_id, []).append(row)

    potentials: dict[str, list[list[int | float]]] = {}
    for potential_id, rows in potential_rows.items():
        for option_index, row in enumerate(rows, start=1):
            attributes = potential_attributes(row)
            # Empty rows are authoritative "无" choices, not missing data.
            potentials[f"{potential_id}:{option_index}"] = attributes

    used_talisman_potential_ids = {
        int(values[7])
        for _priority, values in items.values()
        if values[1] == "t" and int(values[7]) > 0
    }
    talisman_potential_rows: dict[int, list[dict]] = {}
    for path in latest_table_paths(extract_root, "potentialtable_talismanitem.dnt"):
        for row in parse_dnt(path):
            try:
                potential_id = int(row.get("_PotentialID") or 0)
            except (TypeError, ValueError):
                continue
            if potential_id not in used_talisman_potential_ids:
                continue
            talisman_potential_rows.setdefault(potential_id, []).append(row)
    for potential_id, rows in talisman_potential_rows.items():
        for option_index, row in enumerate(rows, start=1):
            # Empty rows are real "无额外属性" selectors, so retain them to
            # preserve the instance packet's one-based option numbering.
            potentials[f"{potential_id}:{option_index}"] = talisman_attributes(
                potential_attributes(row)
            )

    # Generic item TypeParam1 and TypeParam3 both use potentialtable vectors.
    # Their instance selectors differ, but their table lookup is identical.
    used_type_param1_ids = {
        int(values[8])
        for _priority, values in items.values()
        if values[1] == "e"
        and int(values[8]) > 0
    }
    used_type_param3_ids = {
        int(values[9])
        for _priority, values in items.values()
        if values[1] == "e" and len(values) > 9 and int(values[9]) > 0
    }
    generic_potential_rows: dict[int, list[dict]] = {}
    for path in latest_table_paths(extract_root, "potentialtable.dnt"):
        for row in parse_dnt(path):
            try:
                potential_id = int(row.get("_PotentialID") or 0)
            except (TypeError, ValueError):
                continue
            if potential_id not in used_type_param1_ids | used_type_param3_ids:
                continue
            generic_potential_rows.setdefault(potential_id, []).append(row)

    inherent_potentials: dict[str, list[list[int | float]]] = {}
    suffix_potentials: dict[str, list[object]] = {}
    for potential_id, rows in generic_potential_rows.items():
        for option_index, row in enumerate(rows, start=1):
            key = f"{potential_id}:{option_index}"
            attributes = potential_attributes(row)
            if potential_id in used_type_param1_ids:
                inherent_potentials[key] = attributes
            if potential_id in used_type_param3_ids:
                prefix_name = clean_text(
                    ui.get(int(row.get("_PrefixNameID") or 0), "")
                )
                suffix_potentials[key] = [prefix_name, attributes]

    glyph_potential_rows: dict[int, list[dict]] = {}
    for path in latest_table_paths(extract_root, "potentialtable_glyph.dnt"):
        for row in parse_dnt(path):
            try:
                potential_id = int(row.get("_PotentialID") or 0)
            except (TypeError, ValueError):
                continue
            if potential_id > 0:
                glyph_potential_rows.setdefault(potential_id, []).append(row)

    glyph_potentials: dict[str, list[list[int | float]]] = {}
    for potential_id, rows in glyph_potential_rows.items():
        for option_index, row in enumerate(rows, start=1):
            # Keep empty rows as well.  They are a decoded, explicit
            # "no extra attribute" option and must not be confused with an
            # unknown selector that is absent from the DNT.
            glyph_potentials[f"{potential_id}:{option_index}"] = (
                potential_attributes(row)
            )

    engraving_extra_rows: dict[int, list[tuple[int, float, float]]] = {}
    for path in latest_table_paths(extract_root, "imprintinggradetable.dnt"):
        for row in parse_dnt(path):
            try:
                state_id = int(row.get("_state") or -1)
                minimum = float(row.get("_state_min") or 0.0)
                maximum = float(row.get("_state_max") or 0.0)
                grade_icon = int(row.get("_gradeicon") or 0)
            except (TypeError, ValueError):
                continue
            if state_id < 0 or (abs(minimum) < 1e-7 and abs(maximum) < 1e-7):
                continue
            engraving_extra_rows.setdefault(state_id, []).append(
                (grade_icon, minimum, maximum)
            )

    # CDnImprintEquip::BuildState (current client RVA 0x464160) resolves the
    # fixed option in four exact steps:
    #
    #   itemtable_imprintequip._TypeParam1 -> potencetable._potenceID
    #   -> one-based instance byte at record[29] -> _StateN/_StateNValue
    #
    # The renderer then calls the helper at RVA 0x441130 with the resolved
    # state/value pair.  That helper performs an inclusive
    # imprintinggradetable state_min/state_max lookup and returns _gradeicon.
    # record[30] is a generic item-instance byte and is not part of this path.
    # Keeping this table keyed by potence ID exactly mirrors the client and
    # removes the old per-template capture routes.
    potence_rows: dict[int, list[dict]] = {}
    for path in latest_table_paths(extract_root, "potencetable.dnt"):
        for row in parse_dnt(path):
            try:
                potence_id = int(row.get("_potenceID") or 0)
            except (TypeError, ValueError):
                continue
            if potence_id > 0:
                potence_rows.setdefault(potence_id, []).append(row)

    engraving_potences: dict[str, list[int | float] | None] = {}
    for potence_id, rows in potence_rows.items():
        for option_index, row in enumerate(rows, start=1):
            attributes = potential_attributes(row)
            key = f"{potence_id}:{option_index}"
            if not attributes:
                engraving_potences[key] = None
                continue
            # Current potencetable contains at most one fixed state per row.
            # Fail loudly if that invariant changes instead of silently
            # dropping a client-visible property.
            if len(attributes) != 1:
                raise ValueError(
                    "engraving potence row contains multiple states: "
                    f"potence={potence_id}, selector={option_index}"
                )
            state_id, value = attributes[0]
            grade_icon = -1
            for candidate, minimum, maximum in engraving_extra_rows.get(
                int(state_id), ()
            ):
                if minimum <= float(value) <= maximum:
                    grade_icon = candidate
                    break
            engraving_potences[key] = [
                int(state_id), float(value), float(value), grade_icon,
            ]

    # S2C 0D 19 stores only non-zero card-mastery rows as
    # ``(cardskilltable.RowId, level)`` pairs.  Package the client table's
    # display name and tree order so runtime decoding never needs a
    # character-specific payload fingerprint.
    card_masteries: dict[str, list[object]] = {}
    for path in latest_table_paths(extract_root, "cardskilltable.dnt"):
        for row in parse_dnt(path):
            try:
                mastery_id = int(row["RowId"])
                tree_slot = int(row.get("_TreeSlotIndex") or 0)
                state_type = int(row.get("_StateType") or 0)
                hidden = bool(int(row.get("_IsHide") or 0))
            except (TypeError, ValueError):
                continue
            name = render_text(row, "_Name", ui).removesuffix("掌握").strip()
            if mastery_id > 0 and name:
                card_masteries[str(mastery_id)] = [
                    name,
                    tree_slot,
                    state_type,
                    hidden,
                ]

    # Core-rune instance bytes select a conversion/ratio row and a
    # coefficient level.  The actual values live in
    # skillleveltable_corerune; copying those rows avoids the former linear
    # formula and also preserves conversion-specific coefficient ranges.
    conversion_names: dict[int, str] = {}
    for path in latest_table_paths(extract_root, "skilltable_corerune.dnt"):
        for row in parse_dnt(path):
            try:
                skill_index = int(row["RowId"])
            except (TypeError, ValueError):
                continue
            group = (skill_index - 6_540_000) // 100
            if not 0 <= group < 12 or group in conversion_names:
                continue
            raw_name = str(row.get("_StaticName") or "").strip()
            source, separator, target = raw_name.partition(",")
            if not separator:
                continue
            conversion_names[group] = (
                f"{source.strip()}→{target.strip()}"
                .replace("暴击", "致命")
                .replace("物防", "防御")
            )

    core_runes: dict[str, list[object]] = {}
    for path in latest_table_paths(extract_root, "skillleveltable_corerune.dnt"):
        for row in parse_dnt(path):
            try:
                skill_index = int(row.get("_SkillIndex") or 0)
                coefficient_rank = int(row.get("_SkillLevel") or 0)
            except (TypeError, ValueError):
                continue
            group = (skill_index - 6_540_000) // 100
            ratio_rank = skill_index % 100
            if not (0 <= group < 24 and 1 <= ratio_rank <= 21):
                continue
            if not 1 <= coefficient_rank <= 21:
                continue
            conversion = conversion_names.get(group % 12, "")
            params = [
                value.strip()
                for value in str(
                    row.get("_SkillExplanationIDParam") or ""
                ).split(",")
            ]
            if not conversion or len(params) < 5:
                continue
            try:
                ratio = float(params[2])
                coefficient = float(params[3])
                maximum = float(params[4])
            except (TypeError, ValueError):
                continue
            item_level = 60 if group < 12 else 70
            param_id = (group % 12 + 1) * 100 + ratio_rank
            core_runes[f"{item_level}:{param_id}:{coefficient_rank}"] = [
                conversion,
                ratio,
                coefficient,
                maximum,
            ]

    return {
        "format": 14,
        "version": "v576-client-imprint-buildstate",
        "items": {
            str(template_id): values
            for template_id, (_priority, values) in sorted(items.items())
        },
        "enchants": enchants,
        "potentials": potentials,
        "inherent_potentials": inherent_potentials,
        "suffix_potentials": suffix_potentials,
        "glyph_potentials": glyph_potentials,
        "engraving_potences": engraving_potences,
        "card_masteries": card_masteries,
        "core_runes": core_runes,
    }


def write_catalog(payload: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    with output.open("wb") as raw_stream:
        with gzip.GzipFile(fileobj=raw_stream, mode="wb", mtime=0) as stream:
            stream.write(encoded)


def merge_talisman_items(existing_path: Path, generated: dict) -> dict:
    """Merge current instance tables without dropping historical templates.

    Patch-only resource trees do not contain every historical item table.  A
    full rebuild from one of those trees would therefore remove otherwise
    valid equipment mappings.  Talisman templates are self-identifying by the
    dedicated ``t`` category, so their narrow replacement is deterministic.
    Client-global instance tables are replaced as a whole; obsolete manual
    engraving route fields are removed during the format migration.
    """
    with gzip.open(existing_path, "rt", encoding="utf-8") as stream:
        existing = json.load(stream)
    items = existing.setdefault("items", {})
    talismans = {
        template_id: values
        for template_id, values in generated.get("items", {}).items()
        if isinstance(values, list) and len(values) > 1 and values[1] == "t"
    }
    if not talismans:
        raise ValueError("generated catalog contains no talisman templates")
    items.update(talismans)
    talisman_potential_ids = {
        int(values[7])
        for values in talismans.values()
        if len(values) > 7 and int(values[7]) > 0
    }
    potentials = existing.setdefault("potentials", {})
    for key in tuple(potentials):
        try:
            potential_id = int(str(key).partition(":")[0])
        except ValueError:
            continue
        if potential_id in talisman_potential_ids:
            potentials.pop(key, None)
    for key, values in generated.get("potentials", {}).items():
        try:
            potential_id = int(str(key).partition(":")[0])
        except ValueError:
            continue
        if potential_id in talisman_potential_ids:
            potentials[key] = values
    existing["format"] = generated["format"]
    existing["version"] = f"{generated['version']}+talisman-inspect"
    existing["engraving_potences"] = generated["engraving_potences"]
    for obsolete_key in (
        "engraving_extras",
        "engraving_record_extras",
        "engraving_template_record_extras",
    ):
        existing.pop(obsolete_key, None)
    return existing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the compact v576 character-inspect item catalog."
    )
    parser.add_argument("extract_root", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--merge-existing",
        type=Path,
        help="Preserve an audited catalog and replace only talisman templates.",
    )
    args = parser.parse_args()
    payload = build_catalog(args.extract_root.resolve())
    if args.merge_existing is not None:
        payload = merge_talisman_items(args.merge_existing.resolve(), payload)
    write_catalog(payload, args.output.resolve())
    print(
        f"Wrote {len(payload['items'])} items and "
        f"{len(payload.get('enchants', {}))} enchant levels and "
        f"{len(payload.get('potentials', {}))} potential options to "
        f"{len(payload.get('engraving_potences', {}))} engraving potence options to "
        f"{len(payload.get('card_masteries', {}))} card mastery rows and "
        f"{len(payload.get('core_runes', {}))} core rune options to "
        f"{args.output.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
