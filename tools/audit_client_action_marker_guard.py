"""Compare the added client action guard against five local raw captures."""
import ast
from collections import Counter
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.sources import _load_skill_catalog


def main():
    tree = ast.parse(inspect.getsource(protocol.decode_v576_payload))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == 'required_action_markers'):
            pairs = [(k, v) for k, v in zip(node.value.keys, node.value.values)
                     if k.value not in {73, 80}]
            node.value.keys = [k for k, v in pairs]
            node.value.values = [v for k, v in pairs]
    namespace = dict(vars(protocol))
    exec(compile(ast.fix_missing_locations(tree), '<before-client-marker-guard>', 'exec'), namespace)
    before = namespace['decode_v576_payload']
    catalog = frozenset(_load_skill_catalog(str(ROOT / 'dragon_nest_builder/data')))
    directory = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics')
    reports = []
    for name in ('1013bluemoon识别错误', '1004bluemoon丢包01', '1004bluemoon丢包02',
                 '1004安小呆丢包01', '1004安小呆丢包02'):
        markers, changes = Counter(), []
        counts = [0, 0]
        for timestamp, adapter, length, frame in read_trace(directory / (name + '.dpscap')):
            if adapter >= 240:
                continue
            packet = protocol.decode_captured_frame(timestamp, frame, length)
            if packet is None or packet.transport != 'UDP' or packet.inferred_direction() != 'client_to_server':
                continue
            for payload in protocol.split_v576_payload(packet.payload):
                phase = {(73, b'\xfe\x5c\x46\x00'): 4,
                         (80, b'\x28\x8a\x4d\x00'): 10}.get((len(payload), payload[5:9]))
                if phase is None:
                    continue
                clear = protocol._decode_v576_body(payload, phase)
                markers[f'{len(payload)}:{clear[18:21].hex()}'] += 1
                results = [fn(payload, 'client_to_server', catalog, catalog)
                           for fn in (before, protocol.decode_v576_payload)]
                for i, result in enumerate(results):
                    counts[i] += bool(result and result.kind in {'skill_cast', 'skill_action'})
                if results[0] != results[1]:
                    changes.append(dict(timestamp=timestamp, before=repr(results[0]), after=repr(results[1])))
        report = dict(capture=name, markers=dict(markers), casts_before=counts[0],
                      casts_after=counts[1], differences=changes)
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    (ROOT / 'work/1013_client_marker_corpus.json').write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
