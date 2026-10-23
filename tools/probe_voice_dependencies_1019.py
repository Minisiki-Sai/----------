"""Read-only device enumeration and offline audio-onset measurement."""
import array
import ctypes
import json
import math
import sys
import time
import wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.npcap import NpcapPacketStream,find_npcap_dll,_PcapInterface

out=ROOT/'outputs/verification/voice_latency_1019'
obj=NpcapPacketStream.__new__(NpcapPacketStream)
obj._pcap=ctypes.CDLL(str(find_npcap_dll()))
obj._pcap.pcap_findalldevs.argtypes=[ctypes.POINTER(ctypes.POINTER(_PcapInterface)),ctypes.c_char_p]
obj._pcap.pcap_findalldevs.restype=ctypes.c_int
obj._pcap.pcap_freealldevs.argtypes=[ctypes.POINTER(_PcapInterface)]
obj._pcap.pcap_freealldevs.restype=None
samples=[]
for i in range(5):
    start=time.perf_counter()
    count=len(obj._enumerate_devices())
    row={'run':i+1,'ms':(time.perf_counter()-start)*1000,'devices':count}
    samples.append(row)
    print(json.dumps(row),flush=True)
manifest=json.loads((ROOT/'frontend/public/voices/qwen3/manifest.json').read_text(encoding='utf-8'))
audio=[]
for voice in manifest['voices']:
    for text, ident in manifest['phrases'].items():
        with wave.open(str(ROOT/f'frontend/public/voices/qwen3/{voice}/{ident}.wav'),'rb') as wav:
            assert wav.getsampwidth()==2
            rate=wav.getframerate()*wav.getnchannels()
            data=array.array('h',wav.readframes(wav.getnframes()))
        step=max(1,rate//100)
        # First 10-ms block over -45 dBFS RMS; measures acoustic onset, not word recognition.
        threshold=32768*10**(-45/20)
        onset=next((i/rate for i in range(0,len(data),step)
                    if math.sqrt(sum(x*x for x in data[i:i+step])/len(data[i:i+step]))>threshold),len(data)/rate)
        audio.append(dict(voice=voice,text=text,onset_ms=onset*1000,duration_ms=len(data)/rate*1000))
(out/'dependencies.json').write_text(json.dumps(dict(enumeration=samples,audio=audio),ensure_ascii=False),encoding='utf-8')
print('audio worst',json.dumps(sorted(audio,key=lambda x:x['onset_ms'],reverse=True)[:8],ensure_ascii=False),flush=True)
