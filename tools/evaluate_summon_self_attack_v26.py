"""Replay only stored bounded raw audit rows; emit sidecars and test fixtures."""
from __future__ import annotations
import collections
import dataclasses
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (
    EntityLife, PhysicalHit, annotate_hit, specs_from_catalog, read_self_attack_slot, decrypt,
)
from tools.audit_entity_owner_342c_validation import frozen_protocol, valid_record

OUT = ROOT / 'outputs/v26_summon_attack_chain'
TARGETS = {30311, 30401, 32005, 32008, *range(33607, 33615)}
PAIRS = [('0826硬核曜_0_180_v2.json', '0826硬核曜_0_180.json'),
         ('0831黑硬曜_0_180.json', '0831黑硬曜_0_180.json')]


def load(path):
    return json.loads(path.read_text('utf8'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def physical(d, r):
    return PhysicalHit(d['capture'], r['physical_hit_id'],
        (r['frame'], r['message'], r['record_offset']), r['entity'], r['generation'],
        r['template'], r['target'], r['damage'], r['object_id'], r['result_group'],
        bytes.fromhex(r['wire']), r['direction'])


def owner_lives(scan, audit):
    result, provenance = {}, {}
    births = {(r['entity'], r['generation']): r for r in scan['births']}
    grouped = collections.defaultdict(list)
    seen_fingerprints = collections.defaultdict(set)
    for r in audit['records']:
        if valid_record(r) and r['direction'] == 'server_to_client':
            grouped[(r['entity'], r['generation'])].append(r)
            seen_fingerprints[(r['entity'], r['wire'])].add(r['generation'])
    for key, birth in births.items():
        records = grouped.get(key, [])
        candidates = [r for r in records if r['birth_frame'] == birth['frame']
                      and r['age'] is not None and 0 <= r['age'] <= 2
                      and (r['frame'], r['message']) >= (birth['frame'], birth['message'])]
        if not candidates or len({r['owner'] for r in records}) != 1:
            continue
        r = min(candidates, key=lambda r: (r['frame'], r['message'], r['marker']))
        if len(seen_fingerprints[(r['entity'], r['wire'])]) != 1:
            continue
        # Re-verify raw bytes, not merely a summarized owner/template claim.
        b, m = decrypt(bytes.fromhex(r['wire']), r['phase']), r['marker']
        born = decrypt(bytes.fromhex(birth['wire']), 5)
        u32 = lambda at: int.from_bytes(b[at:at + 4], 'little')
        if (b[m:m + 3] != bytes.fromhex('342c00') or u32(m - 4) != birth['entity']
                or u32(m + 3) != r['owner'] or u32(m + 43) != r['owner'] or b[m + 7] != 1
                or int.from_bytes(born[10:14], 'little') != birth['entity']
                or int.from_bytes(born[18:22], 'little') != birth['template']):
            raise AssertionError('owner audit row differs from raw bytes')
        later = [v for k, v in births.items() if k[0] == key[0] and k[1] > key[1]]
        end = min((v['frame'], v['message'], 10) for v in later) if later else None
        result[key] = EntityLife(scan['capture'], birth['entity'], birth['generation'], birth['template'],
            r['owner'], (birth['frame'], birth['message'], 10), (r['frame'], r['message'], m),
            sha(bytes.fromhex(birth['wire'])), sha(bytes.fromhex(r['wire'])), end)
        provenance[key] = dict(birth=birth, owner_record=r)
    return result, provenance


def verify_wire_rows(scan, rows, proto):
    count = 0
    with Path(scan['capture']).open('rb') as stream:
        assert stream.read(6) == b'DNDP1\0'
        for r in rows:
            stream.seek(r['file_offset'])
            t, adapter, original, size = struct.unpack('<dBII', stream.read(17))
            raw = stream.read(size)
            assert sha(raw) == r['raw_frame_sha256']
            assert t == r['t']
            packet = proto.decode_captured_frame(t, raw, original)
            assert packet.inferred_direction() == r['direction']
            wire = list(proto.split_v576_payload(packet.payload))[r['message']]
            assert wire.hex() == r['wire']
            count += 1
    return count


def main():
    catalog = load(OUT / 'static_catalog.json')
    specs = specs_from_catalog(catalog)
    proto, digest = frozen_protocol()
    windows, fixture_cases, sidecars = [], [], []
    selected = set()
    extended = '--extended' in sys.argv
    pairs = PAIRS + ([('0821硬核04_195_375.json', '0821硬核04_195_375_owner.json'),
                      ('0808竞速最佳_0_180.json', '0808竞速最佳_0_180_owner.json')] if extended else [])
    for scan_name, owner_name in pairs:
        scan = load(OUT / scan_name)
        owner_path = ROOT / 'outputs/v25_entity_owner_independent' / owner_name
        audit = load(owner_path if owner_path.exists() else OUT / owner_name)
        assert Path(scan['capture']) == Path(audit['capture'])
        lives, provenance = owner_lives(scan, audit)
        rows = [r for r in scan['hits'] if r['template'] in TARGETS]
        verified = verify_wire_rows(scan, rows, proto)
        counts, by_skill, by_template, unexplained = (collections.Counter() for _ in range(4))
        damage, before, after = 0, [], []
        for r in rows:
            hit = physical(scan, r)
            life = lives.get((hit.entity_id, hit.generation))
            decision = annotate_hit(hit, life, specs, encounter_mode='pve')
            slot = read_self_attack_slot(hit)
            counts[decision.reason] += 1
            identity = (hit.physical_hit_id, hit.entity_id, hit.target_id, hit.object_id, hit.result_group, hit.damage)
            before.append(identity)
            if decision.evidence:
                evidence = decision.evidence
                sidecars.append(dataclasses.asdict(evidence))
                damage += evidence.damage
                by_skill[str((hit.template_id, evidence.monster_skill_id, evidence.player_skill_id))] += 1
                by_template[str(hit.template_id)] += 1
                after.append((evidence.physical_hit_id, evidence.entity_id, evidence.target_id,
                              evidence.object_id, evidence.result_group, evidence.damage))
            else:
                after.append(identity)
                unexplained[str((hit.template_id, slot.monster_skill_id if slot else None))] += 1
            # One real raw fixture per exact observed template/skill, plus an
            # unknown row for each template. Full wire and provenance retained.
            case_key = (hit.template_id, slot.monster_skill_id if slot else None)
            if case_key not in selected and life is not None:
                selected.add(case_key)
                h = dataclasses.asdict(hit)
                h['wire'] = hit.wire.hex()
                fixture_cases.append(dict(name=f'{Path(hit.capture).stem}_f{r["frame"]}_m{r["record_offset"]}',
                    hit=h, life=dataclasses.asdict(life), expected_reason=decision.reason,
                    expected_skill=decision.evidence.player_skill_id if decision.evidence else None,
                    expected_monster_skill=slot.monster_skill_id if slot else None,
                    raw_anchor={k:r[k] for k in ('file_offset','frame','message','raw_frame_sha256','t')},
                    ownership=provenance[(hit.entity_id, hit.generation)]))
        assert before == after
        assert len({row[0] for row in before}) == len(before), 'duplicate physical hit IDs'
        windows.append(dict(capture=scan['capture'], start=scan['start'], seconds=scan['seconds'],
            input_hit_count=len(rows), input_damage=sum(r['damage'] for r in rows),
            raw_hit_rows_reread=verified, accepted_hit_count=counts['exact_self_attack'], accepted_damage=damage,
            reasons=dict(counts), by_skill=dict(by_skill), by_template=dict(by_template), unproven=dict(unexplained),
            physical_ledger_before_sha256=sha(json.dumps(before).encode()),
            physical_ledger_after_sha256=sha(json.dumps(after).encode()),
            entity_owned_registrations=scan['summary']['entity_owned_registrations'],
            literal_2404_known_entity=scan['summary']['literal_2404_known_entity']))
    used = {(c['hit']['template_id'], c['expected_monster_skill']) for c in fixture_cases}
    fixture = dict(schema='summon-self-attack-v26-fixture.1',
        note='Real raw captures. Negative mutations are made by tests and are not claimed as observed packets.',
        catalog=dict(schema=catalog['schema'], accepted=[r for r in catalog['accepted'] if (r['template'], r['monster_skill']) in used]),
        cases=fixture_cases)
    fixture_path = ROOT / 'tests/data/summon_attack_v26_raw.json'
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding='utf8')
    result = dict(windows=windows, total_input_hits=sum(w['input_hit_count'] for w in windows),
        total_accepted_hits=sum(w['accepted_hit_count'] for w in windows),
        total_accepted_damage=sum(w['accepted_damage'] for w in windows),
        physical_identity_and_damage_unchanged=True, object_chain_enabled=False,
        claim='Eligibility sidecar for existing physical hits, NOT net new knownhits or a full profession release gate.',
        protocol_sha256=digest, catalog_sha256=sha((OUT/'static_catalog.json').read_bytes()))
    suffix = '_4windows' if extended else ''
    (OUT / f'self_attack_evaluation{suffix}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    (OUT / f'self_attack_annotations{suffix}.json').write_text(json.dumps(sidecars, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
