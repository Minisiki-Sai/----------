"""Offline timing diagnostics only; no live capture, speech, scoring or uploads."""
import argparse
import gc
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/verification/voice_latency_1019'


def quantiles(values):
    values = sorted(values)
    return {k: values[min(len(values)-1, int((len(values)-1)*p))] if values else 0
            for k, p in [('p50', .5), ('p95', .95), ('p99', .99), ('max', 1)]} | {'count': len(values)}


def run(path):
    from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
    from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
    from dragon_nest_builder.live_dps.voice_signals import VoiceSignalFeed
    costs, signals, slow, collections = [], [], [], []
    ctx = {'start': 0, 'stamp': 0, 'index': 0, 'virtual_end': 0, 'wait': 0,
           'regressions': 0, 'last': 0, 'origin': None}
    gc_start = {}
    def gc_hook(phase, info):
        generation = info['generation']
        if phase == 'start': gc_start[generation] = time.perf_counter()
        elif generation in gc_start:
            duration = (time.perf_counter()-gc_start.pop(generation))*1000
            if duration >= 5:
                collections.append({'packet': ctx['index'], 'timestamp': ctx['stamp'],
                                    'generation': generation, 'ms': duration})
    original_feed = _V576PacketEventDecoder.feed_events
    original_publish = VoiceSignalFeed.publish
    def publish(self, row):
        before = self._sequence
        result = original_publish(self, row)
        if self._sequence != before:
            signals.append(dict(row, packet=ctx['index'], packet_wall_seconds=ctx['origin']+ctx['stamp'],
                                packet_to_publish_ms=(time.perf_counter()-ctx['start'])*1000,
                                simulated_wait_ms=ctx['wait']))
        return result
    def feed(self, packet):
        stamp = packet.timestamp_seconds
        if ctx['origin'] is None: ctx['origin'] = stamp
        ctx['index'] += 1
        ctx['stamp'] = stamp-ctx['origin']
        ctx['start'] = time.perf_counter()
        if stamp < ctx['last']-.001: ctx['regressions'] += 1
        ctx['last'] = max(ctx['last'], stamp)
        ctx['wait'] = max(0, ctx['virtual_end']-stamp)*1000
        result = original_feed(self, packet)
        elapsed = time.perf_counter()-ctx['start']
        costs.append(elapsed*1000)
        ctx['virtual_end'] = max(ctx['virtual_end'], stamp)+elapsed
        if elapsed >= .025:
            slow.append({'packet': ctx['index'], 'timestamp': ctx['stamp'], 'ms': elapsed*1000,
                         'transport': packet.transport, 'bytes': len(packet.payload),
                         'events': len(result) if hasattr(result, '__len__') else None})
        return result
    _V576PacketEventDecoder.feed_events = feed
    VoiceSignalFeed.publish = publish
    gc.callbacks.append(gc_hook)
    start = time.perf_counter()
    source = None
    try:
        source = V576DiagnosticReplaySource(path, ROOT/'dragon_nest_builder/data')
        initialized = time.perf_counter()
        source._decoder.voice_signal_feed = VoiceSignalFeed()
        events = source.poll()
        result = dict(file=path.name, seconds=time.perf_counter()-start, origin=source._decoder.first_timestamp,
                      init_seconds=initialized-start, packets=len(costs), events=len(events),
                      packet_ms=quantiles(costs),
                      signal_publish_ms=quantiles([s['packet_to_publish_ms'] for s in signals]),
                      timestamp_regressions=ctx['regressions'],
                      capture_integrity=source.capture_integrity,
                      signals=signals, slow_packets=slow, gc_pauses=collections)
        if ctx['regressions']:
            for signal in signals: signal.pop('simulated_wait_ms', None)
        (OUT/(path.stem+'.json')).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in {'signals','slow_packets','gc_pauses','capture_integrity'}}
                         | {'signals':len(signals), 'slow_packets':len(slow),
                            'gc_max_ms':max([g['ms'] for g in collections], default=0)}, ensure_ascii=False), flush=True)
    finally:
        gc.callbacks.remove(gc_hook)
        _V576PacketEventDecoder.feed_events = original_feed
        VoiceSignalFeed.publish = original_publish
        if source: source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.capture:
        run(args.capture)
    else:
        paths = sorted(Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics').glob('1019噬魂*.dpscap'))
        print('captures',len(paths),flush=True)
        for path in paths:
            subprocess.run([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),'--capture',str(path)], check=True, cwd=ROOT)
