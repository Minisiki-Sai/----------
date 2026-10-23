"""Continue v39 with guest-only read-only KnownDll section models.

No NtOpenSection/NtMapViewOfSection or other host native call is made. Only
already-hashed, on-disk system PE bytes enter the simulated address space.
Unknown section names/flags/addresses stay blocked. Guest mappings cannot run.
"""
from hashlib import sha256
from collections import Counter, deque
import argparse
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import emulate_client_entry_v39 as v39

OUT = ROOT/'outputs/client_protection_v40_20260908'
VIEW_BASE = 0x900000000
VIEW_STRIDE = 0x1000000
KNOWN_NAMES = {'\\knowndlls\\'+n: n for n in ('ntdll.dll', 'kernel32.dll', 'kernelbase.dll')}


class SectionExperiment(v39.EntryExperiment):
    def __init__(self, *args, enable_sections=True, **kwargs):
        super().__init__(*args, **kwargs)
        if self.base < VIEW_BASE+VIEW_STRIDE*8 and self.base+self.size > VIEW_BASE:
            raise ValueError('image collides with section view region')
        self.enable_sections = enable_sections
        self.section_handles = {}
        self.next_section_handle = 0xb000
        self.views = []
        self.section_calls = []
        self.model_writes = []
        self.static_reads = {}
        for module in self.readonly_modules:
            self.watch_static_image(module['base'], module['size'], module['name'], 'loader')

    def watch_static_image(self, base, size, name, kind):
        state = dict(name=name, kind=kind, base=base, size=size, reads=0,
            bytes=0, minimum=None, maximum=None, pages=Counter(), tail=deque(maxlen=8))
        self.static_reads[base] = state
        def read(uc, access, address, width, value, user):
            offset = address-base
            state['reads'] += 1
            state['bytes'] += width
            state['minimum'] = offset if state['minimum'] is None else min(offset, state['minimum'])
            state['maximum'] = max(offset+width, state['maximum'] or 0)
            state['pages'][offset//v39.PAGE] += 1
            state['tail'].append(dict(offset=offset, size=width,
                instruction_rva=uc.reg_read(v39.x86.UC_X86_REG_RIP)-self.base))
        state['hook'] = self.uc.hook_add(v39.unicorn.UC_HOOK_MEM_READ, read, begin=base, end=base+size-1)

    def guest_range(self, address, size, initialized=True):
        if not 0 < size <= 4096:
            raise ValueError('guest metadata read/write bound')
        if self.base <= address < address+size <= self.base+self.size:
            return
        if v39.STACK_BASE <= address < address+size <= v39.STACK_BASE+v39.STACK_SIZE:
            return
        for allocation in self.allocations:
            lo = address-allocation['base']
            if 0 <= lo < lo+size <= allocation['size']:
                if initialized and not any(a <= lo and lo+size <= b for a,b in allocation['initialized']):
                    raise ValueError('uninitialized guest argument')
                return
        raise ValueError('argument outside owned guest data')

    def guest_read(self, address, size):
        self.guest_range(address, size)
        return bytes(self.uc.mem_read(address, size))

    def guest_write(self, address, data):
        self.guest_range(address, len(data), initialized=False)
        self.uc.mem_write(address, data)
        # API-model writes are separate from unpacker-originated image writes.
        self.model_writes.append(dict(address=hex(address), size=len(data)))
        for allocation in self.allocations:
            offset = address-allocation['base']
            if 0 <= offset < offset+len(data) <= allocation['size']:
                spans = sorted(allocation['initialized']+[(offset, offset+len(data))])
                merged = []
                for lo,hi in spans:
                    if merged and lo <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                    else:
                        merged.append((lo,hi))
                allocation['initialized'] = merged

    def u64(self, address):
        return struct.unpack('<Q', self.guest_read(address, 8))[0]

    def arguments(self, number):
        registers = [v39.x86.UC_X86_REG_RCX, v39.x86.UC_X86_REG_RDX,
            v39.x86.UC_X86_REG_R8, v39.x86.UC_X86_REG_R9]
        args = [self.uc.reg_read(r) for r in registers[:number]]
        sp = self.uc.reg_read(v39.x86.UC_X86_REG_RSP)
        args.extend(self.u64(sp+0x28+i*8) for i in range(max(0, number-4)))
        return args

    def checked_return(self):
        sp = self.uc.reg_read(v39.x86.UC_X86_REG_RSP)
        if not v39.STACK_BASE <= sp <= v39.STACK_BASE+v39.STACK_SIZE-8:
            raise ValueError('external return stack outside synthetic stack')
        ret = self.u64(sp)
        if not self.base <= ret < self.base+self.size:
            raise ValueError('external return outside main image')
        return sp, ret

    def checked_outputs(self, *addresses):
        sp, ret = self.checked_return()
        for index, address in enumerate(addresses):
            self.guest_range(address, 8, initialized=False)
            if address < sp+8 and address+8 > sp:
                raise ValueError('output overlaps return slot')
            if any(address < other+8 and address+8 > other for other in addresses[:index]):
                raise ValueError('overlapping output buffers')

    def finish_call(self, status=0):
        sp, ret = self.checked_return()
        self.uc.reg_write(v39.x86.UC_X86_REG_RAX, status)
        self.uc.reg_write(v39.x86.UC_X86_REG_RSP, sp+8)
        self.uc.reg_write(v39.x86.UC_X86_REG_RIP, ret)
        self.stop_reason = None
        return True

    def open_section(self, record):
        output, access, oa = self.arguments(3)
        raw = self.guest_read(oa, 48)
        length = struct.unpack_from('<I', raw)[0]
        root, nameptr = struct.unpack_from('<QQ', raw, 8)
        attributes = struct.unpack_from('<I', raw, 24)[0]
        security, qos = struct.unpack_from('<QQ', raw, 32)
        if length != 48 or root or security or qos or attributes not in (0, 0x40):
            raise ValueError('unsupported object attributes')
        descriptor = self.guest_read(nameptr, 16)
        length, maximum = struct.unpack_from('<HH', descriptor)
        buffer = struct.unpack_from('<Q', descriptor, 8)[0]
        if not 0 < length <= maximum <= 512 or length % 2:
            raise ValueError('invalid section name length')
        name = self.guest_read(buffer, length).decode('utf-16-le', errors='strict')
        if not name.isascii():
            raise ValueError('non-ASCII name outside strict section allowlist')
        module_name = KNOWN_NAMES.get(name.casefold())
        if module_name and attributes == 0 and name != '\\KnownDlls\\'+module_name:
            raise ValueError('case-sensitive section name mismatch')
        record.update(access=access, attributes=attributes, name=name if module_name else '<not-allowlisted>', name_sha256=sha256(name.encode('utf-16-le')).hexdigest())
        if not module_name or access not in (4, 5, 0xc, 0xd):
            raise ValueError('section name/access not supported by readonly model')
        module = next((m for m in self.readonly_modules if m['name'].casefold() == module_name), None)
        if module is None:
            raise ValueError('no independently loaded static PE for section')
        self.checked_outputs(output)
        if not self.enable_sections:
            return False
        if len(self.section_handles) >= 8:
            raise ValueError('section handle limit')
        handle = self.next_section_handle
        self.next_section_handle += 4
        self.section_handles[handle] = dict(module)
        self.guest_write(output, struct.pack('<Q', handle))
        record.update(modelled=True, handle=hex(handle), file_sha256=module['sha256'])
        return self.finish_call()

    def map_section(self, record):
        handle, process, baseptr, zero, commit, offsetptr, sizeptr, inherit, allocation, protection = self.arguments(10)
        module = self.section_handles.get(handle)
        requested_base, requested_size = self.u64(baseptr), self.u64(sizeptr)
        offset = self.u64(offsetptr) if offsetptr else 0
        record.update(handle=hex(handle), process=hex(process), requested_base=hex(requested_base),
            requested_size=requested_size, offset=offset, protection=protection, allocation=allocation,
            zero_bits=zero, commit_size=commit, inherit=inherit)
        if module is None or process != 0xffffffffffffffff or requested_base or requested_size not in (0,module['size']) or zero or commit or offset or inherit not in (1,2) or allocation or protection not in (2, 0x20):
            raise ValueError('unsupported section mapping contract')
        if len(self.views) >= 8 or module['size'] > VIEW_STRIDE:
            raise ValueError('section mapping budget')
        self.checked_outputs(baseptr, sizeptr)
        if not self.enable_sections:
            return False
        base = VIEW_BASE+VIEW_STRIDE*len(self.views)
        self.uc.mem_map(base, module['size'], v39.unicorn.UC_PROT_READ)
        data = bytes(self.uc.mem_read(module['base'], module['size']))
        self.uc.mem_write(base, data)
        self.views.append(dict(base=base, size=module['size'], name=module['name'], source_sha256=module['sha256']))
        self.watch_static_image(base, module['size'], module['name'], 'section_view')
        self.guest_write(baseptr, struct.pack('<Q', base))
        self.guest_write(sizeptr, struct.pack('<Q', module['size']))
        record.update(modelled=True, mapped_base=hex(base), mapped_size=module['size'],
            restriction='Read-only PE-file image; no export execution, host mapping or assumed relocation-dependent data semantics.')
        return self.finish_call()

    def unmap_section(self, record):
        process, base = self.arguments(2)
        view = next((v for v in self.views if v['base']==base and not v.get('unmapped')), None)
        record.update(process=hex(process), base=hex(base))
        if process != 0xffffffffffffffff or view is None:
            raise ValueError('unknown section view')
        self.checked_return()
        if not self.enable_sections:
            return False
        self.uc.mem_unmap(base, view['size'])
        self.uc.hook_del(self.static_reads[base]['hook'])
        view['unmapped'] = True
        record['modelled'] = True
        return self.finish_call()

    def close_section(self, record):
        handle = self.arguments(1)[0]
        record['handle'] = hex(handle)
        if handle not in self.section_handles:
            raise ValueError('cannot close an unknown handle')
        self.checked_return()
        if not self.enable_sections:
            return False
        del self.section_handles[handle]
        record['modelled'] = True
        return self.finish_call()

    def model_heap_boundary(self):
        if super().model_heap_boundary():
            return True
        if self.stop_reason.get('access') != v39.unicorn.UC_MEM_FETCH_PROT:
            return False
        symbols = self.stop_reason.get('symbols', [])
        contracts = {'NtOpenSection': self.open_section, 'NtMapViewOfSection': self.map_section,
            'NtUnmapViewOfSection': self.unmap_section, 'NtClose': self.close_section}
        for function, method in contracts.items():
            aliases = {'ntdll.dll!'+function, 'ntdll.dll!Zw'+function[2:]}
            if symbols and set(symbols) <= aliases:
                record = dict(function=function, modelled=False)
                self.section_calls.append(record)
                try:
                    if len(self.section_calls) > 64:
                        raise ValueError('section call limit')
                    handled = method(record)
                except (ValueError, v39.unicorn.UcError) as exc:
                    record['rejected'] = str(exc)
                    handled = False
                if not handled:
                    self.stop_reason['section_contract'] = record
                print(json.dumps(dict(section_contract=record), ensure_ascii=False), flush=True)
                return handled
        if any(symbol in {'ntdll.dll!'+name for name in contracts} for symbol in symbols):
            self.stop_reason['ambiguous_section_symbols'] = list(symbols)
        return False

    def run(self, resume=False):
        report = super().run(resume=resume)
        report.update(section_calls=self.section_calls, section_views=self.views,
            api_model_writes=self.model_writes, section_model_enabled=self.enable_sections)
        report['static_reads'] = [{k:(list(v) if k=='tail' else dict(v) if k=='pages' else v)
            for k,v in state.items() if k!='hook'} for state in self.static_reads.values()]
        report['guest_registers'] = {name:hex(self.uc.reg_read(getattr(v39.x86, 'UC_X86_REG_'+name)))
            for name in ('RIP','RSP','RAX','RBX','RCX','RDX','RSI','RDI','RBP','R8','R9','R10','R11','R12','R13','R14','R15','EFLAGS')}
        report['synthetic_stack_sha256'] = sha256(bytes(self.uc.mem_read(v39.STACK_BASE, v39.STACK_SIZE))).hexdigest()
        report['guest_heap_sha256'] = [sha256(bytes(self.uc.mem_read(a['base'],a['size']))).hexdigest() for a in self.allocations]
        report['hot_blocks'] = self.blocks.most_common(16)
        return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--inspect-only', action='store_true')
    parser.add_argument('--segments', type=int, choices=range(1,31), default=5)
    parser.add_argument('--output-name', choices=['section_emulation.json','section_emulation_tracked.json'], default='section_emulation_tracked.json')
    args=parser.parse_args()
    sources = [Path(__file__), Path(v39.__file__)]
    source_hashes = {p.name:sha256(p.read_bytes()).hexdigest() for p in sources}
    raw=v39.CLIENT.read_bytes()
    experiment=SectionExperiment(raw, instruction_limit=50000000, seconds=40,
        module_metadata=True, heap_handle=True, guest_heap=True,
        os_version=tuple(sys.getwindowsversion().platform_version), shared_version=True,
        enable_sections=not args.inspect_only)
    checkpoints=[]
    OUT.mkdir(exist_ok=True)
    for segment in range(args.segments):
        report=experiment.run(resume=segment>0)
        checkpoint=dict(segment=segment+1,instructions=report['instructions'],seconds=report['total_seconds'],
            stop=report['stop'],modified_sections=report['modified_sections'],
            section_calls=len(report['section_calls']),
            static_reads=[{k:s[k] for k in ('base','kind','reads','bytes','minimum','maximum')} for s in report['static_reads']],
            guest_registers=report['guest_registers'], synthetic_stack_sha256=report['synthetic_stack_sha256'],
            guest_heap_sha256=report['guest_heap_sha256'])
        checkpoints.append(checkpoint)
        print(json.dumps(checkpoint,ensure_ascii=False),flush=True)
        report.update(checkpoints=checkpoints, source_hashes=source_hashes,
            sources_changed=any(sha256(p.read_bytes()).hexdigest()!=source_hashes[p.name] for p in sources))
        (OUT/args.output_name).write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
        if report['stop']['reason'] not in ('instruction_limit','timeout') or report['error']:
            break
    if sha256(v39.CLIENT.read_bytes()).hexdigest()!=sha256(raw).hexdigest():
        raise RuntimeError('client changed externally during experiment')
