from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, norm
from dragon_nest_builder.live_dps import effects as effect_rules
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


DEFAULT_CAPTURE_DIR = Path.home() / "Documents" / "DragonNestDpsCapture" / "live_dps_diagnostics"
DEFAULT_OUTPUT = ROOT.parents[1] / "outputs" / "buff_debuff_recognition_audit_20260816" / "audit_data.json"


def text(value: Any) -> str:
    return str(value or "").strip()


def event_field(event: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = event.get(name)
        if value not in (None, ""):
            return value
    return ""


def event_skill(event: dict[str, Any]) -> str:
    return text(event_field(event, "source_skill", "source_skill_name", "skill_name", "skill"))


def event_job(event: dict[str, Any]) -> str:
    return text(event_field(event, "source_job", "job", "source_profession"))


def event_code(event: dict[str, Any]) -> str:
    raw = event_field(event, "status_code", "status_id", "effect_code", "effect_id", "code")
    if isinstance(raw, int):
        return f"0x{raw:08X}"
    return text(raw)


def event_label(event: dict[str, Any]) -> str:
    return text(event_field(event, "label", "effect", "effect_key", "status_kind", "name", "display"))


def canonical_skill(skill: str, job: str) -> str:
    key = norm(skill)
    alias = getattr(effect_rules, "_EX_ALIASES", {}).get(key)
    if alias is not None:
        target, jobs = alias
        if not job or job in jobs:
            return norm(target)
    return key


def is_unknown_event(event: dict[str, Any]) -> bool:
    haystack = " ".join(text(value) for value in event.values()).casefold()
    return any(token in haystack for token in ("待识别", "未知状态", "unknown", "unmapped"))


def max_level_buff_display(rows: list[dict[str, Any]]) -> str:
    values: list[str] = []
    by_attribute: dict[str, tuple[float, Any]] = {}
    for row in rows:
        attribute = text(row.get("属性"))
        level = float(row.get("等级") or 0)
        current = by_attribute.get(attribute)
        if attribute and (current is None or level >= current[0]):
            by_attribute[attribute] = (level, row.get("表格数值"))
    for attribute, (_, value) in sorted(by_attribute.items()):
        suffix = "%" if attribute.endswith("%") else ""
        clean_attribute = attribute[:-1] if suffix else attribute
        values.append(f"{clean_attribute} +{value}{suffix}")
    return "，".join(values)


def load_expected_rules(store: DataStore) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    rules = getattr(effect_rules, "_RULES_BY_SKILL", {})
    for skill, rows in rules.items():
        normalized_rows = rows if isinstance(rows, (list, tuple)) else (rows,)
        for row in normalized_rows:
            effect_key = text(getattr(row, "key", ""))
            value = getattr(row, "value", "")
            duration = getattr(row, "duration_seconds", "")
            key = f"debuff::{norm(skill)}::{effect_key}::{value}::{duration}"
            expected[key] = {
                "type": "Debuff/状态",
                "skill": text(skill),
                "category": effect_key,
                "effect": effect_key,
                "value": value,
                "duration": duration,
                "scope": "boss",
                "rule_source": "effects.py",
            }

    buff_path = ROOT / "dragon_nest_builder" / "data" / "live_dps_profiles" / "player_buff_display_catalog_v576.json"
    payload = json.loads(buff_path.read_text(encoding="utf-8"))
    display_catalog: dict[str, dict[str, Any]] = {}
    for row in payload.get("buffs", []):
        skill = text(row.get("skill"))
        display_catalog[norm(skill)] = row
        key = f"buff::{norm(skill)}::{text(row.get('category'))}"
        expected[key] = {
            "type": "Buff",
            "skill": skill,
            "category": text(row.get("category")),
            "effect": text(row.get("display")),
            "value": "",
            "duration": "",
            "scope": text(row.get("scope") or "personal"),
            "rule_source": "player_buff_display_catalog_v576.json",
            "expected_jobs": "",
        }

    grouped_buffs: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in store.tables.get("buff", []):
        category = text(row.get("类别"))
        if category not in {"个人buff", "团队buff"}:
            continue
        grouped_buffs[(category, text(row.get("来源")), text(row.get("技能")))].append(row)
    for (category, source, skill), rows in grouped_buffs.items():
        normalized = norm(skill)
        display_row = display_catalog.get(normalized, {})
        existing = next(
            (item for item in expected.values() if item["type"] == "Buff" and norm(item["skill"]) == normalized),
            None,
        )
        generated_display = max_level_buff_display(rows)
        if existing is not None:
            existing["expected_jobs"] = source
            if not existing.get("effect"):
                existing["effect"] = generated_display
            existing["rule_source"] += " + skill_buff"
            continue
        key = f"buff::{normalized}::{category}::{source}"
        expected[key] = {
            "type": "Buff",
            "skill": skill,
            "category": category,
            "effect": text(display_row.get("display")) or generated_display,
            "value": "",
            "duration": "",
            "scope": "team" if category == "团队buff" else "personal",
            "rule_source": "skill_buff",
            "expected_jobs": source,
        }
    return expected


def load_skill_jobs() -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    jobs_dir = ROOT / "dragon_nest_builder" / "data" / "modular" / "tables" / "skills" / "dngamer_skill_sim_reborn_split" / "jobs"
    for path in jobs_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tree_rows = ((payload.get("tree") or {}).get("tree") or [])
        tree_jobs = [text((row.get("job") or {}).get("name")) for row in tree_rows if isinstance(row, dict)]
        job_name = text(
            payload.get("job")
            or payload.get("job_name")
            or payload.get("职业")
            or (tree_jobs[-1] if tree_jobs else "")
            or path.stem
        )
        rows: Iterable[Any] = payload.get("skills") or payload.get("技能") or payload.get("rows") or []
        if isinstance(rows, dict):
            rows = rows.values()
        for row in rows:
            if not isinstance(row, dict):
                continue
            skill = text(row.get("skill") or row.get("name") or row.get("技能") or row.get("技能名称"))
            if skill:
                result[norm(skill)].add(job_name)
    return result


def capture_paths(capture_dir: Path, minimum_bytes: int, limit: int) -> list[Path]:
    rows = [path for path in capture_dir.glob("*.dpscap") if path.stat().st_size >= minimum_bytes]
    rows.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return rows[:limit] if limit > 0 else rows


def load_capture(core: NativeSimulatorCore, path: Path) -> dict[str, Any]:
    def progress(percent: int, message: str) -> None:
        if percent in {1, 40, 80, 98}:
            print(f"[{path.name}] {percent:>3}% {message}", flush=True)

    return core.load_replay(str(path), progress)


def audit(capture_dir: Path, minimum_bytes: int, limit: int) -> dict[str, Any]:
    store = DataStore.load()
    expected = load_expected_rules(store)
    skill_jobs = load_skill_jobs()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    paths = capture_paths(capture_dir, minimum_bytes, limit)

    casts: Counter[tuple[str, str]] = Counter()
    cast_files: dict[tuple[str, str], set[str]] = defaultdict(set)
    statuses: Counter[tuple[str, str, str, str, str]] = Counter()
    status_files: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    status_values: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    status_durations: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    unknowns: Counter[tuple[str, str, str, str]] = Counter()
    unknown_files: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    failures: list[dict[str, str]] = []

    for index, path in enumerate(paths, 1):
        print(f"\n[{index}/{len(paths)}] {path.name} ({path.stat().st_size / 1024 / 1024:.1f} MiB)", flush=True)
        try:
            payload = load_capture(core, path)
        except Exception as exc:
            failures.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAILED: {exc}", flush=True)
            continue

        for row in payload.get("skill_cast_events", []):
            skill = text(row.get("skill_name") or row.get("skill"))
            job = text(row.get("job"))
            if not skill:
                continue
            key = (canonical_skill(skill, job), job)
            casts[key] += 1
            cast_files[key].add(path.name)

        for event_type, field in (("Debuff/状态", "boss_effect_events"), ("Buff", "player_buff_events")):
            for row in payload.get(field, []):
                skill = event_skill(row)
                job = event_job(row)
                code = event_code(row)
                label = event_label(row)
                key = (event_type, canonical_skill(skill, job), job, code, label)
                statuses[key] += 1
                status_files[key].add(path.name)
                value = event_field(row, "value", "amount", "parameter", "magnitude")
                duration = event_field(row, "duration_seconds", "duration", "remaining_seconds")
                if value not in (None, ""):
                    status_values[key].add(text(value))
                if duration not in (None, ""):
                    status_durations[key].add(text(duration))
                if is_unknown_event(row) or not skill:
                    unknown_key = (event_type, code, job, skill or label)
                    unknowns[unknown_key] += 1
                    unknown_files[unknown_key].add(path.name)

    rows: list[dict[str, Any]] = []
    for item in expected.values():
        normalized = norm(item["skill"])
        matching_casts = [(key, count) for key, count in casts.items() if key[0] == normalized]
        matching_statuses = [(key, count) for key, count in statuses.items() if key[0] == item["type"] and key[1] == normalized]
        cast_count = sum(count for _, count in matching_casts)
        status_count = sum(count for _, count in matching_statuses)
        observed_jobs = {key[1] for key, _ in matching_casts if key[1]} | {key[2] for key, _ in matching_statuses if key[2]}
        expected_jobs = {part for part in text(item.get("expected_jobs")).split("、") if part}
        expected_jobs.discard("通用")
        inferred_jobs = skill_jobs.get(normalized, set())
        if item["type"] == "Buff":
            jobs = sorted(expected_jobs or inferred_jobs or observed_jobs)
        else:
            jobs = sorted(observed_jobs | inferred_jobs | expected_jobs)
        files = sorted({name for key, _ in matching_casts for name in cast_files[key]} | {name for key, _ in matching_statuses for name in status_files[key]})
        codes = sorted({key[3] for key, _ in matching_statuses if key[3]})
        labels = sorted({key[4] for key, _ in matching_statuses if key[4]})
        if status_count > 0 and not any("待识别" in label or "未知" in label for label in labels):
            recognition = "已识别（实战验证）"
            issue = ""
        elif cast_count > 0 and status_count == 0:
            recognition = "未识别（有施法无状态）"
            issue = "抓包中存在技能施放，但当前状态事件没有对应来源技能。"
        elif status_count > 0:
            recognition = "部分识别（仍有未知字段）"
            issue = "已经收到状态事件，但名称、效果或参数仍未完全映射。"
        else:
            recognition = "规则存在（缺少实战样本）"
            issue = "当前样本库未出现可核对的施法或状态事件。"
        rows.append({
            **item,
            "recognition": recognition,
            "jobs": "、".join(jobs),
            "cast_count": cast_count,
            "status_count": status_count,
            "status_codes": "、".join(codes),
            "observed_labels": "、".join(labels),
            "sample_count": len(files),
            "sample_files": "、".join(files[:8]),
            "issue": issue,
        })

    known_expected = {(row["type"], norm(row["skill"])) for row in rows}
    observed_only: list[dict[str, Any]] = []
    for key, count in statuses.most_common():
        event_type, normalized_skill, job, code, label = key
        if (event_type, normalized_skill) in known_expected:
            continue
        files = sorted(status_files[key])
        observed_only.append({
            "type": event_type,
            "skill": normalized_skill,
            "jobs": job,
            "status_code": code,
            "label": label,
            "count": count,
            "values": "、".join(sorted(status_values[key])[:12]),
            "durations": "、".join(sorted(status_durations[key])[:12]),
            "sample_count": len(files),
            "sample_files": "、".join(files[:8]),
        })

    unknown_rows = []
    for key, count in unknowns.most_common():
        event_type, code, job, source = key
        files = sorted(unknown_files[key])
        unknown_rows.append({
            "type": event_type,
            "status_code": code,
            "job": job,
            "source": source,
            "count": count,
            "sample_count": len(files),
            "sample_files": "、".join(files[:10]),
        })

    rows.sort(key=lambda row: (row["recognition"], row["type"], row["jobs"], row["skill"], row["effect"]))
    return {
        "generated_at": "2026-08-16",
        "capture_dir": str(capture_dir),
        "capture_files_total": len(paths),
        "capture_files_success": len(paths) - len(failures),
        "capture_files_failed": len(failures),
        "minimum_bytes": minimum_bytes,
        "rows": rows,
        "observed_only": observed_only,
        "unknown_statuses": unknown_rows,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit live DPS buff/debuff recognition against replay evidence.")
    parser.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--minimum-bytes", type=int, default=1_000_000)
    parser.add_argument("--limit", type=int, default=0, help="Newest N captures; 0 means all matching files.")
    parser.add_argument("--inspect", type=Path, help="Load one replay and print compact event field examples.")
    args = parser.parse_args()

    if args.inspect:
        store = DataStore.load()
        core = NativeSimulatorCore(store, BuildCalculator(store))
        payload = load_capture(core, args.inspect)
        compact = {
            "skill_cast_event": (payload.get("skill_cast_events") or [{}])[0],
            "boss_effect_event": (payload.get("boss_effect_events") or [{}])[0],
            "player_buff_event": (payload.get("player_buff_events") or [{}])[0],
            "counts": {
                "skill_cast_events": len(payload.get("skill_cast_events") or []),
                "boss_effect_events": len(payload.get("boss_effect_events") or []),
                "player_buff_events": len(payload.get("player_buff_events") or []),
            },
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
        return

    result = audit(args.capture_dir, args.minimum_bytes, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWROTE {args.output}")
    print(json.dumps({key: result[key] for key in ("capture_files_total", "capture_files_success", "capture_files_failed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
