from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DataStore, norm  # noqa: E402
from dragon_nest_builder.hit_analyzer.analyzer import (  # noqa: E402
    ANALYZER_VERSION,
    HitObservation,
    SkillHitAnalyzer,
    SkillHitTemplateLibrary,
    skill_family,
    skill_variant,
    summarize_observations,
)


_WORKER_ANALYZER: SkillHitAnalyzer | None = None

_SIGNATURE_CODE_FILES = (
    "dragon_nest_builder/hit_analyzer/analyzer.py",
    "dragon_nest_builder/hit_analyzer/evidence.py",
    "dragon_nest_builder/live_dps/capture_sources.py",
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/v576/combat_decoder.py",
    "dragon_nest_builder/live_dps/v576/status_decoder.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/generated_attack_instance_specs_v576.py",
)
_SIGNATURE_DATA_GLOBS = (
    "dragon_nest_builder/data/live_dps_profiles/*.json",
    "dragon_nest_builder/data/modular/tables/skills/*.json",
    "dragon_nest_builder/data/modular/tables/skills/skill_damage_tiny_jobs/*.json",
)


def _analysis_signature(parameters: dict[str, object]) -> str:
    """Fingerprint every semantic input that can change corpus results."""

    digest = hashlib.sha256(b"skill-hit-analyzer-signature-v5\0")
    paths = [ROOT / relative for relative in _SIGNATURE_CODE_FILES]
    for pattern in _SIGNATURE_DATA_GLOBS:
        paths.extend(sorted(ROOT.glob(pattern)))
    for path in sorted({item.resolve() for item in paths}):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(ROOT).as_posix()
        except ValueError:
            relative = str(path)
        digest.update(relative.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    digest.update(
        json.dumps(
            parameters,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return digest.hexdigest()


def _worker_init() -> None:
    global _WORKER_ANALYZER
    _WORKER_ANALYZER = SkillHitAnalyzer(DataStore.load())


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_gzip_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(path)


def _read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"invalid shard: {path}")
    return value


def _analyze_one(
    path_text: str,
    relative_name: str,
    combat_content_id: str,
    evidence_revision_id: str,
    analysis_signature: str,
    shard_text: str,
) -> dict[str, object]:
    global _WORKER_ANALYZER
    if _WORKER_ANALYZER is None:
        _worker_init()
    assert _WORKER_ANALYZER is not None
    path = Path(path_text)
    shard_path = Path(shard_text)
    started = time.perf_counter()
    rows = _WORKER_ANALYZER.analyze_dpscap(path)
    rows = [replace(row, source_file=relative_name) for row in rows]
    diagnostics = dict(_WORKER_ANALYZER.last_diagnostics)
    diagnostics.update(
        {
            "path": path_text,
            "relative_path": relative_name,
            "combat_content_id": combat_content_id,
            "evidence_revision_id": evidence_revision_id,
            "analysis_signature": analysis_signature,
            "size_bytes": path.stat().st_size,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    )
    payload = {
        "schema_version": ANALYZER_VERSION,
        "analysis_signature": analysis_signature,
        "combat_content_id": combat_content_id,
        "evidence_revision_id": evidence_revision_id,
        "source": relative_name,
        "diagnostics": diagnostics,
        "observations": [row.to_mapping() for row in rows],
    }
    _write_gzip_json(shard_path, payload)
    return diagnostics


def _valid_shard(
    path: Path,
    *,
    analysis_signature: str,
    evidence_revision_id: str,
) -> bool:
    if not path.exists():
        return False
    try:
        payload = _read_gzip_json(path)
    except (OSError, EOFError, gzip.BadGzipFile, ValueError, TypeError):
        return False
    return (
        int(payload.get("schema_version", 0) or 0) == ANALYZER_VERSION
        and payload.get("analysis_signature") == analysis_signature
        and payload.get("evidence_revision_id") == evidence_revision_id
    )


def _load_observations(
    shards: list[Path],
    *,
    analysis_signature: str,
) -> tuple[list[HitObservation], list[dict[str, object]]]:
    observations: list[HitObservation] = []
    diagnostics: list[dict[str, object]] = []
    for shard in shards:
        payload = _read_gzip_json(shard)
        if int(payload.get("schema_version", 0) or 0) != ANALYZER_VERSION:
            raise ValueError(f"stale analyzer schema in shard: {shard}")
        if payload.get("analysis_signature") != analysis_signature:
            raise ValueError(f"stale semantic signature in shard: {shard}")
        row_diagnostics = payload.get("diagnostics", {})
        if isinstance(row_diagnostics, dict):
            diagnostics.append(dict(row_diagnostics))
        for value in payload.get("observations", ()):
            if not isinstance(value, dict):
                continue
            try:
                observations.append(HitObservation.from_mapping(value))
            except (TypeError, ValueError):
                continue
    return observations, diagnostics


def _coverage_rows(
    store: DataStore,
    grouped: dict[str, list[HitObservation]],
) -> list[dict[str, object]]:
    all_observations = [row for rows in grouped.values() for row in rows]
    # Coverage means that a configured skill variant has at least one factual
    # Hit.  A reliable action with zero Hit, an auxiliary pulse, or an old row
    # without the explicit real-Hit bit is valuable audit evidence, but it must
    # not make the skill look covered.
    exact_observed = {
        (norm(row.job), skill_variant(row.skill_name, row.skill_id))
        for row in all_observations
    }
    exact_real_hit = {
        (norm(row.job), skill_variant(row.skill_name, row.skill_id))
        for row in all_observations
        if row.hit_count - row.legacy_hit_evidence_count > 0
    }
    family_real_hit = {
        (norm(row.job), skill_family(row.skill_name, row.skill_id))
        for row in all_observations
        if row.hit_count - row.legacy_hit_evidence_count > 0
    }
    exact_training = {
        (norm(row.job), skill_variant(row.skill_name, row.skill_id))
        for row in all_observations
        if row.training_eligible and row.hit_count > 0
    }
    result: list[dict[str, object]] = []
    for first_job in store.first_jobs():
        for second_job in store.second_jobs(first_job):
            for skill_name in store.skill_damage_names(second_job, mode="both"):
                if norm(skill_name) in {"", "无", "-"}:
                    continue
                exact_key = (norm(second_job), skill_variant(skill_name))
                family_key = (norm(second_job), skill_family(skill_name))
                has_real_hit = exact_key in exact_real_hit
                has_training = exact_key in exact_training
                if has_training:
                    status = "已有可训练同变体样本"
                elif has_real_hit:
                    status = "已有同变体真实Hit（仅发现）"
                elif family_key in family_real_hit:
                    status = "仅有同族/另一变体真实Hit"
                elif exact_key in exact_observed:
                    status = "仅有动作/辅助脉冲，无真实Hit"
                else:
                    status = "暂无真实Hit样本"
                result.append(
                    {
                        "first_job": first_job,
                        "job": second_job,
                        "skill_name": skill_name,
                        "skill_variant": exact_key[1],
                        "skill_family": family_key[1],
                        "status": status,
                        "has_same_variant_real_hit": has_real_hit,
                        "has_same_variant_training_sample": has_training,
                    }
                )
    return result


def _build_outputs(
    output_dir: Path,
    shard_paths: list[Path],
    duplicates: list[dict[str, str]],
    errors: list[dict[str, str]],
    started_at: str,
    analysis_signature: str,
) -> dict[str, object]:
    observations, diagnostics = _load_observations(
        shard_paths,
        analysis_signature=analysis_signature,
    )
    grouped: dict[str, list[HitObservation]] = defaultdict(list)
    for row in observations:
        grouped[row.profile_key].append(row)
    summaries = [
        summarize_observations(rows)
        for _key, rows in sorted(
            grouped.items(),
            key=lambda item: (
                norm(item[1][0].job) if item[1] else "",
                norm(item[1][0].skill_name) if item[1] else "",
            ),
        )
        if rows
    ]
    store = DataStore.load()
    coverage = _coverage_rows(store, grouped)
    generated_at = datetime.now().isoformat(timespec="seconds")
    total_selected_hits = sum(row.hit_count for row in observations)
    legacy_selected_hits = sum(row.legacy_hit_evidence_count for row in observations)
    total_strict_real_hits = max(0, total_selected_hits - legacy_selected_hits)
    conservation_failures: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        conservation = diagnostic.get("real_hit_event_conservation", {})
        if not isinstance(conservation, dict):
            continue
        inputs = int(conservation.get("input", 0) or 0)
        assigned = int(conservation.get("assigned", 0) or 0)
        unassigned = int(conservation.get("unassigned", 0) or 0)
        if inputs != assigned + unassigned:
            conservation_failures.append(
                {
                    "relative_path": diagnostic.get("relative_path", ""),
                    "input": inputs,
                    "assigned": assigned,
                    "unassigned": unassigned,
                }
            )

    def review_gate(row: object) -> bool:
        return bool(
            not row.persistent
            and row.training_sample_count >= 15
            and row.file_count >= 3
            and row.actor_count >= 2
            and row.balanced_top_share >= 0.67
            and row.balanced_margin >= 0.20
            and row.cross_file_consistency >= 0.67
            and row.target_consistency >= 0.50
            and row.hit_count > 0
        )

    review_candidates = [row for row in summaries if review_gate(row)]
    persistent_skills = [row for row in summaries if row.persistent]
    discovery_only = [
        row
        for row in summaries
        if not row.persistent and row.training_sample_count == 0
    ]
    quarantine = [
        row
        for row in summaries
        if not row.persistent
        and row.training_sample_count > 0
        and not review_gate(row)
    ]

    hit_count_distribution = [
        {
            "key": row.key,
            "job": row.job,
            "skill_name": row.skill_name,
            "hit_count": hit_count,
            "capture_balanced_share": share,
            "is_selected_standard": hit_count == row.hit_count,
            "is_capture_balanced_mode": hit_count == row.modal_hit_count,
        }
        for row in summaries
        for hit_count, share in row.capture_balanced_hit_counts
    ]
    position_rows = [
        {
            "key": row.key,
            "job": row.job,
            "skill_name": row.skill_name,
            "candidate_hit_count": row.hit_count if review_gate(row) else None,
            "observed_modal_hit_count": row.modal_hit_count,
            **asdict(position),
        }
        for row in summaries
        for position in row.positions
    ]
    interval_rows = [
        {
            "key": row.key,
            "job": row.job,
            "skill_name": row.skill_name,
            "from_hit": max(1, position.index - 1),
            "to_hit": position.index,
            "interval_seconds": position.interval_seconds,
            "interval_p10": position.interval_p10,
            "interval_p90": position.interval_p90,
            "samples": position.samples,
        }
        for row in summaries
        for position in row.positions
        if position.interval_seconds is not None
    ]
    unassigned_rows = [
        {
            "relative_path": diagnostic.get("relative_path", ""),
            "real_hit_events": diagnostic.get("real_hit_events", 0),
            "unassigned_real_hits": diagnostic.get("unassigned_real_hits", 0),
            "unassigned_resolved_real_hits": diagnostic.get(
                "unassigned_resolved_real_hits", 0
            ),
            "unassigned_unresolved_real_hits": diagnostic.get(
                "unassigned_unresolved_real_hits", 0
            ),
            "ambiguous_cast_instances": diagnostic.get(
                "ambiguous_cast_instances", 0
            ),
            "unassigned_reasons": diagnostic.get("unassigned_reasons", {}),
        }
        for diagnostic in diagnostics
    ]

    profession_summary: list[dict[str, object]] = []
    jobs = sorted({row.job for row in summaries if row.job})
    for job in jobs:
        job_summaries = [row for row in summaries if row.job == job]
        job_observations = [row for row in observations if row.job == job]
        job_coverage = [row for row in coverage if row["job"] == job]
        profession_summary.append(
            {
                "job": job,
                "captures": len(
                    {row.source_file for row in job_observations if row.source_file}
                ),
                "actors": len(
                    {
                        (row.source_file, row.source_id)
                        for row in job_observations
                        if row.source_id
                    }
                ),
                "skill_variants_observed": len(job_summaries),
                "configured_damage_skills": len(job_coverage),
                "configured_skills_without_samples": sum(
                    not row["has_same_variant_real_hit"] for row in job_coverage
                ),
                "configured_skills_with_real_hits": sum(
                    row["has_same_variant_real_hit"] for row in job_coverage
                ),
                "configured_skills_with_training_samples": sum(
                    row["has_same_variant_training_sample"] for row in job_coverage
                ),
                "observations": len(job_observations),
                "training_observations": sum(
                    row.training_eligible for row in job_observations
                ),
                "review_candidates": sum(review_gate(row) for row in job_summaries),
                "persistent_skills": sum(row.persistent for row in job_summaries),
                "discovery_only_skills": sum(
                    row.training_sample_count == 0 and not row.persistent
                    for row in job_summaries
                ),
            }
        )

    configured_jobs = {
        norm(row["job"])
        for row in coverage
        if norm(row.get("job"))
    }
    observed_jobs = {norm(row.job) for row in observations if norm(row.job)}
    overview = {
        "analyzer_version": ANALYZER_VERSION,
        "analysis_signature": analysis_signature,
        "started_at": started_at,
        "generated_at": generated_at,
        "captures_analyzed": len(diagnostics),
        "duplicate_captures_skipped": len(duplicates),
        "capture_errors": len(errors),
        "capture_bytes": sum(int(row.get("size_bytes", 0) or 0) for row in diagnostics),
        "professions_observed": len({row.job for row in observations if row.job}),
        "configured_second_jobs": len(configured_jobs),
        "configured_second_jobs_observed": len(configured_jobs & observed_jobs),
        "unmapped_job_labels": sorted(observed_jobs - configured_jobs),
        "skill_variants_observed": len(summaries),
        "cast_observations": len(observations),
        "template_eligible_observations": sum(
            row.training_eligible for row in observations
        ),
        "single_target_real_hits_in_observations": total_strict_real_hits,
        "single_target_strict_real_hits_in_observations": total_strict_real_hits,
        "legacy_hit_events_excluded_from_real_hit_totals": legacy_selected_hits,
        "damage_records_in_observations": sum(
            row.damage_record_count for row in observations
        ),
        "target_results_in_observations": sum(
            row.target_result_count for row in observations
        ),
        "decoded_real_hit_events": sum(
            int(row.get("real_hit_events", 0) or 0) for row in diagnostics
        ),
        "auxiliary_damage_events_excluded": sum(
            row.auxiliary_event_count for row in observations
        ),
        "circular_hit_events_quarantined": sum(
            row.circular_hit_count for row in observations
        ),
        "review_candidate_skills": len(review_candidates),
        "persistent_cadence_skills": len(persistent_skills),
        "quarantined_skills": len(quarantine),
        "discovery_only_skills": len(discovery_only),
        "event_conservation_failures": len(conservation_failures),
        "configured_damage_skills": len(coverage),
        "configured_skills_with_real_hits": sum(
            row["has_same_variant_real_hit"] for row in coverage
        ),
        "configured_skills_with_training_samples": sum(
            row["has_same_variant_training_sample"] for row in coverage
        ),
        "configured_skills_without_samples": sum(
            not row["has_same_variant_real_hit"] for row in coverage
        ),
        "high_confidence_whole_profile_skills": sum(
            row.status == "高置信候选" for row in summaries
        ),
    }
    report_payload = {
        "schema_version": ANALYZER_VERSION,
        "overview": overview,
        "definitions": {
            "hit": "只有 has_real_hit_evidence=True 且有独立技能归属证据的事件才可训练；None 仅供发现，False 为辅助脉冲",
            "four_counts": "分别保存伤害记录、目标Hit结果、单目标主技能Hit与辅助脉冲，禁止混成一个Hit数",
            "excluded": "额外伤害、状态伤害、持续伤害和召唤物伤害不会单独证明一次施放；仍保留在发现/节奏审计中",
            "direct_hit_overflow": "它是实际命中结果，但当前归属来自旧模板时具有循环依赖，因此计入事实总数、不用于训练模板",
            "variant": "普通、EX及其他变体不再因去除 EX 后缀而合并",
            "persistent": "持续/召唤技能输出脉冲节奏，不宣称固定 Hit 总数",
            "capture_balance": "每份抓包对Hit数、时间和伤害占比各贡献一票，长场次不会压过短场次",
            "standard_hit": "非持续技能使用跨至少两份抓包或两个角色、至少3次重复且抓包平衡支持率≥5%的最大Hit上界；较少Hit视为可能未打满，孤立更大值只保留审计",
            "strict_attribution": "结果组继承、最近动作、learned profile和其他时序推断不得训练Hit模板；只接受包内技能ID、已验证对象链及明确实体/状态所有者",
            "target_selection": "保留每目标Hit直方图；优先完整最大生命轨迹，再按轨迹长度与伤害选择，部分目标ID样本不训练",
            "confidence": "未使用人工金标准校准，数值仅为证据质量分，不是正确率概率",
            "review_gate": "仅使用已排除循环归因的训练子集：至少15个样本、3份抓包、2个角色、主峰占比≥67%、领先次峰≥20%、跨文件一致性≥67%、目标一致性≥50%；其他循环样本只保留审计，不参与候选统计",
            "coverage": "配置覆盖只承认同一职业、同一技能变体且带明确真实Hit证据的样本；零Hit动作、辅助脉冲、旧格式未知Hit位及另一变体均不算覆盖",
            "deployment": "本次只生成候选模板，不自动改写生产识别规则",
        },
        "profession_summary": profession_summary,
        "skills": [asdict(row) for row in summaries],
        "hit_count_distribution": hit_count_distribution,
        "positions": position_rows,
        "intervals": interval_rows,
        "unassigned": unassigned_rows,
        "quality_gates": {
            "review_candidates": [row.key for row in review_candidates],
            "persistent_cadence": [row.key for row in persistent_skills],
            "quarantine": [row.key for row in quarantine],
            "discovery_only": [row.key for row in discovery_only],
            "event_conservation_failures": conservation_failures,
        },
        "coverage": coverage,
        "files": sorted(
            diagnostics, key=lambda row: norm(row.get("relative_path"))
        ),
        "duplicates": duplicates,
        "errors": errors,
    }
    _write_json(output_dir / "hit_corpus_report.json", report_payload)

    def compact_entry(row: object, *, recommendation: bool) -> dict[str, object]:
        display_status = row.status
        if recommendation:
            display_status = (
                "人工复核候选（循环样本已隔离）"
                if row.circular_hit_count
                else "人工复核候选"
            )
        return {
            "job": row.job,
            "skill_name": row.skill_name,
            "skill_family": row.skill_family,
            "skill_id": row.skill_id,
            "recommended_hit_count": row.hit_count if recommendation else None,
            "observed_modal_hit_count": row.modal_hit_count,
            "observed_max_hit_count": row.observed_max_hit_count,
            "full_hit_sample_count": row.full_hit_sample_count,
            "full_hit_file_count": row.full_hit_file_count,
            "full_hit_actor_count": row.full_hit_actor_count,
            "full_hit_support_share": row.full_hit_support_share,
            "hit_count_selection": row.hit_count_selection,
            "sample_count": row.sample_count,
            "training_sample_count": row.training_sample_count,
            "file_count": row.file_count,
            "actor_count": row.actor_count,
            "capture_balanced_hit_counts": row.capture_balanced_hit_counts,
            "balanced_top_share": row.balanced_top_share,
            "balanced_margin": row.balanced_margin,
            "cross_file_consistency": row.cross_file_consistency,
            "target_consistency": row.target_consistency,
            "confidence_evidence_score": row.confidence,
            "status": display_status,
            "aggregate_status": row.status,
            "circular_hit_count": row.circular_hit_count,
            "decision": (
                "review_candidate_from_non_circular_training_subset"
                if recommendation
                else "no_fixed_hit_recommendation"
            ),
            "persistent": row.persistent,
            "positions": [asdict(position) for position in row.positions],
            "auxiliary_interval_seconds": row.auxiliary_interval_seconds,
            "selection_basis": row.selection_basis,
        }

    compact_templates = {
        "schema_version": ANALYZER_VERSION,
        "analysis_signature": analysis_signature,
        "generated_at": generated_at,
        "status": "candidate_only",
        "promoted_templates": {},
        "review_candidates": {
            row.key: compact_entry(row, recommendation=True)
            for row in review_candidates
        },
        "persistent_cadence": {
            row.key: compact_entry(row, recommendation=False)
            for row in persistent_skills
        },
        "quarantine": {
            row.key: compact_entry(row, recommendation=False)
            for row in quarantine
        },
        "discovery_only": {
            row.key: compact_entry(row, recommendation=False)
            for row in discovery_only
        },
    }
    _write_json(
        output_dir
        / f"skill_hit_templates_v{ANALYZER_VERSION}.candidate.json",
        compact_templates,
    )
    lines = [
        f"# 全职业技能 Hit 语料审计 V{ANALYZER_VERSION}",
        "",
        f"- 分析器版本：{ANALYZER_VERSION}",
        f"- 成功解析：{overview['captures_analyzed']} 份",
        f"- 跳过重复：{overview['duplicate_captures_skipped']} 份",
        f"- 失败：{overview['capture_errors']} 份",
        f"- 覆盖职业：{overview['professions_observed']} 个",
        f"- 观察到技能变体：{overview['skill_variants_observed']} 个",
        f"- 施放/脉冲样本：{overview['cast_observations']} 个",
        f"- 可训练观察：{overview['template_eligible_observations']} 个",
        f"- 达到人工复核门槛的技能：{overview['review_candidate_skills']} 个",
        f"- 持续/召唤节奏技能：{overview['persistent_cadence_skills']} 个",
        f"- 隔离技能：{overview['quarantined_skills']} 个",
        f"- 仅发现技能：{overview['discovery_only_skills']} 个",
        f"- 排除的非真实 Hit 伤害事件：{overview['auxiliary_damage_events_excluded']} 条",
        f"- 配置技能严格真实Hit覆盖：{overview['configured_skills_with_real_hits']}/{overview['configured_damage_skills']}",
        f"- 配置技能拥有严格训练样本：{overview['configured_skills_with_training_samples']}/{overview['configured_damage_skills']}",
        f"- 事件守恒失败：{overview['event_conservation_failures']} 份",
        "",
        "候选模板不会自动写入实战识别；必须经过人工核对或独立回归后再晋级。",
    ]
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a resumable, evidence-aware skill Hit corpus from .dpscap files."
    )
    parser.add_argument("capture_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    capture_root = args.capture_root.resolve()
    output_dir = args.output.resolve()
    shard_dir = output_dir / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(capture_root.rglob("*.dpscap"))
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        raise SystemExit(f"no .dpscap files under {capture_root}")

    started_at = datetime.now().isoformat(timespec="seconds")
    signature_parameters = {
        "analyzer_version": ANALYZER_VERSION,
        "capture_root_kind": "recursive_dpscap",
        "target_track_policy": "max_hp_then_longest_then_damage_v5",
        "decoder_instance_binding": True,
        "per_capture_balancing": True,
        "standard_hit_policy": "independently_supported_upper_envelope_v1",
        "strict_skill_attribution": True,
        "candidate_only": True,
    }
    analysis_signature = _analysis_signature(signature_parameters)
    fingerprinted: list[tuple[Path, str, str, str]] = []
    duplicates: list[dict[str, str]] = []
    print(
        f"[hash] {len(paths)} captures signature={analysis_signature[:12]}",
        flush=True,
    )
    for index, path in enumerate(paths, 1):
        combat_content_id, evidence_revision_id = (
            SkillHitTemplateLibrary.file_fingerprints(path)
        )
        relative = str(path.relative_to(capture_root))
        fingerprinted.append(
            (path, relative, combat_content_id, evidence_revision_id)
        )
        if index % 20 == 0 or index == len(paths):
            print(f"[hash] {index}/{len(paths)}", flush=True)

    by_combat: dict[str, list[tuple[Path, str, str, str]]] = defaultdict(list)
    for row in fingerprinted:
        by_combat[row[2]].append(row)
    selected: list[tuple[Path, str, str, str]] = []
    for combat_content_id, rows in sorted(by_combat.items()):
        # Duplicates share combat frames.  Prefer the largest/newest file,
        # because it normally carries the richest appended identity evidence.
        chosen = max(
            rows,
            key=lambda item: (
                item[0].stat().st_size,
                item[0].stat().st_mtime_ns,
                item[1],
            ),
        )
        selected.append(chosen)
        for row in rows:
            if row == chosen:
                continue
            duplicates.append(
                {
                    "path": row[1],
                    "duplicate_of": chosen[1],
                    "combat_content_id": combat_content_id,
                    "evidence_revision_id": row[3],
                    "selected_evidence_revision_id": chosen[3],
                }
            )

    unique: list[tuple[Path, str, str, str, Path]] = []
    for path, relative, combat_content_id, evidence_revision_id in selected:
        shard = shard_dir / (
            f"{combat_content_id[:32]}-{evidence_revision_id[:12]}-"
            f"{analysis_signature[:12]}.json.gz"
        )
        unique.append(
            (path, relative, combat_content_id, evidence_revision_id, shard)
        )

    pending = [
        row
        for row in unique
        if args.force
        or not _valid_shard(
            row[4],
            analysis_signature=analysis_signature,
            evidence_revision_id=row[3],
        )
    ]
    done_shards = [
        row[4]
        for row in unique
        if not args.force
        and _valid_shard(
            row[4],
            analysis_signature=analysis_signature,
            evidence_revision_id=row[3],
        )
    ]
    errors: list[dict[str, str]] = []
    completed = 0
    print(
        f"[analyze] unique={len(unique)} pending={len(pending)} "
        f"resume={len(done_shards)} workers={max(1, args.workers)}",
        flush=True,
    )
    if pending:
        with ProcessPoolExecutor(
            max_workers=max(1, args.workers), initializer=_worker_init
        ) as pool:
            futures = {
                pool.submit(
                    _analyze_one,
                    str(path),
                    relative,
                    combat_content_id,
                    evidence_revision_id,
                    analysis_signature,
                    str(shard),
                ): (relative, shard)
                for (
                    path,
                    relative,
                    combat_content_id,
                    evidence_revision_id,
                    shard,
                ) in pending
            }
            for future in as_completed(futures):
                relative, shard = futures[future]
                completed += 1
                try:
                    diagnostics = future.result()
                    done_shards.append(shard)
                    print(
                        f"[analyze] {completed}/{len(pending)} {relative} "
                        f"obs={diagnostics.get('observations', 0)} "
                        f"eligible={diagnostics.get('eligible_observations', 0)} "
                        f"sec={diagnostics.get('elapsed_seconds', 0)}",
                        flush=True,
                    )
                except Exception as exc:
                    errors.append({"path": relative, "error": repr(exc)})
                    print(
                        f"[error] {completed}/{len(pending)} {relative}: {exc!r}",
                        flush=True,
                    )

    report = _build_outputs(
        output_dir,
        sorted(done_shards),
        duplicates,
        errors,
        started_at,
        analysis_signature,
    )
    overview = report["overview"]
    print(
        "[done] "
        f"captures={overview['captures_analyzed']} "
        f"jobs={overview['professions_observed']} "
        f"skills={overview['skill_variants_observed']} "
        f"observations={overview['cast_observations']} "
        f"errors={overview['capture_errors']}",
        flush=True,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
