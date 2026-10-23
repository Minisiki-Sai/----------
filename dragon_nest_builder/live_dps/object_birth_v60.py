"""Finite-lifetime compact candidates; no rule is currently certified/enabled.

The rejected self-loop-only experiment is archived in outputs/object_birth_v60_20260909.
A Shoot loop does NOT prove any object lifetime, including when creator+80 is zero.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import math
from .damage_attribution import AttributionLayer, attribution_layer, is_deterministic_attribution
from .native_damage_skill_keys import STATIC_INTERNAL_ATTACK_PARENT, _selected_native_key
from .udp_damage_records import inspect_udp_damage_records


# Derived and hash-checked in outputs/object_birth_v60_20260909/static_proof.py:
# monster table -> actor/file -> exact ACT ordinal + signal -> Projectile/file;
# its Shoot action has a type-4 damage signal and explicit next_action=Shoot.
# This certifies the selector and action graph ONLY, not object lifetime.
# Values: projectile, entity ACT sha256, projectile ACT sha256. Skill mapping
# comes from the existing SELECTOR_SPECS, never from this certificate's IDs.
LOOPING_SELECTOR_CERTIFICATES = {
    (32005, 50, 50, 5): (
        5054,
        '8e8cdf85b7818a395cf6c9bbd1a6ea491f9826c85bfcbba6e22bab0c5353c50a',
        '47e75178430fdeccafbd09dd214b14003926c173f5898444603513a02a3d52e0',
    ),
}

# Must be generated from a positive finite creator duration whose native
# reading, modifier and network override semantics are also verified. Values
# are seconds. No entry for creator+80 == 0: zero is NOT infinity or a default.
FINITE_SELECTOR_LIFETIMES = {}


def annotate_bounded_compact_hit(event, context, owner_proof, objects, *,
                                pve_confirmed, skill_catalog, damage_skill_ids,
                                live_aliases, job_conflicts):
    """Continue exact identity ONLY inside a certified finite duration.

    Caller supplies the CURRENT owner proof obtained at the result boundary.
    Positive/unknown self-attack slots and native result conflicts fail closed.
    The ordinary compact path still handles hits inside its original window.
    """
    if (owner_proof is None or pve_confirmed is not True or event.healing
            or event.amount <= 0 or event.has_real_hit_evidence is not True
            or not str(event.skill_id).isdecimal() or int(event.skill_id) <= 0
            or event.attribution_method == 'direct_hit_overflow'
            or is_deterministic_attribution(event.attribution_method)
            or attribution_layer(event.attribution_method) is AttributionLayer.STATUS):
        return event
    r = objects.objects.get((owner_proof.entity_id, owner_proof.generation, event.wire_damage_object_id))
    if r is None or r.spec is None:
        return event
    key = (r.template, r.record.length, r.record.action, r.record.signal)
    certificate = LOOPING_SELECTOR_CERTIFICATES.get(key)
    lifetime = FINITE_SELECTOR_LIFETIMES.get(key)
    group, attack, projectile, original_lifetime = r.spec
    if (certificate is None or certificate[0] != projectile
            or lifetime is None or not math.isfinite(lifetime) or lifetime <= original_lifetime
            or not math.isfinite(context.timestamp) or not math.isfinite(r.timestamp)
            or context.timestamp-r.timestamp <= original_lifetime
            or context.timestamp-r.timestamp > lifetime
            or r.order >= context.order
            or r.owner != owner_proof.owner_id or r.template != owner_proof.template_id
            or r.birth_hash != owner_proof.birth_fingerprint
            or r.owner_hash != owner_proof.owner_fingerprint
            or r.owner_evidence_id != owner_proof.owner_evidence_id
            or context.generation != owner_proof.generation
            or context.order[2] != owner_proof.record_offset
            or event.wire_record_offset != owner_proof.record_offset
            or sha256(context.wire).hexdigest() != owner_proof.hit_wire_sha256
            or event.source_id != f'0x{owner_proof.owner_id:08X}'
            or event.wire_source_actor_id != owner_proof.entity_id
            or event.wire_entity_id != owner_proof.entity_id
            or event.entity_template_id != owner_proof.template_id
            or event.entity_owner_evidence_id != owner_proof.owner_evidence_id
            or group not in skill_catalog
            or job_conflicts(r.owner, group)):
        return event
    inventory = inspect_udp_damage_records(context.wire, context.direction)
    key = (event.wire_record_offset, event.wire_source_actor_id, event.wire_target_actor_id,
           event.amount, event.critical, event.wire_result_group_id, event.wire_damage_object_id)
    hits = [h for h in inventory.damage_records if h.admissible_fields and h.family == 'direct37'
            and h.physical_key == key]
    if (not inventory.complete or len(hits) != 1 or 'direct37' not in event.wire_damage_layout
            or event.wire_critical != hits[0].critical
            or event.target_id.partition('@')[0] != f'0x{hits[0].target_actor_id:08X}'):
        return event
    hit = hits[0]
    if dict(hit.raw_skill_slots).get(102) != 0xffffffff:
        return event
    selected = _selected_native_key(hit)
    if selected is not None and selected[1] not in (0, 0xffffffff):
        raw = selected[1]
        mapped = STATIC_INTERNAL_ATTACK_PARENT.get(raw, raw)
        if live_aliases.get(mapped, mapped) != group:
            return event
    token = ':'.join(('object-birth-v60-bounded', *certificate[1:], r.record.wire_hash,
        str(r.record.offset), str(projectile), str(r.generation), r.birth_hash,
        r.owner_hash, owner_proof.hit_wire_sha256, str(context.order[2])))
    return replace(event, skill_id=str(group), skill_name=skill_catalog[group][0],
        attribution_method='packet_entity_projectile_selector', cast_instance_id='',
        entity_monster_skill_id=0xffffffff, entity_attack_skill_id=attack,
        entity_attack_evidence_id=sha256(token.encode('ascii')).hexdigest())
