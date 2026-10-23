from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "live_dps_page_layout.py",
    "live_dps_page_boss_statistics.py",
    "live_dps_page_capture.py",
    "live_dps_page_party.py",
    "live_dps_page_views.py",
    "live_dps_page_scoring.py",
)


def main() -> None:
    for name in FILES:
        path = ROOT / "dragon_nest_builder/ui" / name
        lines = path.read_text(encoding="utf-8").splitlines()
        class_index = next(
            index for index, line in enumerate(lines) if line.startswith("class ")
        )
        normalized = lines[: class_index + 1]
        for line in lines[class_index + 1 :]:
            if line and not line.startswith("    "):
                raise RuntimeError(f"unexpected class-body indentation in {path}: {line!r}")
            normalized.append(line[4:] if line else line)
        path.write_text("\n".join(normalized) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
