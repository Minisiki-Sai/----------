"""Locate exact damage amounts in raw UDP, independent of replay attribution."""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import (
    decode_captured_frame, _v576_wire_may_contain_marker, _decode_v576_body,
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE, decode_v576_compound_damage,
    decode_v576_payload, decode_v576_projectile_damage,
)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('capture',type=Path)
    ap.add_argument('--amounts',required=True)
    ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args()
    amounts=[int(v) for v in args.amounts.split(',')]
    markers=tuple(v.to_bytes(8,'little') for v in amounts)
    found=defaultdict(list)
    for stamp,adapter,original,frame in read_trace(args.capture):
        if adapter>=251:continue
        packet=decode_captured_frame(stamp,frame,original)
        if packet is None or packet.transport!='UDP' or packet.inferred_direction()!='server_to_client':continue
        payload=packet.payload
        if len(payload)<9 or not _v576_wire_may_contain_marker(payload,markers):continue
        phase=_V576_XOR_PHASE_BY_SIGNATURE_BYTE[payload[5]]
        decoded=_decode_v576_body(payload,phase)
        matching=[v for v,m in zip(amounts,markers) if m in decoded[9:]]
        if not matching:continue
        primary=decode_v576_payload(payload,packet.inferred_direction())
        compound=decode_v576_compound_damage(payload,packet.inferred_direction(),frozenset())
        projectile=decode_v576_projectile_damage(payload,packet.inferred_direction())
        detail={'stamp':stamp,'length':len(payload),'signature':payload[5:9].hex(),
                'phase':phase,'amountOffsets':{v:decoded.find(v.to_bytes(8,'little'),9) for v in matching},
                'primary':str(primary),'compound':str(compound),'projectile':str(projectile),
                'decodedHex':decoded.hex(),'wireHex':payload.hex()}
        for v in matching:
            if len(found[v])<4:found[v].append(detail)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(found,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'capture':args.capture.name,'matches':{v:len(found[v]) for v in amounts}},ensure_ascii=False))


if __name__=='__main__':main()
