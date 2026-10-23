"""Bounded read-only prefix probe for previously located completeness changes."""
import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import sys


def run(snapshot, capture, output):
    snapshot=snapshot.resolve()
    sys.path.insert(0,str(snapshot))
    from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
    from dragon_nest_builder.live_dps.models import CombatEvent
    source=V576DiagnosticReplaySource(capture,snapshot/'dragon_nest_builder/data',{})
    d=source._decoder
    watched={51,161,194,221,352}
    wanted={6825,24277,33042,56004,80963,89965}
    count=0
    def serial(value):
        return asdict(value) if is_dataclass(value) else value
    def state(entity, timestamp):
        ledger=d.entity_owner_evidence
        return {'entity':entity,'birth':ledger.births.get(entity),
            'binding':serial(ledger.bindings.get(entity)),
            'negative':ledger.negative_state(entity,timestamp=timestamp) if hasattr(ledger,'negative_state') else None,
            'template':serial(d.monster_entities.get(entity)),
            'cached_owner':d.summon_owners.get(entity),
            'recent_object_owner':d.recent_skill_object_owners.get(entity)}
    transitions=[]; rows=[]; last={}; active=None
    wrapped={}
    for name in ('_legacy_entity_owner_blocked','_cached_summon_owner','_skill_object_owner','_infer_recent_summon_owner'):
        method=getattr(d,name,None)
        if method is None: continue
        wrapped[name]=method
        def observe(*args,_name=name,_method=method,**kwargs):
            result=_method(*args,**kwargs)
            if active is not None and args and args[0] in watched:
                active['calls'].append({'method':_name,'args':args,'result':serial(result)})
            return result
        setattr(d,name,observe)
    original=d.feed_events
    class PrefixDone(Exception): pass
    def feed(packet):
        nonlocal count,active
        count+=1
        if count in wanted:
            active={'message':count,'time':packet.timestamp_seconds,'wire':packet.payload.hex(),
                    'before':[state(e,packet.timestamp_seconds) for e in sorted(watched)],'calls':[]}
        events=original(packet)
        for entity in watched:
            s=state(entity,packet.timestamp_seconds)
            key=json.dumps(s,sort_keys=True)
            if last.get(entity)!=key:
                transitions.append({'message':count,'time':packet.timestamp_seconds,'state':s,'wire':packet.payload.hex()})
                last[entity]=key
        if active is not None:
            active.update(after=[state(e,packet.timestamp_seconds) for e in sorted(watched)],
                          decoded=serial(d.last_decoded_message),
                          events=[asdict(e) for e in events if isinstance(e,CombatEvent)])
            rows.append(active);active=None
            print('probed',count,flush=True)
        if count>=90000: raise PrefixDone()
        return events
    d.feed_events=feed
    try:
        source.poll()
    except PrefixDone:
        pass
    finally:
        d.feed_events=original
        for name in wrapped: delattr(d,name)
        source.close()
    if len(rows)!=len(wanted): raise AssertionError('Prefix did not visit every target')
    result={'capture':str(capture),'snapshot':str(snapshot),'messages':count,
            'manifest_sha256':hashlib.sha256((snapshot/'manifest.json').read_bytes()).hexdigest(),
            'transitions':transitions,'rows':rows}
    with output.open('x',encoding='utf-8') as out:json.dump(result,out,ensure_ascii=False,indent=2)
    print('saved',output,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--capture',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.snapshot,a.capture,a.output)
