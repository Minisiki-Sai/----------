"""Bounded offline entry-stub experiment, NOT a Windows/game process loader.

Only an explicit PE byte image and a synthetic stack enter Unicorn memory.
There are no host API callbacks, OS/DLL resolution, files/network accessible to
guest instructions, process handles, anti-cheat modifications or persisted game
changes. Imports, system instructions, interrupts and unknown addresses stop it.
No emulator output is accepted as a runtime attribution certificate.
"""
from collections import Counter, deque
from hashlib import sha256
import argparse
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tmp/reverse_tools_v39'))
import capstone
import pefile
import unicorn
from unicorn import x86_const as x86

CLIENT = Path('D:/DragonNest/Reborn/dragonnest_reborn.exe')
OUT = ROOT / 'outputs/client_protection_v39_20260908'
PAGE = 4096
MAX_IMAGE = 96 * 1024 * 1024
STACK_BASE = 0x300000000
STACK_SIZE = 2 * 1024 * 1024
TRAP_BASE = 0x400000000
STOP_RETURN = 0x4000ff000
TEB_BASE = 0x500000000
PEB_BASE = TEB_BASE + PAGE
LDR_BASE = TEB_BASE + PAGE*2
HEAP_BASE = 0x800000000
HEAP_BUDGET = 64 * 1024 * 1024
SHARED_BASE = 0x7ffe0000


def align(n):
    return (n + PAGE - 1) & ~(PAGE - 1)


def checked_image(raw, require_entry=True):
    pe = pefile.PE(data=raw)
    size = pe.OPTIONAL_HEADER.SizeOfImage
    if pe.FILE_HEADER.Machine != 0x8664 or not 0 < size <= MAX_IMAGE or size % PAGE:
        raise ValueError('unsupported machine or image size')
    base = pe.OPTIONAL_HEADER.ImageBase
    if base % PAGE or not 0 < base < base+size < 2**63:
        raise ValueError('unaligned image base')
    entry = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    if not 0 <= entry < size or (require_entry and not entry):
        raise ValueError('entrypoint outside declared image')
    reserved = [(STACK_BASE, STACK_SIZE), (TRAP_BASE, 0x100000),
                (TEB_BASE, PAGE*6), (0x600000000, 0x3000000),
                (0x700000000, PAGE), (HEAP_BASE, HEAP_BUDGET), (SHARED_BASE, PAGE)]
    if any(base < lo+span and base+size > lo for lo, span in reserved):
        raise ValueError('image overlaps reserved emulator addresses')
    image = bytearray(align(size))
    headers = pe.OPTIONAL_HEADER.SizeOfHeaders
    if not 0 < headers <= min(size, len(raw)):
        raise ValueError('invalid header extent')
    image[:headers] = raw[:headers]
    occupied = [(0, headers)]
    for section in pe.sections:
        rva, length, offset = section.VirtualAddress, section.SizeOfRawData, section.PointerToRawData
        extent = max(length, section.Misc_VirtualSize)
        if rva + extent > size or offset + length > len(raw):
            raise ValueError('invalid section extent')
        if any(rva < end and rva + extent > begin for begin, end in occupied):
            raise ValueError('overlapping image sections')
        occupied.append((rva, rva + extent))
        if length:
            image[rva:rva + length] = raw[offset:offset + length]
    return pe, bytes(image)


class EntryExperiment:
    def __init__(self, raw, instruction_limit=500000, seconds=20, synthetic_image_pointer=False, module_metadata=False, heap_handle=False, guest_heap=False, os_version=None, shared_version=False):
        if not 1 <= instruction_limit <= 50000000 or not 0 < seconds <= 45:
            raise ValueError('experiment bounds exceeded')
        self.pe, self.initial = checked_image(raw)
        self.sha = sha256(raw).hexdigest()
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.size = len(self.initial)
        self.limit, self.seconds = instruction_limit, seconds
        self.uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
        self.uc.mem_map(self.base, self.size)
        self.uc.mem_write(self.base, self.initial)
        # Synthetic stack is non-executable; no actual host stack is exposed.
        self.uc.mem_map(STACK_BASE, STACK_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
        sp = STACK_BASE + STACK_SIZE - 0x108
        self.uc.mem_write(sp, struct.pack('<Q', STOP_RETURN))
        self.uc.reg_write(x86.UC_X86_REG_RSP, sp)
        self.uc.reg_write(x86.UC_X86_REG_EFLAGS, 0x202)
        self.synthetic_image_pointer = synthetic_image_pointer or module_metadata
        self.module_metadata = module_metadata
        self.heap_handle = heap_handle
        self.guest_heap = guest_heap
        self.os_version = os_version
        self.shared_version = shared_version
        if shared_version and os_version is None:
            raise ValueError('shared version requires observed OS metadata')
        if os_version is not None and (not module_metadata or len(os_version) != 3 or any(type(x) is not int or not 0 <= x <= 65535 for x in os_version)):
            raise ValueError('invalid OS version metadata')
        if guest_heap and not heap_handle:
            raise ValueError('guest heap needs explicit synthetic heap handle')
        self.allocations = []
        self.heap_reserved = 0
        if heap_handle and not module_metadata:
            raise ValueError('heap handle needs synthetic module metadata')
        self.environment_allowed = []
        self.readonly_modules = []
        self.export_names = {}
        synthetic_image_pointer = self.synthetic_image_pointer
        if synthetic_image_pointer:
            # Deliberately sparse metadata, not a fabricated complete Windows
            # environment: unknown field reads are trapped, NOT returned as 0.
            self.uc.mem_map(TEB_BASE, PAGE*2, unicorn.UC_PROT_READ)
            self.uc.mem_write(TEB_BASE+0x60, struct.pack('<Q', PEB_BASE))
            self.uc.mem_write(PEB_BASE+0x10, struct.pack('<Q', self.base))
            self.uc.reg_write(x86.UC_X86_REG_GS_BASE, TEB_BASE)
            self.environment_allowed.extend([(TEB_BASE+0x60, 8), (PEB_BASE+0x10, 8)])
        if module_metadata:
            self.add_module_metadata()
        if heap_handle:
            # Opaque guest-only handle; no host process heap or allocation API.
            # Dereferencing it or calling an external allocator still traps.
            self.uc.mem_write(PEB_BASE+0x30, struct.pack('<Q', 0x700000000))
            self.environment_allowed.append((PEB_BASE+0x30, 8))
        if os_version is not None:
            # Observed host version metadata, NOT an alternate build selected to
            # steer protection checks. Other environment fields remain unknown.
            major, minor, build = os_version
            self.uc.mem_write(PEB_BASE+0x118, struct.pack('<IIH', major, minor, build))
            self.environment_allowed.append((PEB_BASE+0x118, 10))
        if shared_version:
            self.uc.mem_map(SHARED_BASE, PAGE, unicorn.UC_PROT_READ)
            self.uc.mem_write(SHARED_BASE+0x260, struct.pack('<I', os_version[2]))
        self.imports = {}
        for module in getattr(self.pe, 'DIRECTORY_ENTRY_IMPORT', []):
            for imp in module.imports:
                address = TRAP_BASE + 16 * len(self.imports)
                self.imports[address] = module.dll.decode('ascii') + '!' + (imp.name.decode('ascii') if imp.name else '#' + str(imp.ordinal))
                self.uc.mem_write(imp.address, struct.pack('<Q', address))
        self.dis = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.dis.detail = True
        self.count = 0
        self.has_run = False
        self.total_seconds = 0
        self.blocks = Counter()
        self.checked_blocks = set()
        self.tail = deque(maxlen=96)
        self.writes = Counter()
        self.first_writes = []
        self.stop_reason = None
        self.uc.hook_add(unicorn.UC_HOOK_CODE, self.code)
        self.uc.hook_add(unicorn.UC_HOOK_BLOCK, self.block)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.write)
        self.uc.hook_add(unicorn.UC_HOOK_MEM_INVALID, self.invalid)
        self.uc.hook_add(unicorn.UC_HOOK_INTR, self.interrupt)
        if synthetic_image_pointer:
            self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.environment_read, begin=TEB_BASE, end=PEB_BASE+PAGE-1)
        if guest_heap:
            self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.heap_read, begin=HEAP_BASE, end=HEAP_BASE+HEAP_BUDGET-1)
        if shared_version:
            self.uc.hook_add(unicorn.UC_HOOK_MEM_READ, self.shared_read, begin=SHARED_BASE, end=SHARED_BASE+PAGE-1)

    def shared_read(self, uc, access, address, size, value, user):
        if not SHARED_BASE+0x260 <= address < address+size <= SHARED_BASE+0x264:
            self.stop('unmodelled_shared_user_data', offset=hex(address-SHARED_BASE), size=size)

    def heap_read(self, uc, access, address, size, value, user):
        for allocation in self.allocations:
            if allocation['base'] <= address < allocation['base']+allocation['mapped']:
                offset = address-allocation['base']
                if not any(lo <= offset and offset+size <= hi for lo, hi in allocation['initialized']):
                    self.stop('uninitialized_guest_heap_read', allocation=hex(allocation['base']), offset=offset, size=size)
                return
        self.stop('unknown_guest_heap_read', address=hex(address), size=size)

    def model_heap_boundary(self):
        """Implement only a guest-local allocator, not an external API call."""
        if not self.guest_heap or self.stop_reason.get('symbols') != ['ntdll.dll!RtlAllocateHeap'] or self.stop_reason.get('access') != unicorn.UC_MEM_FETCH_PROT:
            return False
        handle = self.uc.reg_read(x86.UC_X86_REG_RCX)
        flags = self.uc.reg_read(x86.UC_X86_REG_RDX)
        size = self.uc.reg_read(x86.UC_X86_REG_R8)
        self.stop_reason['arguments'] = dict(handle=hex(handle), flags=flags, size=size)
        mapped = align(max(size, 1))
        if handle != 0x700000000 or flags not in (0, 8) or not 0 < size <= 32*1024*1024 or mapped+PAGE > HEAP_BUDGET-self.heap_reserved or len(self.allocations) >= 128:
            return False
        sp = self.uc.reg_read(x86.UC_X86_REG_RSP)
        if not STACK_BASE <= sp <= STACK_BASE+STACK_SIZE-8:
            return False
        return_address = struct.unpack('<Q', self.uc.mem_read(sp, 8))[0]
        if not self.base <= return_address < self.base+self.size:
            return False
        address = HEAP_BASE+self.heap_reserved
        self.uc.mem_map(address, mapped, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
        self.heap_reserved += mapped+PAGE  # an unmapped guard page after each
        self.allocations.append(dict(base=address, size=size, mapped=mapped, flags=flags,
            initialized=[(0, size)] if flags == 8 else [], writes=0))
        self.uc.reg_write(x86.UC_X86_REG_RAX, address)
        self.uc.reg_write(x86.UC_X86_REG_RSP, sp+8)
        self.uc.reg_write(x86.UC_X86_REG_RIP, return_address)
        self.stop_reason = None
        return True

    def environment_read(self, uc, access, address, size, value, user):
        if not any(lo <= address and address+size <= lo+span for lo, span in self.environment_allowed):
            self.stop('unmodelled_environment_field', structure='PEB' if address >= PEB_BASE else 'TEB',
                offset=hex(address-(PEB_BASE if address >= PEB_BASE else TEB_BASE)), size=size)

    def add_module_metadata(self):
        """Read PE export tables from disk, NEVER load or execute system DLLs."""
        self.uc.mem_map(LDR_BASE, PAGE*4, unicorn.UC_PROT_READ)
        self.uc.mem_write(PEB_BASE+0x18, struct.pack('<Q', LDR_BASE))
        self.environment_allowed.append((PEB_BASE+0x18, 8))
        modules = [dict(name=CLIENT.name, base=self.base, size=self.size)]
        for index, name in enumerate(['ntdll.dll', 'kernel32.dll', 'kernelbase.dll']):
            source = Path('C:/Windows/System32') / name
            raw = source.read_bytes()
            pe, image = checked_image(raw, require_entry=False)
            base = 0x600000000 + index*0x1000000
            self.uc.mem_map(base, len(image), unicorn.UC_PROT_READ)
            self.uc.mem_write(base, image)
            meta = dict(name=name, base=base, size=len(image), sha256=sha256(raw).hexdigest())
            modules.append(meta)
            self.readonly_modules.append(meta)
            for symbol in getattr(pe, 'DIRECTORY_ENTRY_EXPORT').symbols:
                if symbol.name:
                    self.export_names.setdefault(base+symbol.address, []).append(name+'!'+symbol.name.decode('ascii'))
        entries = [LDR_BASE+0x100+i*0x100 for i in range(len(modules))]
        for offset, head_offset in [(0, 0x10), (0x10, 0x20), (0x20, 0x30)]:
            head = LDR_BASE+head_offset
            nodes = [head] + [entry+offset for entry in entries]
            for i, node in enumerate(nodes):
                self.uc.mem_write(node, struct.pack('<QQ', nodes[(i+1)%len(nodes)], nodes[(i-1)%len(nodes)]))
        for i, (module, entry) in enumerate(zip(modules, entries)):
            self.uc.mem_write(entry+0x30, struct.pack('<Q', module['base']))
            self.uc.mem_write(entry+0x40, struct.pack('<I', module['size']))
            text = module['name'].encode('utf-16-le')
            address = LDR_BASE+PAGE+i*0x100
            self.uc.mem_write(address, text+b'\0\0')
            descriptor = struct.pack('<HHIQ', len(text), len(text)+2, 0, address)
            self.uc.mem_write(entry+0x48, descriptor)
            self.uc.mem_write(entry+0x58, descriptor)

    def stop(self, reason, **detail):
        if self.stop_reason is None:
            self.stop_reason = dict(reason=reason, **detail)
        self.uc.emu_stop()

    def block(self, uc, address, size, user):
        self.blocks[address - self.base] += 1
        # Stop the entire translated block before any privileged/system opcode.
        # No system instructions or anti-debug patches are applied; the only
        # optional function model is allocation inside guest-local memory.
        data = bytes(uc.mem_read(address, size))
        key = (address, data)
        if key in self.checked_blocks:
            return
        for ins in self.dis.disasm(data, address):
            mnemonic = ins.mnemonic.split()[-1]
            if ins.group(capstone.CS_GRP_PRIVILEGE) or mnemonic in {'syscall', 'sysenter', 'sysret', 'sysretq', 'sysretl', 'sysexit', 'sysexitq', 'sysexitl', 'int', 'int1', 'int3', 'in', 'out', 'insb', 'insw', 'insd', 'outsb', 'outsw', 'outsd', 'hlt', 'rdmsr', 'wrmsr'}:
                self.stop('system_or_privileged_instruction', rva=ins.address-self.base, instruction=ins.mnemonic)
                return
        if len(self.checked_blocks) < 32768:
            self.checked_blocks.add(key)

    def code(self, uc, address, size, user):
        if not self.base <= address < self.base + self.size:
            self.stop('outside_image_execution', address=hex(address))
            return
        self.count += 1
        self.tail.append((address-self.base, size))

    def write(self, uc, access, address, size, value, user):
        if self.base <= address and address + size <= self.base + self.size:
            page = (address-self.base) // PAGE * PAGE
            self.writes[page] += size
            if len(self.first_writes) < 64:
                self.first_writes.append(dict(rva=address-self.base, size=size, instruction_rva=uc.reg_read(x86.UC_X86_REG_RIP)-self.base))
        elif HEAP_BASE <= address < HEAP_BASE+HEAP_BUDGET:
            for allocation in self.allocations:
                if allocation['base'] <= address < allocation['base']+allocation['mapped']:
                    offset = address-allocation['base']
                    if offset+size > allocation['size']:
                        self.stop('guest_heap_write_outside_allocation', offset=offset, size=size)
                        return
                    allocation['writes'] += size
                    spans = sorted(allocation['initialized']+[(offset, offset+size)])
                    merged = []
                    for lo, hi in spans:
                        if merged and lo <= merged[-1][1]:
                            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                        else:
                            merged.append((lo, hi))
                    if len(merged) > 4096:
                        self.stop('guest_heap_fragment_tracking_limit')
                        return
                    allocation['initialized'] = merged
                    return

    def invalid(self, uc, access, address, size, value, user):
        if address in self.imports:
            self.stop('external_import_boundary', function=self.imports[address])
        elif address == STOP_RETURN:
            self.stop('entry_returned')
        elif any(m['base'] <= address < m['base']+m['size'] for m in self.readonly_modules):
            self.stop('external_module_execution_or_write', address=hex(address), access=access,
                symbols=self.export_names.get(address, []))
        else:
            self.stop('unmapped_or_protected_memory', access=access, address=hex(address), size=size,
                      instruction_rva=uc.reg_read(x86.UC_X86_REG_RIP)-self.base)
        return False

    def interrupt(self, uc, number, user):
        self.stop('interrupt_boundary', number=number)

    def run(self, resume=False):
        entry = self.pe.OPTIONAL_HEADER.AddressOfEntryPoint
        if self.has_run and not resume:
            raise ValueError('reuse requires an explicit resumable boundary')
        if resume and (not self.stop_reason or self.stop_reason['reason'] not in {'instruction_limit', 'timeout'}):
            raise ValueError('cannot resume through an unresolved boundary')
        self.has_run = True
        self.stop_reason = None
        start = time.monotonic()
        error = None
        address = self.uc.reg_read(x86.UC_X86_REG_RIP) if resume else self.base+entry
        limit = self.count+self.limit
        while self.count < limit:
            remaining = self.seconds-(time.monotonic()-start)
            if remaining <= 0:
                break
            error = None
            try:
                self.uc.emu_start(address, self.base+self.size, timeout=int(remaining*1000000), count=limit-self.count)
            except unicorn.UcError as exc:
                error = str(exc)
            if self.stop_reason and self.model_heap_boundary():
                address = self.uc.reg_read(x86.UC_X86_REG_RIP)
                continue
            break
        if self.stop_reason is None:
            if self.count >= limit:
                reason = 'instruction_limit'
            elif not error and bool(self.uc.query(unicorn.UC_QUERY_TIMEOUT)):
                reason = 'timeout'
            else:
                reason = 'unclassified_emulator_boundary'
            self.stop_reason = dict(reason=reason)
        elapsed = time.monotonic()-start
        self.total_seconds += elapsed
        tail = []
        for rva, size in self.tail:
            data = bytes(self.uc.mem_read(self.base+rva, size))
            ins = next(self.dis.disasm(data, self.base+rva, count=1), None)
            tail.append(dict(rva=rva, bytes=data.hex(), bytes_sampled='at_stop', instruction=(ins.mnemonic+' '+ins.op_str) if ins else 'undecoded'))
        modified = []
        for section in self.pe.sections:
            rva = section.VirtualAddress
            span = max(section.SizeOfRawData, section.Misc_VirtualSize)
            section_pages = [p for p in self.writes if p < rva+span and p+PAGE > rva]
            if section_pages:
                modified.append(dict(name=section.Name.rstrip(b'\0').decode('ascii'), rva=rva,
                    virtual_size=span, written_pages=len(section_pages),
                    write_bytes=sum(self.writes[p] for p in section_pages),
                    current_sha256=sha256(bytes(self.uc.mem_read(self.base+rva, span))).hexdigest()))
        resolved = []
        for item in self.first_writes:
            if item['size'] == 8:
                value = struct.unpack('<Q', self.uc.mem_read(self.base+item['rva'], 8))[0]
                names = self.export_names.get(value)
                if names:
                    resolved.append(dict(rva=item['rva'], symbols=names))
        return dict(client_sha256=self.sha, unicorn_version=unicorn.__version__,
            seconds=elapsed, total_seconds=self.total_seconds, instructions=self.count, unique_blocks=len(self.blocks), stop=self.stop_reason,
            error=error, modified_sections=modified, first_image_writes=self.first_writes,
            read_only_system_export_tables=self.readonly_modules,
            resolved_external_symbols=resolved, synthetic_heap_handle=self.heap_handle,
            synthetic_guest_heap=self.guest_heap, guest_allocations=self.allocations,
            supplied_os_version=self.os_version,
            shared_os_build_supplied=self.shared_version,
            instruction_tail=tail,
            assumptions=['Preferred image base; zero initial general registers except stack and flags.',
                'Static import slots replaced only inside emulator with stopping addresses.',
                'No TLS callbacks (client callback pointer is zero).',
                'Synthetic loader list of main, ntdll, kernel32, kernelbase; DLL images are read-only and non-executable.' if self.module_metadata else
                'Only synthetic TEB-to-PEB and image-base pointers; all other environment fields trap.' if self.synthetic_image_pointer else 'No synthetic PEB/OS loaded.'],
            safety=dict(host_functions_called=0, game_process_started=False, live_memory_read=False,
                installed_files_modified=False, external_guest_io=False),
            runtime_attribution_certificates=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--instructions', type=int, default=500000)
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--synthetic-image-pointer', action='store_true')
    parser.add_argument('--module-metadata', action='store_true')
    parser.add_argument('--heap-handle', action='store_true')
    parser.add_argument('--guest-heap', action='store_true')
    parser.add_argument('--os-version', action='store_true')
    parser.add_argument('--segments', type=int, choices=range(1, 4), default=1)
    parser.add_argument('--shared-version', action='store_true')
    args = parser.parse_args()
    raw = CLIENT.read_bytes()
    os_version = tuple(sys.getwindowsversion().platform_version) if args.os_version else None
    script_sha = sha256(Path(__file__).read_bytes()).hexdigest()
    experiment = EntryExperiment(raw, args.instructions, args.seconds, args.synthetic_image_pointer, args.module_metadata, args.heap_handle, args.guest_heap, os_version, args.shared_version)
    checkpoints = []
    for segment in range(args.segments):
        report = experiment.run(resume=segment > 0)
        checkpoints.append(dict(segment=segment+1, instructions=report['instructions'], seconds=report['total_seconds'], stop=report['stop'], modified_sections=report['modified_sections']))
        if args.segments > 1:
            print(json.dumps(checkpoints[-1], ensure_ascii=False), flush=True)
        if report['stop']['reason'] not in {'instruction_limit', 'timeout'} or report['error']:
            break
    report['checkpoints'] = checkpoints
    report['script_sha256'] = script_sha
    report['script_changed_during_run'] = sha256(Path(__file__).read_bytes()).hexdigest() != script_sha
    if sha256(CLIENT.read_bytes()).hexdigest() != sha256(raw).hexdigest():
        raise RuntimeError('Client changed externally during inspection')
    OUT.mkdir(exist_ok=True)
    name = 'entry_emulation_shared_version.json' if args.shared_version else 'entry_emulation_os_version.json' if args.os_version else 'entry_emulation_guest_heap.json' if args.guest_heap else 'entry_emulation_heap_handle.json' if args.heap_handle else 'entry_emulation_modules.json' if args.module_metadata else 'entry_emulation_image_pointer.json' if args.synthetic_image_pointer else 'entry_emulation.json'
    (OUT/name).write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'instruction_tail', 'first_image_writes'}}, ensure_ascii=False))
