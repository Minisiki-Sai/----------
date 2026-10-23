"""Offline guest-only self-file I/O. No guest path is opened on the host.

Only the already supplied, hashed executable bytes can be read. Native services
are identified from this machine's read-only ntdll PE, not a service-number guess.
Unknown calls/flags/paths stop; no privileged instruction is executed.
"""
from hashlib import sha256
from pathlib import Path
import struct

from tools.emulate_client_sections_v40 import SectionExperiment, v39

RAW_FILE_BASE=0xa00000000


def service_candidates(e, number):
    result = set()
    for address, names in e.export_names.items():
        native = [n for n in names if n.startswith(('ntdll.dll!Nt', 'ntdll.dll!Zw'))]
        if not native:
            continue
        data = bytes(e.uc.mem_read(address, 32))
        suffix=data[8:]
        canonical=(suffix.startswith(bytes.fromhex('0f05c3')) or
                   suffix.startswith(bytes.fromhex('f604250803fe7f0175030f05c3cd2ec3')))
        if data[:4] == bytes.fromhex('4c8bd1b8') and struct.unpack_from('<I', data, 4)[0] == number and canonical:
            if len(native) != len(names):
                result.add('<ambiguous-export>')
            result.update(n.split('!')[1][2:] for n in native)
    return result


class FileExperiment(SectionExperiment):
    def __init__(self, raw, *args, **kwargs):
        self.file_bytes = bytes(raw)
        self.file_handles = {}
        self.file_sections = {}
        self.next_file_section = 0xd000
        self.file_views = []
        self.file_view_reads = dict(count=0,bytes=0,minimum=None,maximum=None)
        self.page_protections = {}
        self.next_file_handle = 0xc000
        self.file_calls = []
        self.total_file_read_bytes = 0
        self.model_resume_evidence = None
        super().__init__(raw, *args, **kwargs)

    def add_module_metadata(self):
        super().add_module_metadata()
        # Separate FullDllName from BaseDllName. Both are factual paths for the
        # four PE byte sources, not names chosen to satisfy a protection check.
        sources = [v39.CLIENT, *(Path('C:/Windows/System32')/name for name in ('ntdll.dll','kernel32.dll','kernelbase.dll'))]
        for index, source in enumerate(sources):
            text = str(source).replace('/', '\\').encode('utf-16-le')
            if len(text)+2 > 0x400:
                raise ValueError('full module name exceeds guest metadata slot')
            address = v39.LDR_BASE+v39.PAGE*2+index*0x400
            self.uc.mem_write(address, text+b'\0\0')
            self.uc.mem_write(v39.LDR_BASE+0x100+index*0x100+0x48,
                              struct.pack('<HHIQ',len(text),len(text)+2,0,address))

    def file_name(self, pointer):
        length, root, nameptr, attrs, security, qos = struct.unpack('<I4xQQI4xQQ',self.guest_read(pointer,48))
        if length != 48 or root or security or qos or attrs not in (0,0x40):
            raise ValueError('unsupported self-file object attributes')
        n, maximum, buffer = struct.unpack('<HH4xQ',self.guest_read(nameptr,16))
        if not 0<n<=maximum<=1024 or n%2:
            raise ValueError('invalid self-file name size')
        name = self.guest_read(buffer,n).decode('utf-16-le',errors='strict')
        expected = '\\??\\'+str(v39.CLIENT).replace('/','\\')
        # Python casefold is not Windows ordinal case-insensitive comparison:
        # e.g. it expands sharp-s into 'ss'. Fold ASCII letters per character,
        # preserving Chinese/path characters and all other non-ASCII codepoints.
        equal=name==expected
        if attrs & 0x40:
            key=lambda value: ''.join(chr(ord(c)+32) if 'A'<=c<='Z' else c for c in value)
            equal=key(name)==key(expected)
        if not equal:
            raise ValueError('path is not the explicitly supplied full client path')
        return name

    def outputs(self, spans, inputs=()):
        # Validate the complete write-set before the first mutation. Outputs
        # must not overlap one another, arguments or current return/shadow slots.
        sp = self.uc.reg_read(v39.x86.UC_X86_REG_RSP)
        for index,(address,size) in enumerate(spans):
            self.guest_range(address,size,initialized=False)
            cursor=address
            for lo,hi,permissions in sorted(self.uc.mem_regions()):
                if hi<cursor:
                    continue
                if lo>cursor or not permissions & v39.unicorn.UC_PROT_WRITE:
                    break
                cursor=min(address+size,hi+1)
                if cursor==address+size:
                    break
            if cursor!=address+size:
                raise ValueError('file output is not wholly mapped writable guest memory')
            if address<sp+0x80 and sp<address+size:
                raise ValueError('file output overlaps current call frame')
            if any(address<b+n and b<address+size for b,n in (*spans[:index],*inputs)):
                raise ValueError('file output overlaps another argument')

    def syscall_args(self, count):
        args=self.arguments(count)
        if args:
            args[0]=self.uc.reg_read(v39.x86.UC_X86_REG_R10)
        return args

    def finish_native(self, syscall, status):
        if not syscall:
            return self.finish_call(status)
        ip=self.uc.reg_read(v39.x86.UC_X86_REG_RIP)
        flags=self.uc.reg_read(v39.x86.UC_X86_REG_EFLAGS)
        # x64 syscall user return contract: RCX/R11 are clobbered; the guest
        # kernel transition itself is not executed and has no host counterpart.
        self.uc.reg_write(v39.x86.UC_X86_REG_RCX,ip+2)
        self.uc.reg_write(v39.x86.UC_X86_REG_R11,flags)
        self.uc.reg_write(v39.x86.UC_X86_REG_RAX,status)
        self.uc.reg_write(v39.x86.UC_X86_REG_RIP,ip+2)
        self.stop_reason=None
        return True

    def watch_file_view(self):
        def record(uc,access,address,size,value,user):
            offset=address-RAW_FILE_BASE
            if offset<0 or offset+size>len(self.file_bytes):
                self.stop('raw_self_file_read_outside_file',offset=offset,size=size)
                return
            state=self.file_view_reads
            state['count']+=1;state['bytes']+=size
            state['minimum']=min(offset,state['minimum']) if state['minimum'] is not None else offset
            state['maximum']=max(offset+size,state['maximum'] or 0)
        self.file_view_hook=self.uc.hook_add(v39.unicorn.UC_HOOK_MEM_READ,record,
            begin=RAW_FILE_BASE,end=RAW_FILE_BASE+v39.align(len(self.file_bytes))-1)

    def model_file_boundary(self):
        stop=self.stop_reason
        if not stop or len(self.file_calls)>=256 or len(self.model_writes)>120:
            return False
        syscall=stop.get('reason')=='system_or_privileged_instruction' and stop.get('instruction')=='syscall'
        if syscall:
            ip=self.uc.reg_read(v39.x86.UC_X86_REG_RIP)
            if ip!=self.base+stop['rva'] or bytes(self.uc.mem_read(ip,2))!=b'\x0f\x05':
                return False
            number=self.uc.reg_read(v39.x86.UC_X86_REG_RAX)&0xffffffff
            names=service_candidates(self,number)
        elif stop.get('access')==v39.unicorn.UC_MEM_FETCH_PROT:
            symbols=stop.get('symbols',[])
            if not symbols or any(not n.startswith(('ntdll.dll!Nt','ntdll.dll!Zw')) for n in symbols):
                return False
            names={n.split('!')[1][2:] for n in symbols}
        else:
            return False
        if len(names)!=1 or next(iter(names)) not in ('OpenFile','ReadFile','CreateSection','MapViewOfSection','UnmapViewOfSection','ProtectVirtualMemory','Close'):
            return False
        name=next(iter(names))
        record=dict(function='Nt'+name,syscall=syscall,modelled=False)
        self.file_calls.append(record)
        read_args=self.syscall_args if syscall else self.arguments
        try:
            if not syscall:
                self.checked_return()
            if name=='OpenFile':
                output,access,oa,iosb,share,options=read_args(6)
                access &= 0xffffffff;share &= 0xffffffff;options &= 0xffffffff
                path=self.file_name(oa)
                if access!=0x80100080 or share!=3 or options!=0x60 or len(self.file_handles)>=4:
                    raise ValueError('unsupported self-file read-only open contract')
                nameptr=self.u64(oa+16)
                descriptor=self.guest_read(nameptr,16)
                n=struct.unpack_from('<H',descriptor)[0];buffer=struct.unpack_from('<Q',descriptor,8)[0]
                self.outputs([(output,8),(iosb,16)],[(oa,48),(nameptr,16),(buffer,n)])
                handle=self.next_file_handle
                self.next_file_handle+=4
                self.file_handles[handle]=dict(position=0)
                self.guest_write(output,struct.pack('<Q',handle))
                self.guest_write(iosb,struct.pack('<QQ',0,1)) # FILE_OPENED
                record.update(path=path,handle=hex(handle),source_sha256=sha256(self.file_bytes).hexdigest(),
                              path_case_insensitive=bool(struct.unpack_from('<I',self.guest_read(oa,48),24)[0]&0x40))
                status=0
            elif name=='ReadFile':
                handle,event,apc,context,iosb,buffer,length,offsetptr,key=read_args(9)
                length &= 0xffffffff
                if handle not in self.file_handles or event or apc or context or key or not 0<length<=4096:
                    raise ValueError('unsupported synchronous self-file read contract')
                offset=struct.unpack('<q',self.guest_read(offsetptr,8))[0] if offsetptr else -2
                if offset==-2:
                    offset=self.file_handles[handle]['position']
                if offset<0 or self.total_file_read_bytes+length>64*1024*1024:
                    raise ValueError('invalid self-file read offset/budget')
                self.outputs([(buffer,length),(iosb,16)],[(offsetptr,8)] if offsetptr else [])
                data=self.file_bytes[offset:offset+length]
                status=0 if data else 0xc0000011 # STATUS_END_OF_FILE
                if data:
                    self.guest_write(buffer,data)
                    self.file_handles[handle]['position']=offset+len(data)
                self.guest_write(iosb,struct.pack('<QQ',status,len(data)))
                self.total_file_read_bytes+=len(data)
                record.update(handle=hex(handle),offset=offset,requested=length,returned=len(data),data_sha256=sha256(data).hexdigest())
            elif name=='CreateSection':
                output,access,oa,maxptr,protection,attributes,file_handle=read_args(7)
                access &= 0xffffffff;protection &= 0xffffffff;attributes &= 0xffffffff
                if file_handle not in self.file_handles or maxptr or access!=4 or protection!=2 or attributes!=0x08000000 or len(self.file_sections)>=4:
                    raise ValueError('unsupported read-only self-file section contract')
                if oa and struct.unpack('<I4xQQI4xQQ',self.guest_read(oa,48))!=(48,0,0,0,0,0):
                    raise ValueError('self-file section must be unnamed')
                self.outputs([(output,8)],[(oa,48)] if oa else [])
                handle=self.next_file_section
                self.next_file_section+=4
                self.file_sections[handle]=dict(size=len(self.file_bytes),source_sha256=self.sha)
                self.guest_write(output,struct.pack('<Q',handle))
                record.update(handle=hex(handle),file_handle=hex(file_handle),size=len(self.file_bytes),source_sha256=self.sha)
                status=0
            elif name=='MapViewOfSection':
                handle,process,baseptr,zero,commit,offsetptr,sizeptr,inherit,allocation,protection=read_args(10)
                inherit &= 0xffffffff;allocation &= 0xffffffff;protection &= 0xffffffff
                requested_base=self.u64(baseptr);requested_size=self.u64(sizeptr)
                offset=self.u64(offsetptr) if offsetptr else 0
                if handle not in self.file_sections or process!=0xffffffffffffffff or requested_base or requested_size or zero or commit or offset or inherit!=1 or allocation or protection!=2 or self.file_views:
                    raise ValueError('unsupported full read-only self-file view')
                self.outputs([(baseptr,8),(sizeptr,8)],[(offsetptr,8)] if offsetptr else [])
                size=v39.align(len(self.file_bytes))
                if any(RAW_FILE_BASE<=hi and lo<RAW_FILE_BASE+size for lo,hi,_ in self.uc.mem_regions()):
                    raise ValueError('self-file view conflicts with guest region')
                self.uc.mem_map(RAW_FILE_BASE,size,v39.unicorn.UC_PROT_READ)
                try:
                    self.uc.mem_write(RAW_FILE_BASE,self.file_bytes)
                except v39.unicorn.UcError:
                    self.uc.mem_unmap(RAW_FILE_BASE,size)
                    raise
                self.file_views.append(dict(base=RAW_FILE_BASE,size=size,source_sha256=self.sha,unmapped=False))
                self.watch_file_view()
                self.guest_write(baseptr,struct.pack('<Q',RAW_FILE_BASE))
                self.guest_write(sizeptr,struct.pack('<Q',size))
                record.update(handle=hex(handle),base=hex(RAW_FILE_BASE),size=size,source_sha256=self.sha)
                status=0
            elif name=='UnmapViewOfSection':
                process,base=read_args(2)
                if process!=0xffffffffffffffff or base!=RAW_FILE_BASE or not self.file_views or self.file_views[0]['unmapped']:
                    raise ValueError('not an active self-file view')
                self.uc.mem_unmap(base,self.file_views[0]['size'])
                self.uc.hook_del(self.file_view_hook)
                self.file_views[0]['unmapped']=True
                record.update(handle='0x0',base=hex(base))
                status=0
            elif name=='ProtectVirtualMemory':
                from tools.client_virtual_protection_v43 import apply
                apply(self,read_args(5),record)
                status=0
            else:
                handle=read_args(1)[0]
                if handle in self.file_sections:
                    del self.file_sections[handle]
                elif handle in self.file_handles:
                    del self.file_handles[handle]
                else:
                    raise ValueError('not a guest self-file handle')
                record['handle']=hex(handle)
                status=0
            record.update(modelled=True,status=hex(status))
            return self.finish_native(syscall,status)
        except (ValueError,v39.unicorn.UcError) as exc:
            record['rejected']=str(exc)
            stop['file_contract']=record
            return False

    def model_heap_boundary(self):
        # Section/heap calls keep their existing implementations; file NtClose
        # uses a disjoint handle range and is selected before section NtClose.
        stop=self.stop_reason or {}
        if stop.get('reason')=='system_or_privileged_instruction' or (
            any(n in ('ntdll.dll!NtOpenFile','ntdll.dll!ZwOpenFile','ntdll.dll!NtReadFile','ntdll.dll!ZwReadFile','ntdll.dll!NtCreateSection','ntdll.dll!ZwCreateSection') for n in stop.get('symbols',[])) or
            self.uc.reg_read(v39.x86.UC_X86_REG_RCX) in self.file_sections and any(n in ('ntdll.dll!NtMapViewOfSection','ntdll.dll!ZwMapViewOfSection') for n in stop.get('symbols',[])) or
            self.uc.reg_read(v39.x86.UC_X86_REG_RDX)==RAW_FILE_BASE and any(n in ('ntdll.dll!NtUnmapViewOfSection','ntdll.dll!ZwUnmapViewOfSection') for n in stop.get('symbols',[])) or
            self.uc.reg_read(v39.x86.UC_X86_REG_RCX) in (self.file_handles.keys()|self.file_sections.keys()) and any(n in ('ntdll.dll!NtClose','ntdll.dll!ZwClose') for n in stop.get('symbols',[]))):
            if self.model_file_boundary():
                return True
            return False
        return super().model_heap_boundary()

    def run(self,resume=False):
        result=super().run(resume=resume)
        result.update(file_calls=self.file_calls,file_handles=self.file_handles,
                      file_sections=self.file_sections,
                      file_views=self.file_views,file_view_reads=self.file_view_reads,
                      logical_page_protections=self.page_protections,
                      total_file_read_bytes=self.total_file_read_bytes,
                      full_module_paths=True)
        return result
