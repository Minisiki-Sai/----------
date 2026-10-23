"""Read-only scene-wire summaries for Nightmare Sea captures."""
import argparse
import json
from pathlib import Path
from tools.analyze_scene_transition_packets import _read_logical_packets, _tcp_plaintext


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('captures', nargs='+', type=Path)
    args = parser.parse_args()
    for path in args.captures:
        packets = _read_logical_packets(path)
        origin = packets[0].timestamp_seconds
        end = packets[-1].timestamp_seconds-origin
        rows = []
        for p in packets:
            stamp = p.timestamp_seconds-origin
            body = _tcp_plaintext(p)
            if body is None:
                continue
            opcode = body[2:4].hex()
            offsets = []
            needle = (11120).to_bytes(4,'little')
            at = body.find(needle)
            while at >= 0:
                offsets.append(at)
                at = body.find(needle,at+1)
            if opcode in ('0313','0201','0318','0301','0303','060b') or offsets:
                rows.append(dict(t=round(stamp,3),op=opcode,size=len(body),direction=p.inferred_direction(),
                                 source=f'{p.source_ip}:{p.source_port}',map11120=offsets,
                                 prefix=body[:48].hex(' '),
                                 wire=p.payload.hex() if opcode == '060b' else ''))
        result = dict(file=path.name,duration=end,rows=rows)
        out=Path('outputs/verification/sea_returns');out.mkdir(parents=True,exist_ok=True)
        (out/(path.stem+'.wire.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(path.name,'duration',round(end,3),'rows',len(rows),flush=True)
        for r in rows:
            if r['t']>end-90 or r['op'] in ('0313','0201'):
                print(json.dumps(r,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
