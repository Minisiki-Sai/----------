"""Read-only shadow audit of MASkillUser current-skill -> Projectile +0x1BC0.

The candidate side deliberately uses only real action/cast events, actor IDs,
and the first complete direct37 occurrence of (owner, damage_object_id).  It
does not read damage values, display names, packet skill slots, or existing
inferred skill labels to choose a candidate.

Independent deterministic attribution methods are used only as truth.  The
tool never executes the game client and never changes production modules.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)


DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs/projectile_skill_copy_shadow_v52_20260909"

# These are observed actions, not damage-derived timeline synthesis.  Compact
# input is excluded because it proves a button press but not an accepted
# action.  inferred_combo is explicitly synthetic.
ACCEPTED_ACTION_KINDS = frozenset(
    {
        "skill_action",
        "skill_cast",
        "status_skill_action",
        "structured_multi_action",
    }
)

# Truth must not consume the native result skill slot being audited, a display
# name, a damage amount fingerprint, or a timing/profile inference.  The exact
# object chain is independent because it joins a separately registered
# Projectile template to the same object ID.  Entity and status chains are
# retained for completeness, although player-owned Projectile rows dominate
# this audit.
INDEPENDENT_TRUTH_METHODS = frozenset(
    {
        "damage_object_projectile_link",
        "packet_entity_skill_id",
        "packet_entity_projectile_selector",
        "packet_entity_self_attack",
        "packet_entity_template_owner",
        "periodic_status_packet",
        "status_damage_packet",
        "status_damage_skill",
    }
)

PRODUCTION_FILES = (
    "dragon_nest_builder/live_dps/damage_attribution.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/models.py",
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/sources.py",
    "dragon_nest_builder/live_dps/udp_damage_records.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
)


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _production_signature() -> dict[str, str]:
    return {name: _hash_file(ROOT / name) for name in PRODUCTION_FILES}


def _parse_positive_skill(value: object) -> int:
    text = str(value or "").strip()
    if not text or not text.isdecimal():
        return 0
    result = int(text)
    return result if result > 0 else 0


def _parse_actor(value: object) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except ValueError:
        return 0


def _canonical(skill_id: int) -> int:
    """Comparison-only normalization; never used to create a candidate."""
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(skill_id, skill_id))


def _selection(path: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    captures = [
        {
            "name": str(row["capture"]),
            "sha256": str(row["capture_sha256"]),
        }
        for row in value["sources"]["selected_results"]
    ]
    jobs = {str(row["job"]): dict(row) for row in value["jobs"]}
    if len(captures) != 35 or len(jobs) != 11:
        raise ValueError(
            f"fixed audit contract changed: captures={len(captures)}, under95_jobs={len(jobs)}"
        )
    return captures, jobs


def _find_captures(capture_root: Path, selected: Iterable[dict[str, str]]) -> list[Path]:
    wanted = {row["name"]: row["sha256"] for row in selected}
    by_name: dict[str, list[Path]] = defaultdict(list)
    for path in capture_root.rglob("*.dpscap"):
        if path.name in wanted:
            by_name[path.name].append(path)
    result: list[Path] = []
    for name, expected in wanted.items():
        matches = [path for path in by_name.get(name, ()) if _hash_file(path) == expected]
        if len(matches) != 1:
            raise ValueError(f"expected one hash-matched capture for {name}, got {len(matches)}")
        result.append(matches[0])
    return result


def _event_record(event: CombatEvent, packet: object):
    inventory = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
    if not inventory.complete:
        return None, inventory.rejection_reason
    rows = [
        row
        for row in inventory.damage_records
        if row.admissible_fields
        and row.family == "direct37"
        and row.record_offset == event.wire_record_offset
        and row.source_actor_id == event.wire_source_actor_id
        and row.target_actor_id == event.wire_target_actor_id
        and row.critical == bool(event.wire_critical)
    ]
    if len(rows) != 1:
        return None, f"physical_join_count:{len(rows)}"
    return rows[0], ""


@dataclass(slots=True)
class ActionState:
    skill_id: int
    timestamp_ms: int
    packet_kind: str
    sequence: int
    job: str


@dataclass(slots=True)
class Generation:
    owner: int
    object_id: int
    candidate_skill_id: int
    candidate_canonical_skill_id: int
    action_timestamp_ms: int
    action_kind: str
    action_sequence: int
    action_job: str
    first_hit_ms: int
    last_hit_ms: int
    hits: int = 0
    truth_hits: int = 0
    truth_exact_matches: int = 0
    truth_canonical_matches: int = 0
    truth_ids: Counter[int] = field(default_factory=Counter)
    truth_methods: Counter[str] = field(default_factory=Counter)
    jobs: Counter[str] = field(default_factory=Counter)
    current_skill_ids_after_birth: Counter[int] = field(default_factory=Counter)
    named_gap_hits: int = 0
    unknown_gap_hits: int = 0
    already_deterministic_hits: int = 0

    def observe(self, event: CombatEvent, current: ActionState | None) -> None:
        self.hits += 1
        self.last_hit_ms = max(self.last_hit_ms, int(event.timestamp_ms))
        if event.job:
            self.jobs[str(event.job)] += 1
        if current is not None and current.sequence != self.action_sequence:
            self.current_skill_ids_after_birth[current.skill_id] += 1
        method = str(event.attribution_method or "")
        assigned = _parse_positive_skill(event.skill_id)
        if method in INDEPENDENT_TRUTH_METHODS and assigned:
            self.truth_hits += 1
            self.truth_ids[assigned] += 1
            self.truth_methods[method] += 1
            self.truth_exact_matches += int(assigned == self.candidate_skill_id)
            self.truth_canonical_matches += int(
                _canonical(assigned) == self.candidate_canonical_skill_id
            )
        if is_deterministic_attribution(method):
            self.already_deterministic_hits += 1
        elif assigned:
            # The old label is used only to measure the frozen baseline gap;
            # it never changes candidate_skill_id or truth.
            self.named_gap_hits += 1
        else:
            self.unknown_gap_hits += 1

    def job(self) -> str:
        if self.jobs:
            return self.jobs.most_common(1)[0][0]
        return self.action_job

    def status(self) -> str:
        if not self.truth_hits:
            return "unvalidated"
        if self.truth_canonical_matches != self.truth_hits:
            return "conflict"
        return "validated_clean"

    def compact(self) -> dict[str, Any]:
        return {
            "owner": f"0x{self.owner:08X}",
            "damage_object_id": self.object_id,
            "candidate_skill_id_raw": self.candidate_skill_id,
            "candidate_skill_id_canonical": self.candidate_canonical_skill_id,
            "candidate_source": "actor.current_skill frozen at first direct37",
            "action_timestamp_ms": self.action_timestamp_ms,
            "action_packet_kind": self.action_kind,
            "action_sequence": self.action_sequence,
            "first_hit_ms": self.first_hit_ms,
            "last_hit_ms": self.last_hit_ms,
            "job": self.job(),
            "hits": self.hits,
            "truth_hits": self.truth_hits,
            "truth_exact_matches": self.truth_exact_matches,
            "truth_canonical_matches": self.truth_canonical_matches,
            "truth_skill_ids": {str(k): v for k, v in sorted(self.truth_ids.items())},
            "truth_methods": dict(sorted(self.truth_methods.items())),
            "later_current_skill_ids": {
                str(k): v for k, v in sorted(self.current_skill_ids_after_birth.items())
            },
            "already_deterministic_hits": self.already_deterministic_hits,
            "named_gap_hits": self.named_gap_hits,
            "unknown_gap_hits": self.unknown_gap_hits,
            "status": self.status(),
        }


def audit_capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    current: dict[int, ActionState] = {}
    generations: dict[tuple[int, int], Generation] = {}
    action_sequence = 0
    packet_sequence = 0
    counters: Counter[str] = Counter()
    action_kinds: Counter[str] = Counter()
    join_failures: Counter[str] = Counter()

    def feed_events(packet):
        nonlocal action_sequence, packet_sequence
        packet_sequence += 1
        events = original(packet)
        seen_records: set[tuple[int, int]] = set()
        for event in events:
            if isinstance(event, SkillCastEvent):
                action_kinds[str(event.packet_kind)] += 1
                actor = _parse_actor(event.source_id)
                skill_id = _parse_positive_skill(event.skill_id)
                if event.packet_kind not in ACCEPTED_ACTION_KINDS:
                    counters["rejected_action_kind"] += 1
                    continue
                if actor <= 0x00FFFFFF or not skill_id:
                    counters["rejected_action_actor_or_skill"] += 1
                    continue
                action_sequence += 1
                current[actor] = ActionState(
                    skill_id=skill_id,
                    timestamp_ms=int(event.timestamp_ms),
                    packet_kind=str(event.packet_kind),
                    sequence=action_sequence,
                    job=str(event.job or ""),
                )
                counters["accepted_actions"] += 1
                continue
            if not isinstance(event, CombatEvent):
                continue
            if event.healing or "direct37" not in event.wire_damage_layout:
                continue
            counters["direct37_events"] += 1
            physical, reason = _event_record(event, packet)
            if physical is None:
                join_failures[reason] += 1
                continue
            physical_key = (packet_sequence, int(physical.record_offset))
            if physical_key in seen_records:
                counters["duplicate_event_for_physical_record"] += 1
                continue
            seen_records.add(physical_key)
            counters["complete_direct37_records"] += 1
            owner = int(physical.source_actor_id)
            object_id = int(physical.damage_object_id)
            if owner <= 0x00FFFFFF:
                counters["non_player_source"] += 1
                continue
            if object_id in {0, 0xFFFFFFFF}:
                counters["invalid_damage_object_id"] += 1
                continue
            counters["player_object_records"] += 1
            state = current.get(owner)
            key = (owner, object_id)
            generation = generations.get(key)
            if generation is None:
                if state is None:
                    counters["first_hit_without_current_skill"] += 1
                    continue
                generation = Generation(
                    owner=owner,
                    object_id=object_id,
                    candidate_skill_id=state.skill_id,
                    candidate_canonical_skill_id=_canonical(state.skill_id),
                    action_timestamp_ms=state.timestamp_ms,
                    action_kind=state.packet_kind,
                    action_sequence=state.sequence,
                    action_job=state.job,
                    first_hit_ms=int(event.timestamp_ms),
                    last_hit_ms=int(event.timestamp_ms),
                )
                generations[key] = generation
                counters["candidate_generations"] += 1
            generation.observe(event, state)
            counters["candidate_hits"] += 1
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    status_counts: Counter[str] = Counter()
    job_rows: dict[str, Counter[str]] = defaultdict(Counter)
    skill_rows: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    conflict_groups: dict[tuple[str, int, tuple[int, ...]], Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    truth_hits = exact = canonical = 0
    for generation in generations.values():
        compact = generation.compact()
        status = generation.status()
        job = generation.job() or "未识别"
        status_counts[status] += 1
        truth_hits += generation.truth_hits
        exact += generation.truth_exact_matches
        canonical += generation.truth_canonical_matches
        job_row = job_rows[job]
        job_row["generations"] += 1
        job_row[f"generations_{status}"] += 1
        job_row["candidate_hits"] += generation.hits
        job_row["truth_hits"] += generation.truth_hits
        job_row["truth_exact_matches"] += generation.truth_exact_matches
        job_row["truth_canonical_matches"] += generation.truth_canonical_matches
        job_row["all_candidate_named_gap_hits"] += generation.named_gap_hits
        job_row["all_candidate_unknown_gap_hits"] += generation.unknown_gap_hits
        if status == "validated_clean":
            job_row["strict_same_generation_named_gap_hits"] += generation.named_gap_hits
            job_row["strict_same_generation_unknown_gap_hits"] += generation.unknown_gap_hits
        skill_key = (job, generation.candidate_canonical_skill_id)
        skill_row = skill_rows[skill_key]
        skill_row["generations"] += 1
        skill_row[f"generations_{status}"] += 1
        skill_row["hits"] += generation.hits
        skill_row["truth_hits"] += generation.truth_hits
        skill_row["truth_matches"] += generation.truth_canonical_matches
        skill_row["named_gap_hits"] += generation.named_gap_hits
        skill_row["unknown_gap_hits"] += generation.unknown_gap_hits
        if status == "conflict":
            key = (job, generation.candidate_canonical_skill_id, tuple(sorted(generation.truth_ids)))
            conflict = conflict_groups[key]
            conflict["generations"] += 1
            conflict["hits"] += generation.hits
            conflict["truth_hits"] += generation.truth_hits
            conflict["truth_mismatches"] += generation.truth_hits - generation.truth_canonical_matches
        if len(examples[status]) < 40:
            examples[status].append(compact)
    return {
        "capture": path.name,
        "capture_path": str(path),
        "capture_sha256": _hash_file(path),
        "summary": {
            **dict(sorted(counters.items())),
            "action_kinds": dict(sorted(action_kinds.items())),
            "join_failures": dict(sorted(join_failures.items())),
            "generations_by_status": dict(sorted(status_counts.items())),
            "truth_hits": truth_hits,
            "truth_exact_matches": exact,
            "truth_canonical_matches": canonical,
        },
        "jobs": {
            job: dict(counter) for job, counter in sorted(job_rows.items())
        },
        "skills": [
            {
                "job": job,
                "candidate_skill_id_canonical": skill_id,
                **dict(counter),
            }
            for (job, skill_id), counter in sorted(skill_rows.items())
        ],
        "conflict_groups": [
            {
                "job": job,
                "candidate_skill_id_canonical": candidate,
                "truth_skill_ids": list(truth_ids),
                **dict(counter),
            }
            for (job, candidate, truth_ids), counter in sorted(conflict_groups.items())
        ],
        "generation_examples": {
            status: rows for status, rows in sorted(examples.items())
        },
    }


def _wilson_lower(matches: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    p = matches / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return max(0.0, (centre - radius) / denominator)


def _counter_percent(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 6) if denominator else 0.0


def aggregate(
    captures: list[dict[str, Any]], under95: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    action_kinds: Counter[str] = Counter()
    join_failures: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    jobs: dict[str, Counter[str]] = defaultdict(Counter)
    skills: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    conflict_groups: dict[tuple[str, int, tuple[int, ...]], Counter[str]] = defaultdict(Counter)
    conflict_examples: list[dict[str, Any]] = []
    for capture in captures:
        summary = capture["summary"]
        for key, value in summary.items():
            if isinstance(value, int):
                totals[key] += value
        action_kinds.update(summary.get("action_kinds", {}))
        join_failures.update(summary.get("join_failures", {}))
        status_counts.update(summary.get("generations_by_status", {}))
        for job, values in capture["jobs"].items():
            jobs[job].update(values)
        for values in capture["skills"]:
            key = (str(values["job"]), int(values["candidate_skill_id_canonical"]))
            skills[key].update(
                {name: value for name, value in values.items() if name not in {
                    "job", "candidate_skill_id_canonical"
                }}
            )
        for values in capture["conflict_groups"]:
            key = (
                str(values["job"]),
                int(values["candidate_skill_id_canonical"]),
                tuple(int(value) for value in values["truth_skill_ids"]),
            )
            conflict_groups[key].update(
                {name: value for name, value in values.items() if name not in {
                    "job", "candidate_skill_id_canonical", "truth_skill_ids"
                }}
            )
        for example in capture.get("generation_examples", {}).get("conflict", ()):
            if len(conflict_examples) < 300:
                conflict_examples.append({"capture": capture["capture"], **example})

    truth = totals["truth_hits"]
    exact = totals["truth_exact_matches"]
    canonical = totals["truth_canonical_matches"]
    job_rows: list[dict[str, Any]] = []
    for job, counter in sorted(jobs.items()):
        truth_rows = counter["truth_hits"]
        matches = counter["truth_canonical_matches"]
        baseline = under95.get(job)
        strict_named = counter["strict_same_generation_named_gap_hits"]
        projected = None
        reaches_95 = None
        if baseline:
            denominator = int(baseline["named_population_hits"])
            projected = _counter_percent(
                int(baseline["deterministic_hits"]) + strict_named, denominator
            )
            reaches_95 = projected >= 95.0
        job_rows.append(
            {
                "job": job,
                **dict(counter),
                "canonical_precision_percent": _counter_percent(matches, truth_rows),
                "canonical_precision_wilson95_lower_percent": round(
                    100.0 * _wilson_lower(matches, truth_rows), 6
                ),
                "under95_baseline": bool(baseline),
                "baseline_named_coverage_percent": (
                    float(baseline["named_coverage_percent"]) if baseline else None
                ),
                "baseline_hits_needed_for_95": (
                    int(baseline["hits_needed_for_95"]) if baseline else None
                ),
                "strict_projected_named_coverage_percent": projected,
                "strict_projected_reaches_95": reaches_95,
            }
        )

    skill_rows = [
        {
            "job": job,
            "candidate_skill_id_canonical": skill_id,
            **dict(counter),
            "canonical_precision_percent": _counter_percent(
                counter["truth_matches"], counter["truth_hits"]
            ),
        }
        for (job, skill_id), counter in sorted(skills.items())
    ]
    return {
        "capture_count": len(captures),
        "totals": {
            **dict(totals),
            "action_kinds": dict(sorted(action_kinds.items())),
            "join_failures": dict(sorted(join_failures.items())),
            "generations_by_status": dict(sorted(status_counts.items())),
            "truth_exact_precision_percent": _counter_percent(exact, truth),
            "truth_canonical_precision_percent": _counter_percent(canonical, truth),
            "truth_canonical_precision_wilson95_lower_percent": round(
                100.0 * _wilson_lower(canonical, truth), 6
            ),
            "conflict_truth_hits": truth - canonical,
        },
        "jobs": job_rows,
        "under95_jobs": [row for row in job_rows if row["under95_baseline"]],
        "skills": skill_rows,
        "conflict_groups": [
            {
                "job": job,
                "candidate_skill_id_canonical": candidate,
                "truth_skill_ids": list(truth_ids),
                **dict(counter),
            }
            for (job, candidate, truth_ids), counter in sorted(
                conflict_groups.items(), key=lambda item: (-item[1]["truth_mismatches"], item[0])
            )
        ],
        "conflict_examples": conflict_examples,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    totals = summary["totals"]
    lines = [
        "# MASkillUser → Projectile +0x1BC0 shadow 审计（v52）",
        "",
        "## 结论",
        "",
        f"固定回放 **{summary['capture_count']}** 份抓包。候选只由真实动作更新的 "
        "`actor.current_skill` 与首次 `direct37(owner, damage_object_id)` 建立；对象候选建立后冻结。",
        "",
        f"共有 **{totals.get('candidate_generations', 0):,}** 个候选对象、"
        f"**{totals.get('candidate_hits', 0):,}** 个候选 Hit，其中 "
        f"**{totals.get('truth_hits', 0):,}** 个拥有与原生技能字段独立的确定性真值。"
        f"Canonical 精确率 **{totals['truth_canonical_precision_percent']:.4f}%**，"
        f"95% Wilson 下界 **{totals['truth_canonical_precision_wilson95_lower_percent']:.4f}%**，"
        f"冲突真值 Hit **{totals['conflict_truth_hits']:,}**。",
        "",
        "## 审计边界",
        "",
        "- 候选不读取技能显示名、伤害数值、旧推断技能标签、packet skill slot 或时间窗口。",
        "- `compact_skill_input` 与 `inferred_combo` 不更新 current_skill。",
        "- 真值排除 `packet_skill_id` 及全部时间/画像/旧推断路径，防止与 `+0x1BC0` 循环证明。",
        "- Canonical 归一化仅用于事后比较，不参与候选建立；JSON 同时保存原始动作 Skill ID。",
        "- 严格可新增覆盖只统计同一对象代次已有独立真值且零冲突的旧非确定性 Hit。",
        "",
        "## 11个未达95%职业",
        "",
        "|职业|基线|真值Hit|精确率|95%下界|零冲突代次新增命名Hit|仍需Hit|投影覆盖|达到95%|全部shadow命名候选|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    by_job = {row["job"]: row for row in summary["jobs"]}
    for job in report["under95_contract"]:
        row = by_job.get(job, {"job": job})
        projected = row.get("strict_projected_named_coverage_percent")
        lines.append(
            f"|{job}|{float(row.get('baseline_named_coverage_percent') or 0):.2f}%|"
            f"{int(row.get('truth_hits', 0)):,}|"
            f"{float(row.get('canonical_precision_percent', 0)):.2f}%|"
            f"{float(row.get('canonical_precision_wilson95_lower_percent', 0)):.2f}%|"
            f"{int(row.get('strict_same_generation_named_gap_hits', 0)):,}|"
            f"{int(row.get('baseline_hits_needed_for_95') or 0):,}|"
            f"{('-' if projected is None else f'{projected:.2f}%')}|"
            f"{('是' if row.get('strict_projected_reaches_95') else '否')}|"
            f"{int(row.get('all_candidate_named_gap_hits', 0)):,}|"
        )
    lines += [
        "",
        "## 冲突摘要",
        "",
        f"冲突对象代次：**{totals.get('generations_by_status', {}).get('conflict', 0):,}**；"
        f"无真值对象代次：**{totals.get('generations_by_status', {}).get('unvalidated', 0):,}**；"
        f"同代次真值零冲突：**{totals.get('generations_by_status', {}).get('validated_clean', 0):,}**。",
        "",
        "完整冲突、按技能和逐抓包对象代次保存在同目录 JSON；本工具未修改生产代码。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be 1..8")

    signature_before = _production_signature()
    selection_path = args.selection.resolve()
    selected, under95 = _selection(selection_path)
    paths = _find_captures(args.capture_root.resolve(), selected)
    reports: dict[str, dict[str, Any]] = {}
    if args.workers == 1:
        for index, path in enumerate(paths, 1):
            reports[path.name] = audit_capture(str(path))
            print(f"[{index}/{len(paths)}] {path.name}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(audit_capture, str(path)): path for path in paths}
            for index, future in enumerate(as_completed(futures), 1):
                path = futures[future]
                reports[path.name] = future.result()
                print(f"[{index}/{len(paths)}] {path.name}", flush=True)
    captures = [reports[row["name"]] for row in selected]
    for expected, actual in zip(selected, captures, strict=True):
        if actual["capture_sha256"] != expected["sha256"]:
            raise ValueError(f"capture changed during replay: {actual['capture']}")
    signature_after = _production_signature()
    if signature_after != signature_before:
        raise ValueError("production files changed during shadow audit")

    report = {
        "schema": "projectile-current-skill-copy-shadow-v52",
        "method": "offline replay only; no client execution; no production mutation",
        "candidate_contract": {
            "state": "real accepted SkillCastEvent updates actor.current_skill",
            "birth": "first complete direct37 for (capture, owner, damage_object_id)",
            "freeze": "candidate remains the birth current_skill for the whole observed object generation",
            "forbidden_candidate_inputs": [
                "skill display name",
                "damage amount/value",
                "existing inferred skill label",
                "packet/native skill slots",
                "nearest-cast time window",
            ],
            "accepted_action_kinds": sorted(ACCEPTED_ACTION_KINDS),
            "rejected_action_kinds": ["compact_skill_input", "inferred_combo"],
        },
        "truth_contract": {
            "methods": sorted(INDEPENDENT_TRUTH_METHODS),
            "excluded": [
                "packet_skill_id/native result skill slot",
                "damage-derived names",
                "damage value fingerprints",
                "timing/profile/old inference labels",
            ],
        },
        "comparison_normalization": {
            "purpose": "comparison only; never candidate creation",
            "aliases_sha256": sha256(
                json.dumps(
                    sorted((int(k), int(v)) for k, v in _LIVE_DAMAGE_SKILL_ALIASES.items()),
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest(),
        },
        "selection": str(selection_path),
        "selection_sha256": _hash_file(selection_path),
        "capture_root": str(args.capture_root.resolve()),
        "tool": str(Path(__file__).resolve()),
        "tool_sha256": _hash_file(Path(__file__).resolve()),
        "production_signature_before": signature_before,
        "production_signature_after": signature_after,
        "production_changed": False,
        "under95_contract": list(under95),
        "summary": aggregate(captures, under95),
        "captures": captures,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "projectile_skill_copy_shadow_v52.json"
    md_path = args.output_dir / "projectile_skill_copy_shadow_v52.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"]["totals"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
