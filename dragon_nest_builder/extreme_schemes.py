from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


EXTREME_TEAM_SCHEMES_PATH = (
    Path(os.getenv("APPDATA") or Path.home())
    / "DragonNestBuildSimulator"
    / "extreme_team_schemes.json"
)
EXTREME_TEAM_SCHEME_VERSION = 1
EXTREME_TEAM_TIMELINE_REVISION = 8


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, tuple):
        return {"__kind__": "tuple", "items": [_json_value(item) for item in value]}
    if isinstance(value, set):
        return {"__kind__": "set", "items": [_json_value(item) for item in sorted(value, key=str)]}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: _json_value(item) for key, item in value.items()}
        return {
            "__kind__": "mapping",
            "items": [[_json_value(key), _json_value(item)] for key, item in value.items()],
        }
    return str(value)


def _python_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_python_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get("__kind__")
    if kind == "tuple":
        return tuple(_python_value(item) for item in value.get("items", []))
    if kind == "set":
        return set(_python_value(item) for item in value.get("items", []))
    if kind == "mapping":
        return {
            _python_value(key): _python_value(item)
            for key, item in value.get("items", [])
        }
    return {key: _python_value(item) for key, item in value.items()}


def load_extreme_team_schemes() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(EXTREME_TEAM_SCHEMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    schemes = raw.get("schemes", raw) if isinstance(raw, dict) else {}
    if not isinstance(schemes, dict):
        return {}
    return {
        str(name): _python_value(payload)
        for name, payload in schemes.items()
        if str(name).strip() and isinstance(payload, dict)
    }


def save_extreme_team_schemes(schemes: dict[str, dict[str, Any]]) -> None:
    EXTREME_TEAM_SCHEMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": EXTREME_TEAM_SCHEME_VERSION,
        "schemes": _json_value(schemes),
    }
    temporary = EXTREME_TEAM_SCHEMES_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(EXTREME_TEAM_SCHEMES_PATH)


def save_extreme_team_scheme(
    schemes: dict[str, dict[str, Any]],
    name: str,
    payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    updated = dict(schemes)
    saved = dict(payload)
    saved["schema_version"] = EXTREME_TEAM_SCHEME_VERSION
    saved["saved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    updated[str(name).strip()] = saved
    save_extreme_team_schemes(updated)
    return updated
