"""Inspect bounded end-of-fight physical records without changing the decoder."""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

source = V576DiagnosticReplaySource(Path(sys.argv[1]), Path('dragon_nest_builder/data'))
origin = None
rows = []
try:
    for timestamp, adapter, packet, _, _ in source._read_predecoded_frames():
        if not hasattr(packet, 'payload'):
            continue
        if origin is None:
            origin = timestamp
        elapsed = timestamp - origin
        if not 733 <= elapsed <= 737 or packet.transport.upper() != 'UDP':
            continue
        inventory = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
        for record in inventory.typed_records:
            if int.from_bytes(record.record_bytes[:4], 'little') == 177:
                rows.append(dict(time=elapsed, kind=record.record_type, size=record.declared_length,
                                 raw=record.record_bytes.hex()))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
finally:
    source.close()
