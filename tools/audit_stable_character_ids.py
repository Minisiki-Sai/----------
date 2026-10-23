from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.npcap import (  # noqa: E402
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    PREBATTLE_STATE_ADAPTER_INDEX,
    PROFILE_IDENTITY_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
    decode_profile_identity_bindings,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
    decode_captured_frame,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    _split_length_framed_tcp_messages,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (  # noqa: E402
    decode_tcp_inspect_identity_payload,
    decode_tcp_login_identity_payload,
    decode_tcp_player_spawn_identity_payload,
)


MAGIC = b"DNDP1\x00"
HEADER = struct.Struct("<dBII")
SKIP_ADAPTERS = frozenset(
    {
        IDENTITY_METADATA_ADAPTER_INDEX,
        PROFILE_IDENTITY_ADAPTER_INDEX,
        PREBATTLE_STATE_ADAPTER_INDEX,
        SESSION_EVIDENCE_ADAPTER_INDEX,
    }
)


def _spawn_rows(
    path: Path,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    transports = {
        "main": V576TransportReassembler(),
        "identity": V576TransportReassembler(),
    }
    spawn_rows: list[dict[str, object]] = []
    binding_rows: list[dict[str, object]] = []
    login_rows: list[dict[str, object]] = []
    inspect_identity_rows: list[dict[str, object]] = []

    def consume(logical, channel: str) -> None:
        if (
            logical.transport.upper() != "TCP"
            or logical.inferred_direction() != "server_to_client"
        ):
            return
        for payload in _split_length_framed_tcp_messages(logical.payload):
            login = decode_tcp_login_identity_payload(payload)
            if login is not None:
                login_rows.append(
                    {
                        "capture": path.name,
                        "channel": channel,
                        "actor_marker": int(login.actor_marker),
                        "job_class_codes": list(login.job_class_codes),
                    }
                )
            inspect_identity = decode_tcp_inspect_identity_payload(payload)
            if inspect_identity is not None:
                inspect_identity_rows.append(
                    {
                        "capture": path.name,
                        "channel": channel,
                        "profile_id": int(inspect_identity.profile_id),
                        "identity_flags": int(inspect_identity.identity_flags),
                        "profile_marker": int(inspect_identity.profile_marker),
                    }
                )
            decoded = decode_tcp_player_spawn_identity_payload(payload)
            if decoded is None:
                continue
            spawn_rows.append(
                {
                    "capture": path.name,
                    "channel": channel,
                    "name": decoded.character_name,
                    "profile_id": int(decoded.profile_id),
                    "runtime_id": int(decoded.runtime_actor_id),
                    "job_code": int(decoded.job_class_code),
                    "length": len(decoded.plaintext),
                    "sha256": hashlib.sha256(decoded.plaintext).hexdigest(),
                    "plaintext": decoded.plaintext,
                }
            )

    with path.open("rb", buffering=1024 * 1024) as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"{path}: invalid capture")
        while True:
            header = handle.read(HEADER.size)
            if not header or len(header) != HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = HEADER.unpack(header)
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            if adapter == PROFILE_IDENTITY_ADAPTER_INDEX:
                for row in decode_profile_identity_bindings(frame):
                    binding_rows.append({"capture": path.name, **row})
                continue
            if adapter in SKIP_ADAPTERS:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            channel = "identity" if adapter == IDENTITY_TRACE_ADAPTER_INDEX else "main"
            for logical in transports[channel].feed(packet):
                consume(logical, channel)
    for channel, transport in transports.items():
        for logical in transport.flush():
            consume(logical, channel)
    unique = {}
    for row in spawn_rows:
        key = (
            row["capture"], row["name"], row["profile_id"], row["runtime_id"],
            row["sha256"],
        )
        unique[key] = row
    return list(unique.values()), binding_rows, login_rows, inspect_identity_rows


def _candidate_fields(rows_by_name: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    repeated = {
        name: rows
        for name, rows in rows_by_name.items()
        if len({str(row["capture"]) for row in rows}) >= 2
        and len({int(row["profile_id"]) for row in rows}) >= 2
    }
    if not repeated:
        return []
    minimum_length = min(
        len(row["plaintext"])
        for rows in repeated.values()
        for row in rows
    )
    candidates: list[dict[str, object]] = []
    for size in (4, 8):
        for offset in range(0, minimum_length - size + 1):
            if offset < 12 or (offset < 52 and offset + size > 12):
                continue
            values_by_name: dict[str, int] = {}
            valid = True
            for name, rows in repeated.items():
                values = {
                    int.from_bytes(row["plaintext"][offset : offset + size], "little")
                    for row in rows
                }
                if len(values) != 1:
                    valid = False
                    break
                value = next(iter(values))
                if value == 0 or value == (1 << (size * 8)) - 1:
                    valid = False
                    break
                values_by_name[name] = value
            if not valid or len(set(values_by_name.values())) != len(values_by_name):
                continue
            candidates.append(
                {
                    "offset": offset,
                    "size": size,
                    "values": {
                        name: f"0x{value:0{size * 2}X}"
                        for name, value in values_by_name.items()
                    },
                }
            )
    return candidates


def _aligned_field_quality(
    rows_by_name: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Describe whether aligned header values behave like durable name keys.

    A real character key should not change for one name and should not be shared
    by different names.  Report both failure modes instead of selecting fields
    merely because a small hand-picked subset happened to remain constant.
    """

    report: list[dict[str, object]] = []
    for size in (4, 8):
        for offset in range(52, 96, 4):
            values_by_name: dict[str, set[int]] = defaultdict(set)
            names_by_value: dict[int, set[str]] = defaultdict(set)
            record_count = 0
            for name, rows in rows_by_name.items():
                for row in rows:
                    plaintext = row["plaintext"]
                    if offset + size > len(plaintext):
                        continue
                    value = int.from_bytes(
                        plaintext[offset : offset + size], "little"
                    )
                    values_by_name[name].add(value)
                    names_by_value[value].add(name)
                    record_count += 1
            unstable = {
                name: [f"0x{value:0{size * 2}X}" for value in sorted(values)]
                for name, values in values_by_name.items()
                if len(values) > 1
            }
            collisions = {
                f"0x{value:0{size * 2}X}": sorted(names)
                for value, names in names_by_value.items()
                if value != 0 and len(names) > 1
            }
            report.append(
                {
                    "offset": offset,
                    "size": size,
                    "record_count": record_count,
                    "name_count": len(values_by_name),
                    "unique_nonzero_value_count": len(
                        {value for value in names_by_value if value != 0}
                    ),
                    "zero_name_count": sum(
                        1 for values in values_by_name.values() if 0 in values
                    ),
                    "unstable_name_count": len(unstable),
                    "collision_value_count": len(collisions),
                    "unstable_examples": dict(list(sorted(unstable.items()))[:12]),
                    "collision_examples": dict(
                        list(sorted(collisions.items()))[:12]
                    ),
                }
            )
    return report


def _profile_change_diffs(
    rows_by_name: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for name, rows in sorted(rows_by_name.items()):
        by_profile: dict[int, dict[str, object]] = {}
        for row in rows:
            by_profile.setdefault(int(row["profile_id"]), row)
        if len(by_profile) < 2:
            continue
        samples = list(by_profile.values())
        base = samples[0]["plaintext"]
        changed_offsets: set[int] = set()
        for sample in samples[1:]:
            current = sample["plaintext"]
            shared = min(len(base), len(current))
            changed_offsets.update(
                offset for offset in range(shared) if base[offset] != current[offset]
            )
            changed_offsets.update(range(shared, max(len(base), len(current))))
        ranges: list[list[int]] = []
        for offset in sorted(changed_offsets):
            if not ranges or offset != ranges[-1][1] + 1:
                ranges.append([offset, offset])
            else:
                ranges[-1][1] = offset
        result.append(
            {
                "name": name,
                "profiles": [
                    f"0x{value:08X}" for value in sorted(by_profile)
                ],
                "captures": sorted({str(row["capture"]) for row in samples}),
                "changed_byte_ranges": ranges,
                "offset_52_u32": sorted(
                    {
                        f"0x{int.from_bytes(row['plaintext'][52:56], 'little'):08X}"
                        for row in samples
                    }
                ),
                "offset_52_u64": sorted(
                    {
                        f"0x{int.from_bytes(row['plaintext'][52:60], 'little'):016X}"
                        for row in samples
                    }
                ),
            }
        )
    return result


def _perfect_fields(
    rows_by_name: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Find byte windows stable per name and unique in this corpus."""

    if not rows_by_name:
        return []
    minimum_length = min(
        len(row["plaintext"])
        for rows in rows_by_name.values()
        for row in rows
    )
    result: list[dict[str, object]] = []
    for size in (4, 8):
        for offset in range(4, minimum_length - size + 1):
            # The character-name bytes are already the grouping key and are
            # therefore trivially unique.  Do not report overlapping windows.
            if offset < 52 and offset + size > 12:
                continue
            values_by_name: dict[str, set[int]] = defaultdict(set)
            names_by_value: dict[int, set[str]] = defaultdict(set)
            for name, rows in rows_by_name.items():
                for row in rows:
                    value = int.from_bytes(
                        row["plaintext"][offset : offset + size], "little"
                    )
                    values_by_name[name].add(value)
                    names_by_value[value].add(name)
            if any(len(values) != 1 or 0 in values for values in values_by_name.values()):
                continue
            if any(len(names) != 1 for names in names_by_value.values()):
                continue
            result.append(
                {
                    "offset": offset,
                    "size": size,
                    "name_count": len(values_by_name),
                    "sample_values": {
                        name: f"0x{next(iter(values)):0{size * 2}X}"
                        for name, values in list(sorted(values_by_name.items()))[:8]
                    },
                }
            )
    return result


def _top_optional_stable_unique_fields(
    rows_by_name: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Rank optional byte windows that could still be sparse durable keys."""

    if not rows_by_name:
        return []
    minimum_length = min(
        len(row["plaintext"])
        for rows in rows_by_name.values()
        for row in rows
    )
    result: list[dict[str, object]] = []
    for size in (4, 8):
        for offset in range(4, minimum_length - size + 1):
            if offset < 52 and offset + size > 12:
                continue
            values_by_name: dict[str, set[int]] = defaultdict(set)
            names_by_value: dict[int, set[str]] = defaultdict(set)
            for name, rows in rows_by_name.items():
                for row in rows:
                    value = int.from_bytes(
                        row["plaintext"][offset : offset + size], "little"
                    )
                    if value:
                        values_by_name[name].add(value)
                        names_by_value[value].add(name)
            unstable = sum(1 for values in values_by_name.values() if len(values) > 1)
            collisions = sum(1 for names in names_by_value.values() if len(names) > 1)
            coverage = sum(1 for values in values_by_name.values() if len(values) == 1)
            if unstable or collisions or coverage < max(8, len(rows_by_name) // 2):
                continue
            result.append(
                {
                    "offset": offset,
                    "size": size,
                    "covered_names": coverage,
                    "total_names": len(rows_by_name),
                }
            )
    return sorted(
        result,
        key=lambda row: (-int(row["covered_names"]), int(row["offset"]), int(row["size"])),
    )[:100]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    spawn_rows: list[dict[str, object]] = []
    binding_rows: list[dict[str, object]] = []
    login_rows: list[dict[str, object]] = []
    inspect_identity_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for capture in args.captures:
        try:
            spawns, bindings, logins, inspect_identities = _spawn_rows(capture)
            spawn_rows.extend(spawns)
            binding_rows.extend(bindings)
            login_rows.extend(logins)
            inspect_identity_rows.extend(inspect_identities)
        except Exception as exc:
            errors.append({"capture": str(capture), "error": repr(exc)})

    rows_by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in spawn_rows:
        rows_by_name[str(row["name"])].append(row)
    binding_by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in binding_rows:
        binding_by_name[str(row.get("character_name") or "")].append(row)

    names = sorted(set(rows_by_name) | set(binding_by_name))
    name_summary = []
    for name in names:
        spawns = rows_by_name.get(name, [])
        bindings = binding_by_name.get(name, [])
        profile_ids = {
            int(row["profile_id"]) for row in spawns
        } | {
            int(row.get("profile_id", 0) or 0) for row in bindings
            if int(row.get("profile_id", 0) or 0) > 0
        }
        runtime_ids = {
            int(row["runtime_id"]) for row in spawns
        } | {
            int(row.get("runtime_actor_id", 0) or 0) for row in bindings
            if int(row.get("runtime_actor_id", 0) or 0) > 0
        }
        captures = {
            str(row["capture"]) for row in (*spawns, *bindings)
        }
        name_summary.append(
            {
                "name": name,
                "captures": sorted(captures),
                "profile_ids": [f"0x{value:08X}" for value in sorted(profile_ids)],
                "runtime_ids": [f"0x{value:08X}" for value in sorted(runtime_ids)],
                "spawn_records": len(spawns),
                "binding_records": len(bindings),
                "plaintext_lengths": sorted({int(row["length"]) for row in spawns}),
            }
        )

    payload = {
        "capture_count": len(args.captures),
        "spawn_record_count": len(spawn_rows),
        "binding_record_count": len(binding_rows),
        "login_identity_rows": login_rows,
        "inspect_identity_rows": inspect_identity_rows,
        "same_name_multiple_profiles": [
            row for row in name_summary
            if len(row["captures"]) >= 2 and len(row["profile_ids"]) >= 2
        ],
        "same_name_across_captures": [
            row for row in name_summary if len(row["captures"]) >= 2
        ],
        "candidate_stable_plaintext_fields": _candidate_fields(rows_by_name),
        "aligned_field_quality": _aligned_field_quality(rows_by_name),
        "profile_change_plaintext_diffs": _profile_change_diffs(rows_by_name),
        "perfect_fields_outside_name": _perfect_fields(rows_by_name),
        "top_optional_stable_unique_fields": _top_optional_stable_unique_fields(
            rows_by_name
        ),
        "all_names": name_summary,
        "errors": errors,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
