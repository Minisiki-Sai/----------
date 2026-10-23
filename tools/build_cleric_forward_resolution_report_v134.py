"""Build the evidence report for Paladin/Crusader forward attribution.

This tool only combines already generated, immutable capture audits.  It
never promotes a skill from elapsed time, damage amount, cadence, or an old
display label.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "cleric_forward_resolution_v134_20260910"
ACTION = (
    ROOT
    / "outputs"
    / "cleric_action_native_truth_v129_20260910"
    / "captures.json"
)
STATUS = (
    ROOT
    / "outputs"
    / "cleric_forward_status_v131_20260910"
    / "report.json"
)
SUMMARY = (
    ROOT
    / "outputs"
    / "cleric_forward_final_v134_20260910"
    / "profession_direct_damage_summary.csv"
)
HISTORIES = (
    ROOT
    / "outputs"
    / "cleric_ephemeral_object_history_v132_20260910"
    / "0821_crusader_3202_batch.json",
    ROOT
    / "outputs"
    / "cleric_ephemeral_object_history_v132_20260910"
    / "0830_paladin_3202_batch.json",
)
JOBS = ("圣骑士", "十字军")
ROUTES = (
    "object_birth_0e_spawn_signal",
    "object_birth_0e_action_lifetime",
    "hit_0e_direct_signal",
)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _route_stats(action_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for capture in action_rows:
        for row in capture["rows"]:
            job = str(row["job"])
            route = str(row["route"])
            if job not in JOBS or route not in ROUTES:
                continue
            truth = row.get("truth_skill_id")
            if truth:
                result = (
                    "match"
                    if int(row["candidate_skill_id"]) == int(truth)
                    else "conflict"
                )
            elif row.get("gap_target"):
                result = "gap_candidate"
            else:
                result = "no_truth"
            counts[(job, route, result)] += 1
    output = []
    for job in JOBS:
        for route in ROUTES:
            match = counts[(job, route, "match")]
            conflict = counts[(job, route, "conflict")]
            checked = match + conflict
            output.append(
                {
                    "job": job,
                    "route": route,
                    "truth_matches": match,
                    "truth_conflicts": conflict,
                    "truth_precision_percent": (
                        round(match * 100.0 / checked, 6) if checked else None
                    ),
                    "gap_candidates": counts[(job, route, "gap_candidate")],
                }
            )
    return output


def _history_result(path: Path) -> dict[str, object]:
    doc = _load(path)
    by_object: defaultdict[int, list[dict[str, object]]] = defaultdict(list)
    for row in doc["rows"]:
        for raw_object in row["occurrences"]:
            by_object[int(raw_object)].append(row)
    focus_owner = (
        1_332_324_584 if "crusader" in path.name else 1_335_778_524
    )
    first_is_focus_damage = 0
    focus_birth_before_damage = 0
    later_foreign_reuse = 0
    examples = []
    for object_id, rows in sorted(by_object.items()):
        rows.sort(key=lambda row: (int(row["elapsed_ms"]), int(row["op"])))
        first = rows[0]
        first_source = 0
        if int(first["op"]) == 8:
            raw = bytes.fromhex(str(first["record_hex"]))
            if len(raw) >= 16:
                first_source = int.from_bytes(raw[12:16], "little")
        is_focus_damage = int(first["op"]) == 8 and first_source == focus_owner
        first_is_focus_damage += int(is_focus_damage)
        first_damage_at = min(
            (
                int(row["elapsed_ms"])
                for row in rows
                if int(row["op"]) == 8
                and len(bytes.fromhex(str(row["record_hex"]))) >= 16
                and int.from_bytes(
                    bytes.fromhex(str(row["record_hex"]))[12:16], "little"
                )
                == focus_owner
            ),
            default=None,
        )
        if first_damage_at is not None:
            focus_birth_before_damage += int(
                any(
                    int(row["op"]) == 9
                    and int(row["actor"]) == focus_owner
                    and int(row["elapsed_ms"]) < first_damage_at
                    for row in rows
                )
            )
            later_foreign_reuse += int(
                any(
                    int(row["op"]) == 9
                    and int(row["actor"]) != focus_owner
                    and int(row["elapsed_ms"]) > first_damage_at
                    for row in rows
                )
            )
        if len(examples) < 4:
            examples.append(
                {
                    "damage_object_id": object_id,
                    "first_elapsed_ms": int(first["elapsed_ms"]),
                    "first_record_type": int(first["op"]),
                    "first_damage_source": first_source,
                    "first_actor_field": int(first["actor"]),
                }
            )
    return {
        "capture": Path(doc["capture"]).name,
        "focus_owner_actor_id": focus_owner,
        "objects": len(by_object),
        "first_seen_as_focus_damage": first_is_focus_damage,
        "focus_owner_birth_before_first_damage": focus_birth_before_damage,
        "later_reused_by_foreign_object_birth": later_foreign_reuse,
        "examples": examples,
    }


def _coverage_rows() -> list[dict[str, object]]:
    with SUMMARY.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [row for row in rows if row["职业"] in JOBS]


def main() -> None:
    inputs = (ACTION, STATUS, SUMMARY, *HISTORIES)
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError("\n".join(missing))
    action = _load(ACTION)
    status = _load(STATUS)
    routes = _route_stats(action)
    histories = [_history_result(path) for path in HISTORIES]
    coverage = _coverage_rows()
    report = {
        "schema": "cleric-forward-resolution-v134",
        "policy": {
            "allowed": "skill/action -> typed object/status generation -> physical damage record",
            "forbidden": [
                "elapsed-time skill naming",
                "damage-amount matching",
                "hit-cadence matching",
                "legacy label as truth",
                "capture-global object-id propagation",
            ],
        },
        "inputs": {str(path.relative_to(ROOT)): _hash(path) for path in inputs},
        "route_validation": routes,
        "ephemeral_result_object_batches": histories,
        "status_evidence": status["captures"],
        "current_coverage": coverage,
        "verdict": {
            "best_route": "typed object birth + owner/object/generation + static Projectile/ACT skill",
            "actor_action_state": (
                "0E proves START_OR_REPLACE_REQUEST, not successful current-pair commit; "
                "no replayable CLEAR record was observed in the fixed captures"
            ),
            "status_state": (
                "3202/3205 status responses prove START/REFRESH/expiry, but their "
                "skill-less direct37 rows do not repeat effect_instance_id"
            ),
            "paladin_over_95_hit": False,
            "crusader_over_95_hit": False,
            "reason": (
                "Promoting action requests or status lifetime alone would contradict "
                "independent packet/object truth and relabel real overlapping hits."
            ),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    route_map = {(row["job"], row["route"]): row for row in routes}
    coverage_map = {row["职业"]: row for row in coverage}
    lines = [
        "# 圣骑士/十字军正向伤害归因审计 v134",
        "",
        "## 结论",
        "",
        "从技能释放向后追踪是正确主线，但必须按对象代次或状态代次闭合。",
        "普通 Projectile 对象链精度最高；Actor 当前动作请求和状态存活区间不能单独决定每个 Hit 的技能名。",
        "",
        "## 三条路线实测",
        "",
        "|职业|路线|真值一致|真值冲突|精度|可覆盖现有缺口|",
        "|---|---|---:|---:|---:|---:|",
    ]
    labels = {
        "object_birth_0e_spawn_signal": "动作内出生信号→对象代次",
        "object_birth_0e_action_lifetime": "对象出生落在动作存活期",
        "hit_0e_direct_signal": "伤害发生时当前0E动作",
    }
    for job in JOBS:
        for route in ROUTES:
            row = route_map[(job, route)]
            precision = (
                "—"
                if row["truth_precision_percent"] is None
                else f"{row['truth_precision_percent']:.3f}%"
            )
            lines.append(
                f"|{job}|{labels[route]}|{row['truth_matches']}|"
                f"{row['truth_conflicts']}|{precision}|{row['gap_candidates']}|"
            )
    lines += [
        "",
        "## 临时 result object 实证",
        "",
        "|抓包|检查对象|首次即为本角色伤害|本角色伤害前有出生|以后被其他对象出生复用|",
        "|---|---:|---:|---:|---:|",
    ]
    for row in histories:
        lines.append(
            f"|{row['capture']}|{row['objects']}|{row['first_seen_as_focus_damage']}|"
            f"{row['focus_owner_birth_before_first_damage']}|"
            f"{row['later_reused_by_foreign_object_birth']}|"
        )
    lines += [
        "",
        "## 当前严格确定性覆盖",
        "",
        "|职业|总直接Hit|确定Hit|Hit覆盖率|确定伤害覆盖率|距95%还差Hit|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for job in JOBS:
        row = coverage_map[job]
        lines.append(
            f"|{job}|{row['总直接Hit']}|{row['确定性已归因']}|"
            f"{float(row['确定性覆盖率%']):.3f}%|"
            f"{float(row['确定性伤害覆盖率%']):.3f}%|{row['距离95%确定性Hit']}|"
        )
    lines += [
        "",
        "## Actor+0x6B0/+0x6B8 状态结论",
        "",
        "- 已能解出 `0E` 的技能 RowId/等级请求，并静态确认成功分支会 START 或 REPLACE 当前动作对。",
        "- 抓包没有携带可证明该请求实际提交成功的当前动作对象句柄；旧 Projectile、场地和状态伤害可与新动作并发。",
        "- 固定语料没有出现可独立回放的 `66/67/68` CLEAR 记录；客户端 CLEAR 还受动作 subtype、句柄相等和队列状态约束。",
        "- 因而 Actor 动作状态适合校验已闭合对象链，不适合单独补技能名。",
        "",
        "## 本轮生产行为",
        "",
        "已保存 `神圣惩戒 EX` 的服务器状态代次（effect class 62、实例 ID、START/刷新/到期），但未用它强行重命名 Hit。A/B 重放中总 Hit、总伤害和每条现有技能归属完全不变。",
        "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(OUT / "REPORT.md")


if __name__ == "__main__":
    main()
