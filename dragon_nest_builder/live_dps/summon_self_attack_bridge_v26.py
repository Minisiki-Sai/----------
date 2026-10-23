"""Opt-in metadata-only integration bridge. Does not create player attacks.

Main must inject a context provider from its physical-hit and guarded owner
ledgers. A missing provider/catalog/PVE mode/schema disables the bridge. The
provider must return the CURRENT life, never a cached pre-conflict snapshot.
"""
from __future__ import annotations
from dataclasses import fields, replace
import hashlib

from .summon_self_attack_evidence_v26 import annotate_hit

# Added only by the supplied models patch, not by importing this module.
SCHEMA_FIELDS = {
    'entity_attack_source_kind': ('str', ''),
    'entity_owner_actor_id': ('int', 0),
    'entity_birth_generation': ('int', 0),
    'entity_monster_skill_id': ('int', 0),
    'entity_attack_skill_id': ('int', 0),
    'entity_creation_context_skill_id': ('int', 0),
    'entity_display_group_candidate_id': ('int', 0),
    'entity_attack_method': ('str', ''),
    'entity_attack_evidence_id': ('str', ''),
    'entity_attack_static_proof_id': ('str', ''),
    'entity_attack_wire_sha256': ('str', ''),
    'entity_attack_record_offset': ('int', 0),
    'entity_attack_xor_phase': ('int', 0),
    'entity_physical_hit_id': ('str', ''),
}


def evidence_metadata(evidence):
    clone = 33607 <= evidence.template_id <= 33614
    token = ':'.join((evidence.physical_hit_id, evidence.wire_sha256,
                      str(evidence.generation), evidence.static_proof_id,
                      evidence.birth_fingerprint, evidence.owner_fingerprint))
    return dict(
        wire_entity_id=evidence.entity_id,
        entity_template_id=evidence.template_id,
        entity_attack_source_kind='summon_clone' if clone else 'summon',
        entity_owner_actor_id=evidence.owner_id,
        entity_birth_generation=evidence.generation,
        entity_monster_skill_id=evidence.monster_skill_id,
        entity_attack_skill_id=evidence.player_skill_id,
        entity_creation_context_skill_id=evidence.context_skill_id,
        # This is a proposal for UI grouping ONLY. It does not change skill_id.
        entity_display_group_candidate_id=6319 if clone else 0,
        entity_attack_method=evidence.method,
        entity_attack_evidence_id=hashlib.sha256(token.encode()).hexdigest(),
        entity_attack_static_proof_id=evidence.static_proof_id,
        entity_attack_wire_sha256=evidence.wire_sha256,
        entity_attack_record_offset=evidence.record_offset,
        entity_attack_xor_phase=evidence.xor_phase,
        entity_physical_hit_id=evidence.physical_hit_id,
    )


def annotate_existing_event(host, packet, decoded, event):
    """Post-emission hook for core and bundled-direct status paths.

    Intentionally does NOT change source_id/skill_id/attribution_method,
    cast_instance_id, raw_skill_id, hit evidence, target, amount or timing.
    Thus Main can approve grouping and knownhit policy separately, without
    polluting player cast/proc/rotation/firepower evidence during a preview.
    """
    provider = getattr(host, 'summon_self_attack_context_provider', None)
    specs = getattr(host, 'summon_self_attack_specs', None)
    mode = getattr(host, 'summon_self_attack_encounter_mode', 'unknown')
    if (event is None or not callable(provider) or not specs or mode != 'pve'
            or event.has_real_hit_evidence is not True or event.healing):
        return event
    if not (set(SCHEMA_FIELDS) | {'wire_entity_id', 'entity_template_id'}) <= {f.name for f in fields(event)}:
        return event
    context = provider(packet, decoded, event)
    if context is None:
        return event
    hit, life = context
    if (hit.wire != packet.payload or hit.direction != packet.inferred_direction()
            or hit.entity_id != getattr(decoded, 'source_actor_id', None)
            or hit.target_id != getattr(decoded, 'target_actor_id', None)
            or hit.order[2] != getattr(decoded, 'record_offset', -1)
            or hit.damage != getattr(decoded, 'damage', None)
            or hit.damage != event.amount
            or hit.object_id != event.wire_damage_object_id
            or hit.result_group != event.wire_result_group_id):
        return event
    decision = annotate_hit(hit, life, specs, encounter_mode=mode)
    if decision.evidence is None:
        return event
    return replace(event, **evidence_metadata(decision.evidence))
