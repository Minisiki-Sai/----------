from __future__ import annotations

import hashlib
import importlib.util
import marshal
from functools import lru_cache
from pathlib import Path
from typing import Iterable


FINGERPRINT_SCHEMA = "dragon-nest-version-fingerprint-v1"

_REPLAY_MODULES = (
    "dragon_nest_builder.live_dps.circle_swing_composition",
    "dragon_nest_builder.live_dps.circle_swing_forms",
    "dragon_nest_builder.live_dps.nightmare_sea_dragon",
    "dragon_nest_builder.live_dps.crippling_punisher_actions",
    "dragon_nest_builder.live_dps.holy_hammer_actions",
    "dragon_nest_builder.live_dps.aura_state",
    "dragon_nest_builder.live_dps.character_identity_sessions",
    "dragon_nest_builder.live_dps.client_native_skill_catalog",
    "dragon_nest_builder.live_dps.skill_plate_damage",
    "dragon_nest_builder.live_dps.player_root_action_objects",
    "dragon_nest_builder.live_dps.generated_player_root_action_signal_specs_v101",
    "dragon_nest_builder.live_dps.generated_player_root_full_action_signal_specs_v474",
    "dragon_nest_builder.live_dps.entity_damage_families",
    "dragon_nest_builder.live_dps.shock_damage_identity",
    "dragon_nest_builder.live_dps.compound_skill_responses",
    "dragon_nest_builder.live_dps.native_damage_skill_keys",
    "dragon_nest_builder.live_dps.settlement",
    "dragon_nest_builder.live_dps.clear_trigger",
    "dragon_nest_builder.live_dps.clear_reward_trigger",
    "dragon_nest_builder.data_loader",
    "dragon_nest_builder.effect_stacking",
    "dragon_nest_builder.team_support_rules",
    "dragon_nest_builder.live_dps.action_parser",
    "dragon_nest_builder.live_dps.aggregator",
    "dragon_nest_builder.live_dps.mechanic_damage",
    "dragon_nest_builder.live_dps.self_hp_cost",
    "dragon_nest_builder.live_dps.boss_action_v2",
    "dragon_nest_builder.live_dps.boss_hp",
    "dragon_nest_builder.live_dps.boss_health_wire",
    "dragon_nest_builder.live_dps.boss_recognizer",
    "dragon_nest_builder.live_dps.cast_timeline",
    "dragon_nest_builder.live_dps.damage_attribution",
    "dragon_nest_builder.live_dps.damage_object_origins",
    "dragon_nest_builder.live_dps.generated_attack_instance_specs_v576",
    "dragon_nest_builder.live_dps.damage_ledger",
    "dragon_nest_builder.live_dps.damage_wire_evidence",
    "dragon_nest_builder.live_dps.entity_damage_owner",
    "dragon_nest_builder.live_dps.field_effect_instances",
    "dragon_nest_builder.live_dps.instant_action_damage",
    "dragon_nest_builder.live_dps.skill_attribution_v2",
    "dragon_nest_builder.live_dps.udp_damage_records",
    "dragon_nest_builder.live_dps.udp_damage_identity",
    "dragon_nest_builder.live_dps.debuff_evidence",
    "dragon_nest_builder.live_dps.effects",
    "dragon_nest_builder.live_dps.event_ledger",
    "dragon_nest_builder.live_dps.healing_attribution",
    "dragon_nest_builder.live_dps.periodic_health_mechanism",
    "dragon_nest_builder.live_dps.inspect_catalog",
    "dragon_nest_builder.live_dps.models",
    "dragon_nest_builder.live_dps.nest_recognizer",
    "dragon_nest_builder.live_dps.profile",
    "dragon_nest_builder.live_dps.protocol_v576",
    "dragon_nest_builder.live_dps.tls_probe",
    "dragon_nest_builder.live_dps.capture_health",
    "dragon_nest_builder.live_dps.udp_direction",
    "dragon_nest_builder.live_dps.dungeon_map_catalog",
    "dragon_nest_builder.live_dps.sources",
    "dragon_nest_builder.live_dps.summon_presence",
    "dragon_nest_builder.live_dps.entity_owner_evidence",
    "dragon_nest_builder.live_dps.autonomous_attack_families",
    "dragon_nest_builder.live_dps.compact_entity_object_evidence",
    "dragon_nest_builder.live_dps.pve_scene_evidence",
    "dragon_nest_builder.live_dps.summon_attack_runtime",
    "dragon_nest_builder.live_dps.summon_self_attack_evidence_v26",
    "dragon_nest_builder.live_dps.generated_summon_self_attack_specs_v26",
    "dragon_nest_builder.live_dps.generated_mirror_self_attack_specs_v26",
    "dragon_nest_builder.live_dps.damage_object_v25_shaman_illusion",
    "dragon_nest_builder.live_dps.capture_sources",
    "dragon_nest_builder.live_dps.prebattle_state",
    "dragon_nest_builder.live_dps.replay_duplicate_hit_evidence",
    "dragon_nest_builder.live_dps.protocol_catalogs",
    "dragon_nest_builder.live_dps.replay_normalization",
    "dragon_nest_builder.live_dps.tcp_profile_codec",
    "dragon_nest_builder.live_dps.timeline_evidence",
    "dragon_nest_builder.live_dps.v576.actor_state",
    "dragon_nest_builder.live_dps.v576.combat_decoder",
    "dragon_nest_builder.live_dps.v576.core_decoder",
    "dragon_nest_builder.live_dps.v576.profile_decoder",
    "dragon_nest_builder.live_dps.v576.status_decoder",
)

_SCORE_MODULES = (
    "dragon_nest_builder.mixins.skill_timeline_effects",
    "dragon_nest_builder.live_dps.team_score_sync",
    "dragon_nest_builder.job_mechanics",
    "dragon_nest_builder.cooldown",
    "dragon_nest_builder.native_timeline",
    "dragon_nest_builder.mixins.skill_timeline_support",
    "dragon_nest_builder.mixins.skill_timeline_optimizer",
    "dragon_nest_builder.mixins.skill_timeline_baseline_policy",
    "dragon_nest_builder.mixins.timeline_runtime",
    "dragon_nest_builder.equipment_constants",
    "dragon_nest_builder.equipment_resonance",
    "dragon_nest_builder.live_dps.score_equipment_defaults",
    "dragon_nest_builder.live_dps.score_equipment",
    "dragon_nest_builder.live_dps.score_equipment_uniform",
    "dragon_nest_builder.calculator",
    "dragon_nest_builder.data_loader",
    "dragon_nest_builder.effect_stacking",
    "dragon_nest_builder.hp_conditional",
    "dragon_nest_builder.live_dps.aggregator",
    "dragon_nest_builder.live_dps.healing_attribution",
    "dragon_nest_builder.live_dps.periodic_health_mechanism",
    "dragon_nest_builder.live_dps.nest_recognizer",
    "dragon_nest_builder.live_dps.damage_ledger",
    "dragon_nest_builder.live_dps.damage_object_origins",
    "dragon_nest_builder.live_dps.field_effect_instances",
    "dragon_nest_builder.live_dps.instant_action_damage",
    "dragon_nest_builder.live_dps.skill_attribution_v2",
    "dragon_nest_builder.live_dps.generated_attack_instance_specs_v576",
    "dragon_nest_builder.live_dps.protocol_v576",
    "dragon_nest_builder.live_dps.score_engine",
    "dragon_nest_builder.live_dps.castability",
    "dragon_nest_builder.fury_planner",
    "dragon_nest_builder.live_dps.score_v2",
    "dragon_nest_builder.live_dps.score_body_policy",
    "dragon_nest_builder.live_dps.score_burst",
    "dragon_nest_builder.live_dps.calibration_boss_scope",
    "dragon_nest_builder.live_dps.skill_realization",
    "dragon_nest_builder.live_dps.hit_parallel",
    "dragon_nest_builder.live_dps.full_run_score",
    "dragon_nest_builder.live_dps.score_hit_evidence",
    "dragon_nest_builder.live_dps.score_glyphs",
    "dragon_nest_builder.live_dps.score_glyph_selection",
    "dragon_nest_builder.live_dps.score_glyph_objective",
    "dragon_nest_builder.live_dps.score_intervals",
    "dragon_nest_builder.live_dps.score_profiles",
    "dragon_nest_builder.live_dps.score_role_duties",
    "dragon_nest_builder.live_dps.healer_demand",
    "dragon_nest_builder.live_dps.healer_rescue",
    "dragon_nest_builder.live_dps.duty_groups",
    "dragon_nest_builder.live_dps.score_rotation",
    "dragon_nest_builder.live_dps.score_support",
    "dragon_nest_builder.live_dps.score_support_allocation",
    "dragon_nest_builder.live_dps.score_support_catalog",
    "dragon_nest_builder.live_dps.score_support_context",
    "dragon_nest_builder.live_dps.score_support_counterfactual",
    "dragon_nest_builder.live_dps.score_survival",
    "dragon_nest_builder.live_dps.scoring",
    "dragon_nest_builder.live_dps.cast_timeline",
    "dragon_nest_builder.live_dps.combat_glyph_inference",
    "dragon_nest_builder.live_dps.cooldown_glyph_evidence",
    "dragon_nest_builder.live_dps.combat_skill_levels",
    "dragon_nest_builder.live_dps.damage_attribution",
    "dragon_nest_builder.live_dps.timeline_evidence",
    "dragon_nest_builder.live_dps.v576.core_decoder",
    "dragon_nest_builder.live_dps.v576.status_decoder",
    "dragon_nest_builder.live_dps.theory_planner",
    "dragon_nest_builder.live_dps.theory_plan_builder",
    "dragon_nest_builder.live_dps.timing_ablation",
    "dragon_nest_builder.mechanism_timeline",
    "dragon_nest_builder.native.overlay_service",
    "dragon_nest_builder.native.replay_score_service",
    "dragon_nest_builder.profile_build_sync",
    "dragon_nest_builder.shared",
    "dragon_nest_builder.skill_adjustments",
    "dragon_nest_builder.skill_glyph_policy",
    "dragon_nest_builder.mixins.extreme_engine",
    "dragon_nest_builder.mixins.skill_build",
    "dragon_nest_builder.mixins.skill_simulation",
    "dragon_nest_builder.mixins.skill_timeline_engine",
    "dragon_nest_builder.mixins.skill_timeline_local_optimizer",
    "dragon_nest_builder.mixins.skill_timeline_output",
    "dragon_nest_builder.mixins.skill_timeline_policy_1_6",
    "dragon_nest_builder.mixins.skill_timeline_schedule",
    "dragon_nest_builder.mixins.skill_timeline_scheme_1",
    "dragon_nest_builder.mixins.skill_timeline_state",
    "dragon_nest_builder.mixins.team_effects",
    "dragon_nest_builder.mixins.team_practical",
    "dragon_nest_builder.mixins.timeline_presenter",
    "dragon_nest_builder.team_support_rules",
    "dragon_nest_builder.timeline_engine",
    "dragon_nest_builder.timeline_parallel",
    "dragon_nest_builder.timeline_parallel",
    "dragon_nest_builder.ui.live_dps_page",
    "dragon_nest_builder.v410_native_core",
    "dragon_nest_builder.v410_legacy_timeline",
)


def _update_file(digest: "hashlib._Hash", path: Path, label: str) -> None:
    digest.update(label.encode("utf-8", errors="surrogatepass"))
    digest.update(b"\0")
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(4 * 1024 * 1024):
                digest.update(chunk)
    except OSError:
        digest.update(b"<missing>")
    digest.update(b"\0")


@lru_cache(maxsize=8)
def module_code_fingerprint(module_names: tuple[str, ...]) -> str:
    """Hash importable bytecode, including frozen/PyInstaller modules."""

    digest = hashlib.sha256(FINGERPRINT_SCHEMA.encode("ascii"))
    for module_name in sorted(set(module_names)):
        digest.update(module_name.encode("utf-8"))
        digest.update(b"\0")
        try:
            spec = importlib.util.find_spec(module_name)
            loader = spec.loader if spec is not None else None
            get_code = getattr(loader, "get_code", None)
            code = get_code(module_name) if callable(get_code) else None
            if code is not None:
                digest.update(marshal.dumps(code))
            elif spec is not None and spec.origin:
                _update_file(digest, Path(spec.origin), module_name)
            else:
                digest.update(b"<unavailable>")
        except Exception as exc:
            digest.update(type(exc).__name__.encode("ascii", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _iter_data_files(data_root: Path) -> Iterable[Path]:
    for relative in ("live_dps_profiles", "modular"):
        directory = data_root / relative
        if directory.is_dir():
            yield from (
                path
                for path in sorted(directory.rglob("*"))
                if path.is_file() and path.suffix.casefold() in {".json", ".gz"}
            )
    for name in ("dragon_nest_data.json", "skill_adjustments.json"):
        path = data_root / name
        if path.is_file():
            yield path


@lru_cache(maxsize=16)
def _data_content_fingerprint_cached(
    data_root_text: str,
    manifest: tuple[tuple[str, int, int], ...],
) -> str:
    data_root = Path(data_root_text)
    digest = hashlib.sha256(FINGERPRINT_SCHEMA.encode("ascii"))
    for label, _size, _modified_ns in manifest:
        path = data_root / label
        _update_file(digest, path, label)
    return digest.hexdigest()


def data_content_fingerprint(data_root_text: str) -> str:
    """Hash rule-bearing data and notice replacements during one app run."""

    data_root = Path(data_root_text)
    manifest = tuple(
        (
            path.relative_to(data_root).as_posix(),
            int(path.stat().st_size),
            int(path.stat().st_mtime_ns),
        )
        for path in _iter_data_files(data_root)
    )
    return _data_content_fingerprint_cached(data_root_text, manifest)


@lru_cache(maxsize=16)
def _replay_decoder_fingerprint_cached(
    data_root_text: str, data_fingerprint: str
) -> str:
    digest = hashlib.sha256(b"replay-decoder-v1\0")
    digest.update(module_code_fingerprint(_REPLAY_MODULES).encode("ascii"))
    digest.update(data_fingerprint.encode("ascii"))
    return digest.hexdigest()


def replay_decoder_fingerprint(data_root_text: str) -> str:
    return _replay_decoder_fingerprint_cached(
        data_root_text, data_content_fingerprint(data_root_text)
    )


@lru_cache(maxsize=16)
def _team_score_rules_fingerprint_cached(
    data_root_text: str,
    replay_fingerprint: str,
    data_fingerprint: str,
) -> str:
    digest = hashlib.sha256(b"team-score-rules-v1\0")
    digest.update(replay_fingerprint.encode("ascii"))
    digest.update(module_code_fingerprint(_SCORE_MODULES).encode("ascii"))
    digest.update(data_fingerprint.encode("ascii"))
    return digest.hexdigest()


def team_score_rules_fingerprint(data_root_text: str) -> str:
    data_fingerprint = data_content_fingerprint(data_root_text)
    return _team_score_rules_fingerprint_cached(
        data_root_text,
        _replay_decoder_fingerprint_cached(data_root_text, data_fingerprint),
        data_fingerprint,
    )
