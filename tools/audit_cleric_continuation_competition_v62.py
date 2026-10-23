"""Diagnose the old 3019 bucket; never use its name as ground truth."""
import bisect
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.protocol_v576 import PacketRecord


def main():
    prior = ROOT / 'outputs/chain_lightning_meter_v60_20260909'
    inputs = {}
    def read(name):
        data = (prior/name).read_bytes()
        inputs[str(prior/name)] = sha256(data).hexdigest()
        return json.loads(data)
    audit = read('v59_completed.json')['capture_evidence']
    gaps = read('remaining_325.json')['rows']
    actions = sorted((a for a in audit['focus_control_events'] if a['record_type'] == '0x0E'),
                     key=lambda a:a['sequence'])
    order = [a['sequence'] for a in actions]
    other_latest, counts = [], Counter()
    for gap in gaps:
        hit = gap['physical']
        index = bisect.bisect_left(order, hit['sequence'])-1
        if index < 0:
            counts['no_prior_action_request'] += 1
            continue
        action = actions[index]
        counts[str(action['skill_id'])] += 1
        if action['skill_id'] != 3019:
            other_latest.append(dict(hit=hit, latest_action_request=action,
                action_age_ms=hit['elapsed_ms']-action['elapsed_ms'],
                reattribution_allowed=False,
                reason='Action overlap is risk, not proof of Hit ownership.'))
    # Explicitly synthetic: this tests selector priority, not a real cast.
    state = SimpleNamespace(continuation_profile_usage={},
        recent_skill_casters={3019:[(123456,100.)], 3407:[(123456,102.5)]},
        _action_conflicts_with_confirmed_job=lambda a,b:False)
    packet = PacketRecord(103., 'UDP', '118.25.23.98', '192.168.1.2', 15100, 60000,
                          b'x'*140, 140, 140)
    selected = _V576PacketEventDecoder._continuation_damage_candidate(state, 123456, 103., packet)
    assert selected == 3019
    outside = _V576PacketEventDecoder._continuation_damage_candidate(state, 123456, 104.6, packet)
    assert outside is None
    result = dict(schema='cleric-continuation-competition-v62', inputs=inputs,
        baseline_scope='325 old unresolved 3019 named Hits in 0821; not ground truth',
        original_gap_rows=len(gaps), latest_action_request_counts=dict(counts),
        other_latest_action_rows=other_latest,
        selector_test=dict(synthetic=True, previous_3019_at=100., previous_3407_at=102.5,
            hit_at=103., selected=selected, at_104_6=outside),
        confirmed_wrong_skill_rows=0, production_changed=False,
        finding='4.5s unprofiled continuation precedes generic learned/recent-cast fallback; missing exact evidence can enter 3019 despite another cast. Neither recent actions nor the old bucket certify skill ownership.')
    output = ROOT/'outputs/cleric_continuation_competition_v62_20260909'
    output.mkdir(parents=True, exist_ok=True)
    (output/'REPORT.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(dict(old_rows=len(gaps), latest_actions=dict(counts),
        overlapping_other_action_rows=len(other_latest), confirmed_wrong_rows=0), ensure_ascii=False))


if __name__ == '__main__':
    main()
