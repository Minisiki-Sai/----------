"""One complete source replay with transparent, four-object observer hooks."""
from __future__ import annotations
import hashlib
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from tools.audit_damage_attribution_paired import BaselineFinder, MODULES
CANDIDATE=ROOT/'outputs/damage_attribution_candidate_v20_20260907'
sys.meta_path.insert(0,BaselineFinder(CANDIDATE))
from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource

OWNER=1337770664
OBJECTS={102046,102941,101918,102809}
ORIGIN=1788595471.266909
OUT=ROOT/'outputs/audit_remaining_registration_source_v20_20260907'
CAP=Path(r'C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics\0905黑龙经典冰灵.dpscap')


def clean(value):
    if is_dataclass(value):return clean(asdict(value))
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(set,frozenset,list,tuple)):return [clean(v) for v in value]
    if isinstance(value,bytes):return value.hex()
    if isinstance(value,(str,int,float,bool)) or value is None:return value
    return repr(value)


def where(fn):
    return str(Path(fn.__code__.co_filename).resolve())


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if OUT.exists():raise SystemExit('Refusing to overwrite existing trace')
    before={f:hashlib.sha256((CANDIDATE/f).read_bytes()).hexdigest() for f in MODULES.values()}
    trace=[];current={};transport_current={};started=time.monotonic();last=[started]
    def scope(t):return 1399<=t-ORIGIN<=1410 or 1490<=t-ORIGIN<=1501
    def log(kind,**kw):trace.append(clean(dict(kind=kind,packet=dict(current),**kw)))
    def progress(done,total):
        if time.monotonic()-last[0]>20:
            print(json.dumps(dict(progress=round(done/total,4),seconds=round(time.monotonic()-started,2),trace_rows=len(trace))),flush=True);last[0]=time.monotonic()
    source=V576DiagnosticReplaySource(CAP,ROOT/'dragon_nest_builder/data',{},progress_callback=progress)
    decoder=source._decoder;ledger=decoder.damage_object_origins
    feed_original=decoder.feed_events
    gl=feed_original.__func__.__globals__
    metadata=dict(core_feed=where(feed_original.__func__),
        host_registration_decoder=where(gl['decode_v576_damage_object_registration']),
        protocol_registration_decoder=where(protocol.decode_v576_damage_object_registration),
        ledger_registration=where(type(ledger).observe_registration),
        transport_feed=where(protocol.V576TransportReassembler.feed),
        core_host_function_is_protocol=gl['decode_v576_damage_object_registration'] is protocol.decode_v576_damage_object_registration)
    assert str(CANDIDATE) in metadata['core_feed'] and str(CANDIDATE) in metadata['ledger_registration']
    def snapshot():
        return clean(dict(origins={obj:ledger._origins.get((OWNER,obj)) for obj in sorted(OBJECTS)},
            invalidations={obj:ledger._unknown_registration_invalidations.get((OWNER,obj)) for obj in sorted(OBJECTS)},
            revoked={str(k):v for k,v in ledger._revoked_origin_generations.items() if k[0]==OWNER and k[1] in OBJECTS},
            actions=[a for a in ledger._actions if a.owner_actor_id==OWNER and a.raw_skill_id in (2353,2207)]))
    for name in ('observe_registration','invalidate_origin','attribute_direct37','observe_action','_prune','_live_parent_origin'):
        original=getattr(ledger,name)
        def make_hook(name,original):
            def hook(*args,**kw):
                owner=kw.get('source_actor_id',args[0] if args and name=='observe_action' else None)
                obj=kw.get('damage_object_id')
                watched=(owner==OWNER and obj in OBJECTS) or (name=='observe_action' and owner==OWNER and len(args)>1 and args[1] in (2353,2207))
                active=scope(current.get('t',0))
                if name in ('_prune','_live_parent_origin'):watched=active
                prev=snapshot() if watched and active else None
                result=original(*args,**kw)
                if prev is not None:
                    after=snapshot()
                    if name!='_prune' or prev!=after:
                        log('ledger.'+name,args=args,kwargs=kw,before=prev,after=after,result=result)
                return result
            return hook
        setattr(ledger,name,make_hook(name,original))
    # Globals here are the ACTUAL bound core namespace, not a guessed alias.
    for name in ('decode_v576_damage_object_registration','decode_v576_server_damage_object_registrations'):
        original=gl[name]
        def make_decode_hook(name,original):
            def hook(*args,**kw):
                result=original(*args,**kw)
                if scope(current.get('t',0)):
                    rs=result if isinstance(result,tuple) else (result,)
                    selected=[r for r in rs if r and r.source_actor_id==OWNER and r.damage_object_id in OBJECTS]
                    if selected or (len(args[0])==112 and abs(current['t']-1788596877.978771)<.00001) or (len(args[0])==112 and abs(current['t']-1788596969.072681)<.00001):
                        log('core.'+name,length=len(args[0]),direction=args[1] if len(args)>1 else None,
                            wire=args[0].hex(),result=selected,executed=where(original))
                return result
            return hook
        gl[name]=make_decode_hook(name,original)
    count=[0]
    def feed(packet):
        count[0]+=1;current.clear();current.update(p=count[0],t=packet.timestamp_seconds,
            capture_seconds=round(packet.timestamp_seconds-ORIGIN,6),length=len(packet.payload),transport=packet.transport,direction=packet.inferred_direction())
        if scope(packet.timestamp_seconds):
            independent=protocol.decode_v576_damage_object_registration(packet.payload,None)
            if independent and independent.source_actor_id==OWNER and independent.damage_object_id in OBJECTS:
                log('source.feed_events_input',wire=packet.payload.hex(),independent_registration=independent)
        events=feed_original(packet)
        if scope(packet.timestamp_seconds):
            relevant=[e for e in events if getattr(e,'wire_damage_object_id',0) in OBJECTS]
            if relevant:log('source.output_events',events=relevant)
        return events
    decoder.feed_events=feed
    original_transport=protocol.V576TransportReassembler.feed
    def transport(self,packet):
        active=scope(packet.timestamp_seconds)
        raw=protocol.decode_v576_damage_object_registration(packet.payload,None) if active else None
        outputs=original_transport(self,packet)
        if raw and raw.source_actor_id==OWNER and raw.damage_object_id in OBJECTS:
            log('transport.before_after',input_t=packet.timestamp_seconds,input_length=len(packet.payload),
                input_wire=packet.payload.hex(),input_independent=raw,
                output_packets=[dict(length=len(p.payload),wire=p.payload.hex(),direction=p.inferred_direction(),t=p.timestamp_seconds) for p in outputs])
        return outputs
    protocol.V576TransportReassembler.feed=transport
    error=None
    try:
        events=source.poll()
        event_count=len(events)
    except Exception as exc:
        error=repr(exc);event_count=None
        raise
    finally:
        source.close()
        after={f:hashlib.sha256((CANDIDATE/f).read_bytes()).hexdigest() for f in MODULES.values()}
        OUT.mkdir(parents=True)
        (OUT/'trace.json').write_text(json.dumps(clean(dict(capture=str(CAP),runtime=metadata,
            before_hashes=before,after_hashes=after,runtime_unchanged=before==after,
            elapsed_seconds=round(time.monotonic()-started,3),feed_packets=count[0],event_count=event_count,
            error=error,trace=trace)),ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(output=str(OUT/'trace.json'),rows=len(trace),seconds=round(time.monotonic()-started,2),runtime=metadata,error=error),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
