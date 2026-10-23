from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_attack_instance_catalog import ActParseError, parse_act


DEFAULT_RESOURCE_ROOT = Path(r"E:\ext0811.rar\Resource00\Resource00\resource")


@dataclass(frozen=True, slots=True)
class MergedAction:
    global_index: int
    source_file: str
    local_index: int
    name: str
    length_frames: int
    signal_types: tuple[int, ...]
    signal_orders: tuple[int, ...]
    signal_payload0: tuple[int, ...]


def _casefold_files(directory: Path) -> dict[str, Path]:
    return {
        path.name.casefold(): path
        for path in directory.iterdir()
        if path.is_file()
    }


def load_merged_actions(resource_root: Path, player_class: str) -> list[MergedAction]:
    class_dir = resource_root / "char" / "player" / player_class
    manifest = class_dir / f"{player_class}.act"
    if not manifest.is_file():
        raise FileNotFoundError(manifest)

    files = _casefold_files(class_dir)
    merged: list[MergedAction] = []
    for listed_name in manifest.read_text(encoding="utf-8-sig").splitlines():
        listed_name = listed_name.strip()
        if not listed_name:
            continue
        path = files.get(listed_name.casefold())
        if path is None:
            print(f"MISSING\t{listed_name}")
            continue
        try:
            parsed = parse_act(path)
        except ActParseError as exc:
            print(f"PARSE_ERROR\t{listed_name}\t{exc}")
            continue
        for local_index, action in enumerate(parsed.actions):
            merged.append(
                MergedAction(
                    global_index=len(merged),
                    source_file=path.name,
                    local_index=local_index,
                    name=action.name,
                    length_frames=action.length_frames,
                    signal_types=tuple(signal.signal_type for signal in action.signals),
                    signal_orders=tuple(signal.order for signal in action.signals),
                    signal_payload0=tuple(
                        int.from_bytes(signal.payload[:4], "little", signed=False)
                        for signal in action.signals
                    ),
                )
            )
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("player_class")
    parser.add_argument("indices", nargs="+", type=int)
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    parser.add_argument("--radius", type=int, default=1)
    args = parser.parse_args()

    merged = load_merged_actions(args.resource_root, args.player_class)
    print(f"CLASS\t{args.player_class}\tACTIONS\t{len(merged)}")
    selected: set[int] = set()
    for requested in args.indices:
        for candidate in (requested - 1, requested):
            selected.update(range(candidate - args.radius, candidate + args.radius + 1))
    for index in sorted(selected):
        if not 0 <= index < len(merged):
            continue
        action = merged[index]
        print(
            "ACTION\t"
            f"{action.global_index}\t{action.source_file}\t{action.local_index}\t"
            f"{action.name}\t{action.length_frames}\t"
            f"{','.join(map(str, action.signal_types))}\t"
            f"{','.join(f'{index}:{signal_type}:{order}:{payload0}' for index, (signal_type, order, payload0) in enumerate(zip(action.signal_types, action.signal_orders, action.signal_payload0)))}"
        )


if __name__ == "__main__":
    main()
