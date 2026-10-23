"""Single-process read-only exact-parent audit of real diagnostic captures.

Only our diagnostic state is updated. Production feed/attribution is not run
or patched. No inferred event skill/name/job is used as a truth anchor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.audit_remaining_registration_packet_decoder import (
    decode_candidate_registration, decode_extended_resource_registration, u32, LAYOUTS,
)
from tools.audit_remaining_registration_server_variants import DATA_DIR, hashes, load_templates
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body, _v576_candidate_xor_phases,
    decode_v576_embedded_direct_damage_events, decode_v576_scene_teardown,
)
from dragon_nest_builder.live_dps.damage_object_origins import (
    _SPEC_BY_PROJECTILE_TEMPLATE, _PARENT_TEMPLATES_BY_CHILD,
    CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS,
    REJECTED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
)


def static_spec(template):
    allowed = CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS | PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
    if (template not in allowed or template in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS
            or template in REJECTED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS):
        return None
    spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template)
    return spec if spec and not spec.require_action_evidence else None


def audit(path, templates, output, extended120=False):
    before = hashes(); started = time.monotonic()
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    skills = source._decoder.damage_skill_ids
    scene = 0; serial = 0; origin = None
    generations = []; slots = {}; counters = Counter(); recent_tails = {}
    stats = Counter(); per_length = Counter(); directions = Counter(); failed_lengths = Counter()
    layout_examples = {}; direct_digest = hashlib.sha256(); seen_hits = {}; last_print = started
    parent_checks = []; orphan_fixed = []

    def register(row):
        key = (scene, row['owner'], row['obj'])
        token=(*key,row['kind'],row['tail'])
        old_id=recent_tails.get(token)
        if old_id is not None:
            old=generations[old_id]
            if abs(row['t']-old['t']) <= .25:
                stats['exact_tail_replicas']+=1
                if row['t']<old['t']:stats['replica_timestamp_backward']+=1
                if slots.get(key)!=old_id:stats['late_replica_did_not_revive_old_generation']+=1
                old['replicas'].append({k:row[k] for k in ('p','m','length','phase','direction','kind')})
                return old
        prev_id = slots.get(key)
        prev = generations[prev_id] if prev_id is not None else None
        age = row['t'] - prev['t'] if prev else -1
        # Byte-identical copied wrappers are real duplicates. A typed/server
        # echo may merge only when ALL 49 common bytes match, not merely the
        # template. Conflicting tails in the same shape start a new generation.
        exact = bool(prev and row['kind'] == prev['kind'] and row['tail'] == prev['tail'])
        echo = bool(prev and {row['kind'], prev['kind']} & {'server09'}
                    and row['kind'] != prev['kind'] and row['prefix49'] == prev['prefix49'])
        if prev and abs(age) <= .25 and (exact or echo):
            stats['exact_tail_replicas' if exact else 'typed_server_exact_49byte_echoes'] += 1
            prev['replicas'].append({k: row[k] for k in ('p', 'm', 'length', 'phase', 'direction', 'kind')})
            if row['kind'] != 'server09' and prev['kind'] == 'server09':
                # Retain the typed bytes but do not retroactively create a
                # parent edge for any earlier hit in this generation.
                prev['typed_observation'] = row
                prev['candidate_parent'] = row['candidate_parent']
                if row['candidate_parent']:
                    bind_parent(prev, row)
            return prev
        if prev:
            prev['superseded_at'] = [row['p'], row['m']]
        counters[key] += 1
        spec = static_spec(row['template'])
        g = {**row, 'id': len(generations), 'scene': scene, 'generation': counters[key],
             'replicas': [], 'parent_binding': None, 'fixed_skills': Counter(),
             'raw_hits': 0, 'relay_identity_unique_hits': 0, 'hit_examples': [],
             'fixed_examples': [], 'static_skill': spec.canonical_skill_id if spec else 0,
             'static_skill_ids': sorted(spec.skill_ids) if spec else [],
             'fixed_vs_static_conflicts': [], 'superseded_at': None}
        generations.append(g); slots[key] = g['id']; recent_tails[token]=g['id']
        if row['candidate_parent']:
            bind_parent(g, row)
        return g

    def bind_parent(g, row):
        parent_key = (scene, row['owner'], row['candidate_parent'])
        pid = slots.get(parent_key)
        parent = generations[pid] if pid is not None else None
        check = {'child_generation_id': g['id'], 'child_packet': row['p'], 'child_offset': row['m'],
                 'owner': row['owner'], 'child_obj': row['obj'], 'child_template': row['template'],
                 'candidate_parent': row['candidate_parent'], 'parent_generation_id': pid,
                 'status': 'no_exact_same_owner_parent', 'independent_fixed_at_birth': {},
                 'expected_skill': 0, 'expected_skill_ids': [], 'parent_static_skill': 0,
                 'parent_static_skill_ids': [], 'parent_field_low16': row['candidate_parent'] & 0xffff}
        if parent is not None:
            age = row['t'] - parent['t']
            check.update(parent_packet=parent['p'], parent_object_id=parent['obj'],
                         parent_owner=parent['owner'], parent_scene=parent['scene'],
                         parent_generation=parent['generation'], parent_template=parent['template'],
                         parent_age_seconds=round(age, 6),
                         independent_fixed_at_birth=dict(parent['fixed_skills']),
                         parent_static_skill=parent['static_skill'], parent_static_skill_ids=parent['static_skill_ids'])
            edge = parent['template'] in _PARENT_TEMPLATES_BY_CHILD.get(row['template'], ())
            check['typed_act_parent_child_edge'] = edge
            if (parent['p'], parent['m']) >= (row['p'], row['m']):
                check['status'] = 'parent_not_before_child'
            elif not 0 <= age <= 8:
                check['status'] = 'exact_id_but_stale_reject'
            elif not edge:
                check['status'] = 'exact_id_but_ACT_edge_mismatch'
            elif parent['fixed_vs_static_conflicts'] or len(parent['fixed_skills']) > 1:
                check['status'] = 'parent_independent_truth_conflict'
            elif parent['static_skill']:
                check.update(status='exact_generation_ACT_supported', expected_skill=parent['static_skill'],
                             expected_skill_ids=parent['static_skill_ids'])
            elif len(parent['fixed_skills']) == 1:
                skill = int(next(iter(parent['fixed_skills'])))
                check.update(status='exact_generation_fixed_parent_supported', expected_skill=skill,
                             expected_skill_ids=[skill])
            else:
                check['status'] = 'exact_generation_but_parent_skill_unproven'
        parent_checks.append(check); g['parent_binding'] = len(parent_checks) - 1

    def feed(packet):
        nonlocal serial, scene, origin, last_print
        serial += 1
        t = float(packet.timestamp_seconds); payload = bytes(packet.payload)
        if origin is None: origin = t
        direction = packet.inferred_direction()
        if str(packet.transport).upper() == 'TCP':
            if decode_v576_scene_teardown(payload, direction, connection_context=packet) is not None:
                scene += 1; slots.clear(); stats['scene_teardown_resets'] += 1
            return ()
        if str(packet.transport).upper() != 'UDP': return ()
        rows = []
        if len(payload) in LAYOUTS or (extended120 and len(payload)==120):
            candidate = decode_candidate_registration(payload, known_templates=templates)
            if candidate is None and extended120:
                candidate=decode_extended_resource_registration(payload,known_templates=templates,allow_missing_resource=True)
            if candidate:
                c = candidate; b = _decode_v576_body(payload, c.xor_phase); o = c.owner_offset
                rows.append(dict(p=serial, m=o-1, t=t, capture_seconds=round(t-origin, 6),
                                 owner=c.source_actor_id, obj=c.damage_object_id,
                                 template=c.projectile_template_id, kind=c.layout,
                                 length=len(payload), phase=c.xor_phase, direction=direction,
                                 offset=o, tail=c.normalized_tail_hex, prefix49=b[o:o+49].hex(),
                                 candidate_parent=c.parent_object_id_candidate,
                                 resource=c.effect_resource_id, sequence=c.child_sequence,
                                 repeated_resource=c.repeated_resource_id,
                                 resource_missing_sentinel=c.resource_missing_sentinel,
                                 wire_payload_hex=payload.hex(), decoded_payload_hex=b.hex()))
                per_length[str(len(payload))] += 1
                layout_examples.setdefault(str(len(payload)), rows[-1])
            else:
                failed_lengths[str(len(payload))] += 1
        seen = set()
        for phase in _v576_candidate_xor_phases(payload):
            b = _decode_v576_body(payload, phase); m = b.find(b'\x09', 9)
            while m >= 0:
                if m + 48 <= len(b) and b[m+2:m+8] == bytes(6):
                    owner, obj, template = u32(b,m-4), u32(b,m+8), u32(b,m+12)
                    if (0 < owner < 0xffffffff and owner == u32(b,m+44)
                            and 0 < obj <= 0xffffff and 0 < template <= 0x7fffffff):
                        k=(m,owner,obj,template)
                        if k not in seen:
                            seen.add(k)
                            rows.append(dict(p=serial,m=m,t=t,capture_seconds=round(t-origin,6),
                                             owner=owner,obj=obj,template=template,kind='server09',
                                             length=len(b),phase=phase,direction=direction,offset=m-4,
                                             tail=b[m-4:m+48].hex(),prefix49=(b[m-4:m]+b[m+3:m+48]).hex(),
                                             candidate_parent=None,resource=None,sequence=None,
                                             marker=b[m:m+3].hex(),decoded_record_hex=b[m-4:m+100].hex()))
                m=b.find(b'\x09',m+1)
        hits = decode_v576_embedded_direct_damage_events(payload, None, skills)
        ordered = [(r['m'], 0, r) for r in rows] + [(h.record_offset, 1, h) for h in hits]
        for _, kind, item in sorted(ordered, key=lambda x:(x[0],x[1])):
            if kind == 0:
                register(item); continue
            h=item
            if not h.source_actor_id or not h.damage_object_id: continue
            owner,obj=int(h.source_actor_id),int(h.damage_object_id)
            skill=int(h.skill_id) if h.skill_id_exact else 0
            hr=dict(p=serial,m=int(h.record_offset),t=t,capture_seconds=round(t-origin,6),
                    owner=owner,obj=obj,target=int(h.target_actor_id or 0),
                    result_group=int(h.damage_result_group_id or 0),damage=int(h.damage or 0),
                    fixed_skill=skill,direction=direction)
            stats['raw_direct37_rows'] += 1
            directions[str(direction)] += 1
            direct_digest.update(json.dumps(hr,sort_keys=True,separators=(',',':')).encode())
            # Strong report-only identity preserves same-skill simultaneous hits.
            # It is NOT substituted for production dedup or scored Hit counts.
            identity=(scene,owner,obj,hr['target'],hr['result_group'],hr['damage'],skill)
            previous=seen_hits.get(identity)
            unique = previous is None or abs(t-previous) > .25
            if unique: seen_hits[identity]=t;stats['relay_identity_unique_direct37'] += 1
            gid=slots.get((scene,owner,obj))
            if gid is None: stats['direct37_no_current_generation'] += 1;continue
            g=generations[gid]
            if not 0 <= t-g['t'] <= 8:
                stats['direct37_stale_generation_reject'] += 1;continue
            g['raw_hits'] += 1
            g['relay_identity_unique_hits'] += int(unique)
            if len(g['hit_examples']) < 3: g['hit_examples'].append(hr)
            if skill:
                g['fixed_skills'][str(skill)] += 1
                g['fixed_examples'].append(hr)
                if g['static_skill'] and skill not in g['static_skill_ids']:
                    g['fixed_vs_static_conflicts'].append(hr)
        if time.monotonic()-last_print > 25:
            print(json.dumps({'capture':path.name,'packet':serial,'registrations':dict(per_length),
                              'generations':len(generations),'parent_checks':len(parent_checks)},ensure_ascii=False),flush=True)
            last_print=time.monotonic()
        return ()

    source._decoder.feed_events=feed
    try: source.poll()
    finally: source.close()
    for check in parent_checks:
        child=generations[check['child_generation_id']]
        parent=generations[check['parent_generation_id']] if check['parent_generation_id'] is not None else None
        check.update(child_raw_hits=child['raw_hits'],child_unique_hits=child['relay_identity_unique_hits'],
                     child_fixed=dict(child['fixed_skills']),child_fixed_examples=child['fixed_examples'],
                     parent_fixed_final=dict(parent['fixed_skills']) if parent else {},
                     parent_fixed_vs_static_conflicts=parent['fixed_vs_static_conflicts'] if parent else [])
        expected=set(check['expected_skill_ids'])
        check['independent_child_conflicts']=[h for h in child['fixed_examples'] if expected and h['fixed_skill'] not in expected]
        check['independent_child_matches']=[h for h in child['fixed_examples'] if expected and h['fixed_skill'] in expected]
    groups=defaultdict(Counter)
    for g in generations:
        key=f"{g['kind']}:{g['template']}"
        groups[key]['generations']+=1;groups[key]['raw_hits']+=g['raw_hits']
        groups[key]['relay_identity_unique_hits']+=g['relay_identity_unique_hits']
        groups[key]['fixed_rows']+=sum(g['fixed_skills'].values())
        groups[key]['fixed_vs_static_conflicts']+=len(g['fixed_vs_static_conflicts'])
        groups[key]['static_skill']=g['static_skill']
    after=hashes()
    report={'capture':str(path),'feed_packets':serial,'origin':origin,'elapsed_seconds':round(time.monotonic()-started,3),
            'core_before':before,'core_after':after,'core_unchanged':before==after,
            'policy':'same scene+owner+full object ID; current generation in wire order; <=8s rejection guard; no nearest template/action; raw rows NOT scored Hit counts',
            'stats':stats,'directions':directions,'accepted_lengths':per_length,'nonmatching_length_packets':failed_lengths,
            'raw_direct_digest':direct_digest.hexdigest(),'groups':groups,'layout_examples':layout_examples,
            'parent_status':Counter(r['status'] for r in parent_checks),
            'independent_child_fixed_conflicts':sum(len(r['independent_child_conflicts']) for r in parent_checks),
            'independent_child_fixed_matches':sum(len(r['independent_child_matches']) for r in parent_checks),
            'parent_checks':parent_checks,'generations':generations}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('capture','feed_packets','elapsed_seconds','core_unchanged','accepted_lengths',
                                        'stats','parent_status','independent_child_fixed_conflicts','independent_child_fixed_matches')},ensure_ascii=False),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('captures',nargs='+',type=Path)
    p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--extended120',action='store_true')
    p.add_argument('--resource-root',type=Path,default=Path(r'E:\ext0811.rar\Resource00\Resource00\resource'))
    a=p.parse_args();sys.stdout.reconfigure(encoding='utf-8');a.output_dir.mkdir(parents=True,exist_ok=True)
    templates=load_templates(a.resource_root)
    for path in a.captures:
        output=a.output_dir/(path.stem+'.json')
        if output.exists():print('EXISTS; not overwritten',output,flush=True);continue
        print('START',path.name,flush=True);audit(path,templates,output,a.extended120)


if __name__=='__main__':main()
