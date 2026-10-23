"""Offline full-prefix search for exact object tokens; not attribution.

Encrypted token prefilter is lossless across the 13 XOR phases. A candidate
is retained only after unique whole-message typed-boundary validation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from tools.analyze_npcap_trace import read_trace
from tools.probe_compact_entity_attack_selectors import walk

KEY = bytes.fromhex('c8bfcda4943f2642fd8a723474')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture', type=Path)
    ap.add_argument('--objects', required=True)
    ap.add_argument('--end', type=float, required=True)
    ap.add_argument('--types', help='Optional comma separated typed record kinds (integers)')
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    kinds = {int(s, 0) for s in a.types.split(',')} if a.types else None
    tokens = {int(s, 0): int(s, 0).to_bytes(4, 'little') for s in a.objects.split(',')}
    encoded = tuple({bytes(v ^ KEY[(j+r) % 13] for j, v in enumerate(t))
                     for t in tokens.values() for r in range(13)})
    first = None
    rows = []
    rejected = []
    counts = Counter()
    seen = set()
    for frame, (t, _adapter, original, raw) in enumerate(read_trace(a.capture), 1):
        if first is None:
            first = t
        elapsed = t-first
        if elapsed > a.end:
            break
        packet = p.decode_captured_frame(t, raw, original)
        if not packet or packet.transport != 'UDP':
            continue
        for message, wire in enumerate(p.split_v576_payload(packet.payload)):
            counts['udp_messages'] += 1
            if not any(e in wire[9:] for e in encoded):
                continue
            counts['token_prefilter'] += 1
            parsed = walk(wire)
            if not parsed:
                counts['not_unique_typed_message'] += 1
                for phase in range(13):
                    body = p._decode_v576_body_uncached(wire, phase)
                    matches = {str(obj): body.find(token, 9) for obj, token in tokens.items()
                               if token in body[9:]}
                    if matches:
                        rejected.append(dict(frame=frame, message=message,
                            elapsed_ms=round(elapsed*1000), phase=phase, matches=matches,
                            direction=packet.inferred_direction(), wire_hex=wire.hex(),
                            plaintext_hex=body.hex(), evidence='unframed_token_only_not_identity'))
                continue
            phase, body, records = parsed
            for actor, m, op, n in records:
                if kinds is not None and op not in kinds:
                    continue
                rb = body[m-4:m+3+n]
                hits = {str(obj): [i for i in range(len(rb)-3) if rb[i:i+4] == token]
                        for obj, token in tokens.items() if token in rb}
                if not hits:
                    continue
                key = (round(elapsed*1000), rb)
                if key in seen:
                    counts['same_ms_identical_record'] += 1
                    continue
                seen.add(key)
                rows.append(dict(frame=frame, message=message, elapsed_ms=round(elapsed*1000),
                                 direction=packet.inferred_direction(), observer=int.from_bytes(body[10:14], 'little'),
                                 actor=actor, marker=m, op=op, length=n, phase=phase,
                                 occurrences=hits, record_hex=rb.hex()))
                counts[f'typed_{op:02x}_{n}'] += 1
    result = dict(schema='object-full-history-v60', capture=str(a.capture), end_seconds=a.end,
                  objects=list(tokens), counts=dict(counts), rows=rows, rejected_candidates=rejected,
                  attribution_claim=False, production_changed=False)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(counts=counts, output=str(a.output)), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
