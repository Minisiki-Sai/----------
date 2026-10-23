"""Bounded 0314/ACT-index diagnostic. No action proximity damage upgrades."""
from __future__ import annotations
import collections
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import read_compact_action


def main():
    out = ROOT / 'outputs/v26_summon_attack_chain'
    catalog = json.loads((out / 'static_catalog.json').read_text('utf8'))
    templates = {r['template']: r for r in catalog['templates']}
    selected = ['0821硬核04_0_180_v2.json', '0830星期天_210_390.json',
                '0831黑硬曜_0_180.json', '0826硬核曜_0_180.json']
    windows, examples, seen = [], [], set()
    for name in selected:
        path = ROOT / 'outputs/v25_entity_owner_independent' / name
        audit = json.loads(path.read_text('utf8'))
        counts, rejected = collections.Counter(), collections.Counter()
        for r in audit['actions']:
            if r['template'] not in templates or not r['compact']:
                continue
            names = tuple(a['name'] for a in templates[r['template']]['action_indices'])
            obs = read_compact_action(bytes.fromhex(r['wire']), r['marker'], r['actor'], names, direction=r['direction'])
            if obs is None:
                rejected[str(r['template'])] += 1
                continue
            key = (r['template'], obs.action_code, obs.variant, obs.kind, obs.act_index_hypothesis)
            counts[str(key)] += 1
            if key not in seen:
                seen.add(key)
                observation = dataclasses.asdict(obs)
                observation['raw_record'] = obs.raw_record.hex()
                examples.append(dict(capture=audit['capture'], frame=r['frame'], message=r['message'],
                    file_offset=r['file_offset'], generation=r['generation'], template=r['template'],
                    wire=r['wire'], wire_sha256=hashlib.sha256(bytes.fromhex(r['wire'])).hexdigest(),
                    act_names=names, observation=observation))
        windows.append(dict(capture=audit['capture'], start=audit['start'], seconds=audit['seconds'],
            observed=dict(counts), rejected=dict(rejected), attribution_upgrades=0))
    result = dict(windows=windows, examples=examples,
        saint_static_indices={str(t):templates[t]['action_indices'] for t in (30311,30401)},
        interpretation='ACT index is an alignment hypothesis, not packet-backed hit ownership. Saint observed code3 is Stand; code9 ShockofRelic not observed in these windows.',
        no_upgrade_reasons=['No action-instance token joining 0314 to a physical result/object.',
            'Repeated Stand snapshots do not exclude external ShockofRelic or pending projectiles.',
            'Code255/out-of-ACT-range is not a fallback to a skill.',
            'Old boss decoder accepts only six payload bytes and a preferred phase; candidate requires full23-byte marker record and unique phase. Still not a full outer-message record walker.'])
    (out / 'compact_action_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    fixture = ROOT / 'tests/data/summon_compact_v26_raw.json'
    fixture.write_text(json.dumps(dict(examples=examples),ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(windows=windows,example_count=len(examples)),ensure_ascii=False))


if __name__ == '__main__':
    main()
