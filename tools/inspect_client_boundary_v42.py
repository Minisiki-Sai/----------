"""Read a pinned OFFLINE guest snapshot; never execute the client or a syscall."""
from hashlib import sha256
import argparse
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.client_guest_snapshot_v41 import restore_snapshot
from tools.emulate_client_sections_v40 import v39


def inspect(experiment):
    e = experiment
    reg = lambda name: e.uc.reg_read(getattr(v39.x86, 'UC_X86_REG_' + name))
    number = reg('RAX') & 0xffffffff
    candidates = []
    for address, names in sorted(e.export_names.items()):
        if not any(name.startswith('ntdll.dll!Nt') or name.startswith('ntdll.dll!Zw') for name in names):
            continue
        body = bytes(e.uc.mem_read(address, 32))
        # Require the actual file's canonical x64 service stub, not a hardcoded
        # service-number table borrowed from a different Windows build.
        if body[:4] == bytes.fromhex('4c8bd1b8') and struct.unpack_from('<I', body, 4)[0] == number and b'\x0f\x05' in body[8:]:
            candidates.append(dict(address=hex(address), names=names, bytes=body.hex()))
    rip = reg('RIP')
    instructions = [dict(address=hex(i.address), bytes=i.bytes.hex(), instruction=i.mnemonic+' '+i.op_str)
                    for i in e.dis.disasm(bytes(e.uc.mem_read(rip, 96)), rip)]
    args = [reg(name) for name in ('R10', 'RDX', 'R8', 'R9')]
    sp = reg('RSP')
    args.extend(e.u64(sp + 0x28 + 8*n) for n in range(7))
    result = dict(stop=e.stop_reason, rip=hex(rip), service_number=number,
                  candidates=candidates, instructions=instructions, arguments=[hex(x) for x in args])
    # Bounded decoding only after a unique Nt/Zw alias identity is established.
    names = {n.split('!')[-1][2:] for item in candidates for n in item['names'] if n.split('!')[-1][:2] in ('Nt', 'Zw')}
    if names == {'OpenFile'}:
        oa = e.guest_read(args[2], 48)
        length, root, unicode_ptr, attributes, sd, qos = struct.unpack('<I4xQQI4xQQ', oa)
        length_u, maximum_u, buffer = struct.unpack('<HH4xQ', e.guest_read(unicode_ptr, 16))
        if length != 48 or length_u > maximum_u or length_u > 1024 or length_u % 2:
            raise ValueError('unsupported object name metadata')
        name = e.guest_read(buffer, length_u).decode('utf-16-le', errors='strict')
        result['open_file'] = dict(name=name, root=hex(root), attributes=hex(attributes),
                                  security_descriptor=hex(sd), qos=hex(qos), access=hex(args[1]),
                                  share=hex(args[4]&0xffffffff), options=hex(args[5]&0xffffffff),
                                  stack_option_slot=hex(args[5]),
                                  handle_output=hex(args[0]), io_status_output=hex(args[3]))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--full-path-v42', action='store_true')
    args = parser.parse_args()
    if args.full_path_v42:
        from tools.continue_client_v42 import restore
        e = restore(v39.CLIENT.read_bytes(), args.snapshot.read_bytes(), args.sha256)
    else:
        e = restore_snapshot(v39.CLIENT.read_bytes(), args.snapshot.read_bytes(), args.sha256)
    result = inspect(e)
    result['snapshot_sha256'] = args.sha256
    result['client_sha256'] = e.sha
    result['source_sha256'] = sha256(Path(__file__).read_bytes()).hexdigest()
    out = ROOT/'outputs/client_protection_v42_20260908'
    out.mkdir(exist_ok=True)
    name = 'boundary_fullpath.json' if args.full_path_v42 else 'boundary.json'
    (out/name).write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
