"""Read-only live-boundary diagnostics; never promotes evidence or changes Hits.

Counts here precede replay aggregation and are for locating rejection branches,
not a replacement for the frozen all-profession acceptance audit.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps import damage_object_origins as objects
from dragon_nest_builder.live_dps.autonomous_attack_families import confirmed_pve_target
from dragon_nest_builder.live_dps.summon_attack_runtime import SUMMON_SELF_ATTACK_SPECS
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import PhysicalHit, read_self_attack_slot
from tools.audit_direct_damage_by_profession import _scope_reason, _strict_classification


def diagnose(capture: Path, output: Path):
    source = V576DiagnosticReplaySource(capture, ROOT / 'dragon_nest_builder/data', {})
    decoder = source._decoder
    ledger = decoder.damage_object_origins
    register, feed = ledger.observe_registration, decoder.feed_events
    observations = OrderedDict()
    counts, samples = Counter(), defaultdict(list)

    def observe(**kwargs):
        result = register(**kwargs)
        key = (kwargs['source_actor_id'], kwargs['damage_object_id'])
        observations[key] = {**kwargs, 'accepted_origin': result is not None}
        observations.move_to_end(key)
        while len(observations) > 32768:
            observations.popitem(last=False)
        return result

    def inspect(event, packet):
        owner = event.wire_source_actor_id
        obj = event.wire_damage_object_id
        serial, offset, now = decoder._damage_object_packet_serial, event.wire_record_offset, packet.timestamp_seconds
        detail = {'wire_source': owner, 'object': obj}
        if 0 < owner < 65536:
            entity = decoder.monster_entities.get(owner)
            template = entity.template_id if entity else 0
            binding = decoder.entity_owner_evidence.resolve(owner, template, now)
            reason = decoder.entity_owner_evidence.negative_state(owner, timestamp=now)
            hit = PhysicalHit('diagnostic', 'not-a-proof', (serial, 0, offset), owner,
                binding.generation if binding else 0, template, event.wire_target_actor_id,
                event.amount, obj, event.wire_result_group_id, packet.payload, packet.inferred_direction())
            slot = read_self_attack_slot(hit)
            value = slot.monster_skill_id if slot else -1
            detail.update(template=template, self_skill=value, owner_state=reason,
                owner_resolved=binding is not None,
                pve=confirmed_pve_target(decoder, event.wire_target_actor_id, packet),
                map_id=decoder.detected_map_id, map_active=decoder.detected_map_active,
                map_history=decoder.detected_map_history[-1:] if decoder.detected_map_history else [],
                current_owner_proof=bool(event.entity_owner_evidence_id))
            if not binding:
                return 'entity_' + str(reason or 'owner_not_resolved'), template, detail
            if not event.entity_owner_evidence_id:
                return 'entity_current_hit_owner_proof_missing', template, detail
            if not slot:
                return 'entity_self_attack_record_not_supported', template, detail
            if not detail['pve']:
                return 'entity_pve_not_independently_confirmed', template, detail
            if (template, value) not in SUMMON_SELF_ATTACK_SPECS:
                return 'entity_self_skill_empty' if value == 0xffffffff else 'entity_self_skill_unmapped', template, detail
            return 'entity_exact_evidence_not_applied', template, detail
        if not owner or not obj:
            return 'wire_identity_missing', 0, detail
        key = (owner, obj)
        origin, snapshot = ledger._origin_at(key, now, serial, offset)
        recent = observations.get(key)
        detail['last_registration'] = recent
        if origin is None:
            template = recent['projectile_template_id'] if recent else 0
            spec = objects._SPEC_BY_PROJECTILE_TEMPLATE.get(template)
            if recent:
                detail['last_registration_age_seconds'] = now - recent['timestamp']
            if spec and detail['last_registration_age_seconds'] > spec.lifetime_seconds:
                # This is NOT proof that the ID was reused. It says only that
                # the last observed state cannot identify the present hit.
                return 'only_expired_registration_observed', template, detail
            reason = 'no_matching_registration' if recent is None else (
                'registration_template_not_mapped' if template not in objects._SPEC_BY_PROJECTILE_TEMPLATE
                else 'registration_not_admitted_or_pruned')
            return reason, template, detail
        template = origin.projectile_template_id
        detail['origin'] = asdict(origin)
        invalidation = snapshot.invalidation if snapshot else ledger._unknown_registration_invalidations.get(key)
        revoked = ledger._revoked_origin_generations.get((*key, origin.generation))
        detail.update(invalidation=invalidation, revoked=revoked)
        if now > origin.expires_at:
            return 'object_expired', template, detail
        if invalidation and ledger._boundary_reached(invalidation, now, serial, offset):
            return 'object_invalidated_by_registration', template, detail
        if revoked and ledger._boundary_reached(revoked, now, serial, offset):
            return 'object_revoked_by_fixed_skill_conflict', template, detail
        if revoked == (now, serial, offset + 1):
            # The host deliberately revokes after processing this physical
            # record; _origin_at(offset) still sees its pre-conflict snapshot.
            # Do not call that "deterministic origin not applied".
            return 'current_hit_fixed_skill_conflict', template, detail
        spec = objects._SPEC_BY_PROJECTILE_TEMPLATE.get(template)
        if origin.action_qualified_subtype and not origin.action_backed:
            return 'object_action_qualified_missing_action', template, detail
        if origin.requires_root_action_proof and not origin.parent_root_action_epoch_id:
            return 'object_parent_root_missing', template, detail
        if spec and spec.require_action_evidence and not origin.action_backed:
            return 'object_caster_action_missing', template, detail
        if ledger._origin_has_deterministic_lineage(origin, now, serial, offset):
            return 'object_deterministic_origin_not_applied', template, detail
        return 'object_only_provisional_lineage', template, detail

    def wrapped(packet):
        events = feed(packet)
        for event in events:
            if (not isinstance(event, CombatEvent)
                    or _scope_reason(event) != 'included_positive_direct_hit'
                    or _strict_classification(event) != 'nondeterministic'):
                continue
            reason, template, detail = inspect(event, packet)
            key = (event.job, event.skill_id, event.skill_name, reason, template)
            counts[key] += 1
            if len(samples[key]) < 2:
                samples[key].append({'time': packet.timestamp_seconds, 'serial': decoder._damage_object_packet_serial,
                    'transport': packet.transport, 'direction': packet.inferred_direction(),
                    'event': asdict(event), 'state': detail, 'wire': packet.payload.hex()})
        return events

    ledger.observe_registration = observe
    decoder.feed_events = wrapped
    try:
        for _ in source.iter_batches():
            pass
    finally:
        source.close()
    rows = [dict(job=k[0], skill_id=k[1], skill=k[2], reason=k[3], template=k[4], hits=v, examples=samples[k])
            for k, v in counts.most_common()]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(capture=str(capture), scope='pre-aggregation diagnostic only', rows=rows),
        ensure_ascii=False, indent=2, default=list), encoding='utf-8')
    totals = Counter()
    for row in rows:
        totals[row['reason']] += row['hits']
    print(json.dumps(dict(capture=capture.name, hits=sum(counts.values()), reasons=totals, output=str(output)), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    diagnose(args.capture, args.output)
