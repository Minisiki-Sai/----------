"""Bounded static resource-call review. No client execution or codec guessing.

Original exception anchors are preferred; four bounded, independently verified
direct-target leaf windows are explicitly distinguished from exception ranges.
All writes stay under the delegated pak* report paths; no PE is produced.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.review_client_damage_types_v44 import StaticView, BASE, capstone, x86
from tools.review_client_code_links_v44 import reachable_rows

OUT = ROOT / 'outputs/native_wire_v47_20260909'
FUNCTIONS = [0x92c0e0, 0x1eca50, 0x1e9520, 0x1e93c0, 0x1eab10,
             0x11ca80, 0x1ea1e0, 0x11d000, 0x11b250, 0x11b2c1]
# Phase 2: fifteen additional logical functions, plus nine continuation ranges
# belonging to already counted functions. No targets beyond this explicit list.
EXTRA_FUNCTIONS = [0x1eeed0,0x11adf0,0x11c570,0x11b35e,0x1ee710,0x1ed0d0,
                   0x11b3c4,0x1ee2c0,0x22ec20,0x11b3e5,0x22edf0,0x1eadd0,
                   0x11d670,0x24f380,0x11b4f0,0x120230,0x23c780,0x11ae60,
                   0x12026a,0x23c7be,0x23c805,0x23c81b,0x23c82e,0x120331]
CONTINUATIONS = {0x11b35e:0x11b250,0x11b3c4:0x11b250,0x11b3e5:0x11b250,
                 0x12026a:0x120230,0x120331:0x120230,0x23c7be:0x23c780,
                 0x23c805:0x23c780,0x23c81b:0x23c780,0x23c82e:0x23c780}
MAX_FUNCTIONS = 34
PHASE3_FUNCTIONS = [0x120040,0x1b469ec,0x23d260,0x1b47030,0x23d286,0x23d2e6,0x23d4b0]
# Four entry windows lack an exception record, but have verified direct callers.
# The Adler window is intentionally only the single-byte path, not its whole function.
PHASE3_LEAVES = [
    dict(begin=0x23d350,size=10,caller=0x23c7b2,caller_bytes='e8990b0000',kind='tail-jump leaf'),
    dict(begin=0x11fd10,size=0x327,caller=0x1b4707c,caller_bytes='e88f8c5dfe',kind='straight-line leaf through ret'),
    dict(begin=0x23d200,size=0x60,caller=0x23d328,caller_bytes='e9d3feffff',kind='direct tail-target leaf'),
    dict(begin=0x240bb0,size=0x44,caller=0x23d6c7,caller_bytes='e8e4340000',kind='single-byte checksum prefix only'),
]
READ_REPORTS = [
    'outputs/client_resources_v37_20260908/REPORT.md',
    'outputs/client_resources_v37_20260908/index_review.json',
    'outputs/pak_wrapper_v39_20260908/REPORT.md',
    'outputs/pak_wrapper_v39_20260908/probe_review.json',
    'outputs/client_protection_v44_20260908/code_links.json',
]
SOURCE_FILES = ['tools/trace_pak_reader_v47.py', 'tests/test_pak_reader_v47.py',
                'tools/review_client_damage_types_v44.py', 'tools/review_client_code_links_v44.py']
# Independent instruction-byte pins, not assertions against formatted mnemonics.
EVIDENCE = {
    0x92c24c: 'e8ff078cff', 0x92c28f: '488d150adc5d00',
    0x1eca94: 'e9e7fff2ff', 0x11cb10: '488d157960e900',
    0x11cddf: 'e81c020000', 0x11d151: 'e8fae0ffff',
    0x11b268: 'ba84af7c90', 0x11b26d: 'e8fe120000',
    0x11b288: 'ba00000080', 0x11b290: 'c744242003000000',
    0x11b29c: 'ff15b623d500', 0x11b2a2: '48894710',
    0x11b2bd: '488d5718', 0x11b2d5: '41b810010000',
    0x11b2e3: 'ff154724d500', 0x11b2e9: '8b8f18010000',
    0x11b2ef: '8d41f6', 0x11b2f2: '83f801', 0x11b2f5: '0f87d9010000',
    0x11b300: '80bf2401000001', 0x11b307: '0f84c7010000',
    0x11b30d: '448b8720010000', 0x11b31b: 'e8d0faffff',
    0x11b322: '4863871c010000', 0x11b329: '41b902000000',
    0x11b333: '4c69c0c4feffff', 0x11b33a: 'e8b1faffff',
    0x11b33f: '898720010000',
}
BODY_EVIDENCE = {
    0x1eef46:'e885e1ffff',0x1ed1ec:'e88f210600',0x24f413:'e808deecff',
    0x24f423:'e8c8c0ecff',0x11b528:'e803060000',0x11b538:'4869f83c010000',
    0x11b53f:'4803bbd0020000',0x11b54c:'448b870c010000',0x11b55a:'e891f8ffff',
    0x11b55f:'8b8f00010000',0x11b571:'448b8f00010000',0x11b57f:'e8dcf8ffff',
    0x11b584:'ba84af7c90',0x11b589:'e8e20f0000',0x11b593:'398f00010000',
    0x11b621:'0f1006',0x11b627:'41b001',0x11b62a:'ba10000000',0x11b632:'e8f94b0000',
    0x11b651:'3b01',0x11b65e:'663b4104',0x11b66d:'3a4106',
    0x11b6fd:'498d5507',0x11b88e:'41b810000000',0x11b89b:'e8506dcd00',
    0x11b8c2:'488b55a7',0x11b8c6:'e8a50c0000',0x11b8cb:'448baf00010000',
    0x11b8d2:'4183c5f0',0x11b8e9:'4883c210',0x11b8f8:'4533c0',
    0x11b8fb:'418bd5',0x11b905:'e826490000',0x11b90a:'4183fd10',
    0x11b913:'0fbe5419ff',0x11b918:'442bea',0x11b9be:'8b8f04010000',
    0x11b9cf:'458bcd',0x11b9d9:'488d559f',0x11b9e0:'e89b0d1200',
    0x11bac6:'4c89b648010000',0x11ae92:'ff159828d500',
    0x11aea0:'837c244001',0x11c570:'e977a4a201',
    0x120263:'803d791c190100',0x1202ae:'e88dfdffff',0x1202c6:'e875fdffff',
    0x1202fb:'e8e0f1ffff',0x23c7b2:'e8990b0000',0x23c7cb:'e8e00c0000',
    0x23c7d7:'83f801',0x23c81b:'8b44243c',0x23c81f:'8907',
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(data):
    return sha256(data).hexdigest()


def read_string(view, rva):
    section=view.section(rva)
    if not section or section['name'] not in ('.rdata','.data'):
        return None
    try:
        data=view.read(rva,min(256,section['rva']+section['virtual_size']-rva))
    except ValueError:
        return None
    import re
    for encoding, pattern in (('ascii',rb'[\x20-\x7e]{3,}\x00'),
                               ('utf-16-le',rb'(?:[\x20-\x7e]\x00){3,}\x00\x00')):
        m=re.match(pattern,data)
        if m:
            value=m.group()[:-1 if encoding=='ascii' else -2]
            return dict(rva=rva,encoding=encoding,text=value.decode(encoding),bytes_sha256=digest(value))
    return None


def inspect_function(view, begin, leaf=None):
    function=view.function(begin)
    if leaf is None:
        require(function is not None and function[0]==begin,'not an exact exception entry')
        start,end,unwind=function
    else:
        require(function is None and leaf['begin']==begin and 0<leaf['size']<=1024,'invalid bounded leaf')
        anchor=bytes.fromhex(leaf['caller_bytes'])
        require(view.read(leaf['caller'],len(anchor))==anchor,'leaf caller bytes changed')
        require(len(anchor)==5 and anchor[0] in (0xe8,0xe9) and
                leaf['caller']+5+struct.unpack('<i',anchor[1:])[0]==begin,'leaf not a direct target')
        start,end,unwind=begin,begin+leaf['size'],None
    require(end-start<=32768,'function byte budget')
    raw=view.read(start,end-start)
    switch=None
    decode_raw=raw
    if begin==0x23d4b0:
        # Proven dispatch: state <=29, indexed 30-dword RVA table. Table bytes
        # are data, not a continuation of the linear instruction stream.
        require(view.read(0x23d553,9).hex()=='83f81d0f8787150000','switch bound changed')
        require(view.read(0x23d571,19).hex()=='8b8c81fcea2300488d05812adcff4803c8ffe1','switch dispatch changed')
        table=view.read(0x23eafc,120)
        switch=dict(at=0x23d582,table_rva=0x23eafc,bytes=table.hex(),
                    targets=list(struct.unpack('<30I',table)))
        decode_raw=raw[:0x23eafc-start]
    decoder=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64)
    decoder.detail=True
    instructions=list(decoder.disasm(decode_raw,BASE+start))
    require(len(instructions)<=8192,'instruction budget')
    lite=[]
    for ins in instructions:
        operand=ins.op_str
        if ins.operands and ins.operands[0].type==x86.X86_OP_IMM and (
                ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP)):
            operand=hex(ins.operands[0].imm-BASE)
        lite.append((ins.address-BASE,ins.size,ins.mnemonic,operand))
    reached,unresolved=reachable_rows(lite,start,end)
    if switch:
        by_address={r[0]:r for r in lite}
        require(switch['at'] in reached,'unreachable switch')
        require(all(start<=t<switch['table_rva'] and t in by_address for t in switch['targets']),
                'switch target not an aligned instruction')
        pending=list(set(switch['targets']))
        while pending:
            address=pending.pop()
            while address not in reached and start<=address<switch['table_rva']:
                require(address in by_address,'switch path not aligned')
                row=by_address[address]; reached.add(address)
                _,size,mnemonic,operand=row
                if mnemonic.startswith(('ret','iret')) or mnemonic in ('ud2','int3','hlt'):
                    break
                if mnemonic.startswith('j') or mnemonic.startswith('loop'):
                    target=int(operand,16) if operand.startswith('0x') else None
                    if target is not None and start<=target<switch['table_rva']:
                        pending.append(target)
                    if mnemonic=='jmp':break
                address+=size
    rows=[]
    for ins in instructions:
        rva=ins.address-BASE
        row=dict(rva=rva,bytes=ins.bytes.hex(),instruction=ins.mnemonic+' '+ins.op_str,
                 entry_reachable=rva in reached)
        if ins.operands and ins.operands[0].type==x86.X86_OP_IMM and (
                ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP)):
            target=ins.operands[0].imm-BASE
            row.update(direct_target=target,target_function=view.function(target))
        memories=[]
        for op in ins.operands:
            if op.type!=x86.X86_OP_MEM:
                continue
            memory=dict(base=ins.reg_name(op.mem.base),index=ins.reg_name(op.mem.index),
                        scale=op.mem.scale,displacement=op.mem.disp,width=op.size)
            if op.mem.base==x86.X86_REG_RIP:
                target=rva+ins.size+op.mem.disp
                memory['rip_target']=target
                memory['string']=read_string(view,target)
                if ins.disp_size==4:
                    disp=struct.unpack('<i',ins.bytes[ins.disp_offset:ins.disp_offset+4])[0]
                    require(rva+ins.size+disp==target,'encoded displacement mismatch')
                    memory['encoded_disp32']=ins.bytes[ins.disp_offset:ins.disp_offset+4].hex()
            memories.append(memory)
        if memories:row['memory']=memories
        rows.append(row)
    index=bisect_left(view.starts,start)
    record_rva=view.exception['rva']+index*12 if leaf is None else None
    return dict(begin=start,end=end,unwind=unwind,code_sha256=digest(raw),
        exception_record_rva=record_rva,exception_record_bytes=view.file_read(record_rva,12).hex() if record_rva else None,
        direct_entry_window=leaf,resolved_switch=switch,
        provenance=view.provenance(start,end-start),instructions=rows,
        undecoded_suffix=len(decode_raw)-sum(i.size for i in instructions),
        excluded_table_bytes=len(raw)-len(decode_raw),
        unresolved_internal_targets=unresolved)


def disassemble_selected(view, entries):
    require(0<len(entries)<=MAX_FUNCTIONS and len(set(entries))==len(entries),'maximum 34 distinct exception ranges')
    return [inspect_function(view,rva) for rva in entries]


def build_contract(functions):
    rows = {r['rva']: r for f in functions for r in f['instructions']}
    for rva, expected in EVIDENCE.items():
        require(rva in rows and rows[rva]['bytes'] == expected,
                f'contract instruction changed/missing at {rva:#x}')
        require(rows[rva]['entry_reachable'], f'unreachable contract evidence at {rva:#x}')
    require(any(m.get('string', {}).get('text') == 'Pak File %ls'
                for m in rows[0x92c28f]['memory'] if m.get('string')), 'missing Pak File anchor')
    require(any(m.get('string', {}).get('text') == 'pak'
                for m in rows[0x11cb10]['memory'] if m.get('string')), 'missing pak extension anchor')
    edges = [(0x92c24c, 0x1eca50, 'call'), (0x1eca94, 0x11ca80, 'tail_jump'),
             (0x11cddf, 0x11d000, 'call'), (0x11d151, 0x11b250, 'call')]
    for at, target, _ in edges:
        require(rows[at].get('direct_target') == target, 'call edge mismatch')
    return dict(
        status='archive-path/open/header/index-position evidence; body contract unresolved',
        evidence_instructions=[rows[rva] for rva in EVIDENCE],
        resource_edges=[dict(at=a, target=t, kind=k) for a,t,k in edges],
        fragment_link=dict(predecessor=0x11b250, fallthrough_at=0x11b2c1,
                           reason='adjacent exception fragments, preserved object/path/handle registers'),
        loader_call=dict(at=0x11cddf, target=0x11d000,
                         rcx='manager', rdx='constructed narrow PAK path', r8='narrow base folder',
                         confidence='argument construction in directory-enumeration path'),
        archive_call=dict(at=0x11d151, target=0x11b250, rcx='allocated 0x300-byte object',
                          rdx='PAK path', r8='not established; do not interpret as a flag'),
        open_like=dict(at=0x11b29c, slot=0xe6d658, rdx=0x80000000, r8=1, r9=0,
                       stack_arg5=3, stack_arg6=0x80, stack_arg7=0,
                       result_stored_object_offset=0x10,
                       api='CreateFileA-shaped arguments; import binding NOT established'),
        header_read_like=dict(at=0x11b2e3, slot=0xe6d730, destination_object_offset=0x18,
                              byte_count=0x110, bytes_read_pointer='rsp+0x80', overlapped=0,
                              api='ReadFile-shaped arguments; import binding NOT established',
                              actual_read_success='not established by static bytes'),
        header_fields=[
            dict(header_offset=0x100, object_offset=0x118, width=4, role='version',
                 evidence=[0x11b2e9,0x11b2ef,0x11b2f2,0x11b2f5],
                 operation='unsigned(version-10) > 1 branches to 0x11b4d4; accepts 10/11'),
            dict(header_offset=0x104, object_offset=0x11c, width=4, role='directory item count',
                 evidence=[0x11b322,0x11b333], operation='sign-extend then multiply by -316'),
            dict(header_offset=0x108, object_offset=0x120, width=4, role='directory position',
                 evidence=[0x11b30d,0x11b31b,0x11b33f],
                 operation='v11 passes value in R8D; v10 stores helper EAX result'),
            dict(header_offset=0x10c, object_offset=0x124, width=1, role='version-11 rejection flag',
                 evidence=[0x11b300,0x11b307], operation='value 1 branches to 0x11b4d4')],
        directory_position_helper=dict(target=0x11adf0, inspected=False,
            v11=dict(at=0x11b31b, rdx='object+0x10 handle', r8='zero-extended header+264', r9=0),
            v10=dict(at=0x11b33a, rdx='object+0x10 handle', r8='-316 * signed(header+260)', r9=2),
            confidence='seek-shaped argument contract, not verified helper semantics'),
        body_entry_fields={str(n): 'unresolved; NOT the archive-header field at this offset'
                           for n in (256,260,264)},
        unresolved=['body offset/length and +256/+260/+264 entry semantics',
                    'wrapper header/cipher/key/codec/checksum and all input consumption',
                    'indirect-slot import binding and full open/header error handling',
                    'meaning of EDX=0x907caf84 passed to 0x11c570 (not evidence of a key)'],
        next_bounded_targets=[dict(rva=0x11b35e, reason='unexamined continuation after count test'),
                              dict(rva=0x11adf0, reason='verify directory-position helper'),
                              dict(rva=0x11c570, reason='explain explicit pre-open initialization argument')],
        extraction_contract_proven=False, selected_exported=0, codec_attempts=0)


def selected_baseline(index_bytes, probe_bytes):
    index, probe = json.loads(index_bytes), json.loads(probe_bytes)
    require(probe['source_index_sha256'] == digest(index_bytes), 'prior index identity mismatch')
    selected = [e for e in probe['entries'] if e['role'] == 'selected']
    require(len(selected) == 12 and len({(e['archive'],e['name']) for e in selected}) == 12,
            'expected original twelve unique selections')
    require(not any(e['accepted'] for e in selected), 'prior acceptance changed; review needed')
    return dict(source='existing v37/v39 reports only; no archive bodies reread in v47',
                archive_count=len(index['manifest']),
                directory_entry_count=sum(e['entries'] for e in index['manifest']),
                selected=[dict(archive=e['archive'],name=e['name'], body_offset=e['offset'],
                               entry_256=e['indexed_size'],entry_260=e['length'],entry_264=e['compressed'],
                               body_sha256=e['body_sha256'], old_reference=e.get('old', {}).get('sha256'),
                               old_length=e.get('old', {}).get('length'), plaintext_validated=False)
                          for e in selected],
                note='Historical reference is not current-version identity; size equality is insufficient.')


def observed_body_plan(entry):
    """Only dataflow planning; deliberately not an unpacker or acceptance gate."""
    n = entry['entry_256']
    require(type(n) is int and 0 <= n <= 0x7fffffff, 'unsafe signed body length')
    require(type(entry['entry_260']) is int and 0 <= entry['entry_260'] <= 0x7fffffff,
            'unsafe output capacity')
    return dict(seek_offset=entry['body_offset'], read_bytes=n,
                output_capacity=entry['entry_260'], entry_264_not_used_by_observed_read=entry['entry_264'],
                wrapper_probe_bytes=16 if n >= 16 else 0,
                wrapped_input_before_tail_adjustment=n-16 if n >= 16 else None,
                wrapper_detected=None, algorithm=None, plaintext_validated=False)


def static_dependency(view, rva, size):
    try:
        raw=view.read(rva,size)
    except ValueError as error:
        return dict(rva=rva,size=size,available=False,reason=str(error))
    return dict(rva=rva,size=size,available=True,bytes=raw.hex(),sha256=digest(raw),
                provenance=view.provenance(rva,size))


def build_body_contract(view, functions):
    rows={r['rva']:r for f in functions for r in f['instructions']}
    for rva,expected in BODY_EVIDENCE.items():
        require(rva in rows and rows[rva]['bytes']==expected,
                f'body instruction changed/missing at {rva:#x}')
        require(rows[rva]['entry_reachable'],f'unreachable body evidence at {rva:#x}')
    return dict(status='body read and wrapper/codec call parameters established; algorithms unresolved',
        evidence_instructions=[rows[rva] for rva in BODY_EVIDENCE],
        name_to_body_edges=[dict(at=a,target=t) for a,t in
                           [(0x1eef46,0x1ed0d0),(0x1ed1ec,0x24f380),(0x24f413,0x11d220),
                            (0x24f423,0x11b4f0),(0x11b528,0x11bb30)]],
        entry_address='object[+0x2d0] + signed(lookup index) * 316; lookup -1 rejected',
        entry_fields={
            '256':dict(role='body allocation and actual read-helper length',evidence=[0x11b55f,0x11b571,0x11b57f]),
            '260':dict(role='decoded-output allocation and codec output-capacity in/out',evidence=[0x11b5ab,0x11b9be,0x11b9d9]),
            '264':dict(role='no separate input-length/codec use observed; copied with full 316-byte descriptor',
                       warning='not proven meaningless/reserved; other callsites remain outside scope'),
            '268':dict(role='absolute-position-shaped helper input, R9=0',evidence=[0x11b54c,0x11b55a])},
        read_helper=dict(target=0x11ae60,caller=0x11b57f,rdx='archive handle',r8='body buffer',r9d='entry+256',
                         indirect_slot=0xe6d730,success_check='API-like BOOL plus requested==0 or read_count>=1',
                         short_read_warning='helper does not require read_count==requested; body caller does not test return'),
        wrapper_steps=[
            dict(step=1,call=0x11b589,target=0x11c570,rdx=0x907caf84,
                 rcx='not freshly assigned; global-style initializer must not be assumed to use this'),
            dict(step=2,call=0x11b632,target=0x120230,rcx='copy of first 16 body bytes',edx=16,r8b=1),
            dict(step=3,evidence=[0x11b651,0x11b65e,0x11b66d],
                 operation='compare processed first 7 bytes against global string at 0x1159f40; mismatch uses original body codec path'),
            dict(step=4,evidence=[0x11b6fd,0x11b89b,0x11b8c6],
                 operation='substring after first 7 chars, capped at 16; parse with base=16 and end pointer; invalid-stoll/ERANGE branches; parsed 64-bit result passed in RDX to initializer'),
            dict(step=5,call=0x11b905,target=0x120230,rcx='copy of body[16:entry+256]',edx='entry+256 minus 16',r8b=0),
            dict(step=6,evidence=[0x11b90a,0x11b913,0x11b918],
                 operation='if signed remaining length >16, subtract sign-extended final byte; not proof of PKCS#7 validation'),
            dict(step=7,call=0x11b9e0,target=0x23c780,rcx='output allocation',rdx='pointer to uint32 output capacity/result',
                 r8='processed input copy',r9d='length after tail adjustment',initial_output_capacity='entry+260')],
        codec=dict(target=0x23c780,raw_fallback_call=0x11b5d3,wrapped_call=0x11b9e0,
                   raw_source_length='entry+256',wrapped_source_length='entry+256-16-signed_tail_if_len_gt16',
                   init_target=0x23d350,process_target=0x23d4b0,process_mode=4,end_target=0x23eb80,
                   process_success=1,outer_success=0,output_length='context+0x1c copied back to caller on success',
                   identity='zlib-uncompress-shaped context/status contract; inner codec not inspected, not an algorithm proof',
                   validation_limit='caller checks return==0, not equality of output length and entry+260; no full input-consumption proof'),
        initializer_boundary=dict(entry=0x11c570,jump_target=0x1b469ec,
                                  warning='only entry JMP reachable; remainder not decoded/executed as meaningful code'),
        block_processor=dict(entry=0x120230,requires='nonzero length divisible by 16',
            branch_flag=0x12b1ee3,
            flag_zero=dict(target=0x120040,mode1_context=0x12b1fb0,mode1_word=0x12b20a0,
                           mode0_context=0x12b22d0,mode0_word=0x12b23c0),
            flag_nonzero=dict(target=0x11f4e0,context_words=[8,14],mode1_context=0x12b1f90,mode0_context=0x12b20b0),
            warning='block size, context counts and prior AES tables do not establish cipher/mode/key here'),
        static_dependencies=[static_dependency(view,0x1159f40,32)]+[
            static_dependency(view,a,16) for a in (0x12b1ee3,0x12b1f90,0x12b1fb0,0x12b20a0,0x12b20b0,0x12b22d0,0x12b23c0)],
        output_object=dict(bytes=0x158,entry_copy_offset=8,entry_copy_bytes=316,
                           decoded_buffer_offset=0x148,initial_cursor_offset=0x150),
        next_targets=[dict(rva=0x11bb30,reason='verify exact resource-name matching'),
                      dict(rva=0x1b469ec,reason='initializer entry JMP destination; no exception entry established'),
                      dict(rva=0x120040,reason='actual mode-selected block transform'),
                      dict(rva=0x23d350,reason='verify codec identity/init parameters')],
        extraction_contract_proven=False,selected_exported=0,codec_attempts=0)


def range_mapping(view,rva,size):
    section=view.section(rva)
    result=dict(requested_rva=rva,requested_size=size,section=section,
                decoded=static_dependency(view,rva,size))
    if section and section['name'] in view.segments:
        base,data=view.segments[section['name']]
        result['decoded_extent']=dict(begin=base,end=base+len(data),bytes=len(data),
                                      requested_segment_offset=rva-base)
    result['covering_decoded_segments']=[name for name,(base,data) in view.segments.items()
                                         if base<=rva and rva+size<=base+len(data)]
    try:
        raw=view.file_read(rva,size)
        result['original_file_backing']=dict(available=True,offset=view.file_offset(rva,size),
                                             bytes=raw.hex(),sha256=digest(raw))
    except ValueError as error:
        result['original_file_backing']=dict(available=False,reason=str(error))
    return result


def narrow_global_refs(view):
    """Candidates for two known globals only, not an unrestricted string scan."""
    sources=[(n,*view.segments[n]) for n in ('.text','.data0')]
    section=next(s for s in view.sections if s['name']=='.data2')
    sources.append(('.data2/original',section['rva'],view.file_read(section['rva'],section['virtual_size'])))
    refs=[]
    for name,base,data in sources:
        for rex in (0x48,0x4c):
            for op in (0x8b,0x89,0x8d):
                for reg in range(8):
                    prefix=bytes([rex,op,5+8*reg]); at=-1
                    while True:
                        at=data.find(prefix,at+1)
                        if at<0:break
                        if at+7>len(data):continue
                        target=base+at+7+struct.unpack_from('<i',data,at+3)[0]
                        if target in (0x12b4c20,0x1159f40):
                            refs.append(dict(section=name,rva=base+at,target=target,
                                             bytes=data[at:at+7].hex(),exception_range=view.function(base+at)))
    return dict(targets=[0x12b4c20,0x1159f40],forms='7-byte RIP-relative MOV/LEA, REX=48/4c only',
                sources=[dict(name=n,begin=b,bytes=len(d)) for n,b,d in sources],candidates=refs,
                limitation='Uninspected candidates are not instruction-boundary-validated references; indirect/address-transformed initialization is not excluded.')


def build_algorithm_contract(view,functions):
    rows={r['rva']:r for f in functions for r in f['instructions']}
    pins={0x120090:'660f38de00',0x1200a4:'66410f38df04c1',
          0x11fd10:'f30f6f5910',0x11fd15:'f30f6f01',0x11fd49:'660f3adfc301',
          0x12001f:'660f3adfc340',0x12002e:'f30f7f92e0000000',
          0x1b47093:'660f38db4c2430',0x1b4709a:'41c780f00000000e000000',
          0x1b46a58:'48391529b576ff',0x1b46a75:'488b0da4e176ff',
          0x1b46a96:'48395020',0x1b46afb:'4883c228',0x1b46b15:'488d5328',
          0x23d350:'ba0f000000',0x23d355:'e906ffffff',
          0x23d2fc:'894208',0x23d312:'897a28',0x23d328:'e9d3feffff',
          0x23d251:'c7421400800000',0x23d669:'3c08',
          0x240bc8:'81faf1ff0000',0x240bdd:'3df1ff0000'}
    for rva,value in pins.items():
        require(rva in rows and rows[rva]['bytes']==value and rows[rva]['entry_reachable'],
                f'algorithm byte/reachability evidence changed at {rva:#x}')
    cipher=next(f for f in functions if f['begin']==0x120040)
    schedule=next(f for f in functions if f['begin']==0x11fd10)
    rcons=[int(r['bytes'][-2:],16) for r in schedule['instructions'] if r['instruction'].startswith('aeskeygenassist ')]
    require(rcons==[1,0,2,0,4,0,8,0,16,0,32,0,64],'AES-256 expansion sequence changed')
    codec=next(f for f in functions if f['begin']==0x23d4b0)
    diagnostics=[dict(instruction=r['rva'],string=m['string']) for r in codec['instructions']
                 for m in r.get('memory',[]) if r['entry_reachable'] and m.get('string')]
    needed={'unknown compression method','incorrect header check','invalid stored block lengths',
            'invalid literal/lengths set','invalid distances set','incorrect data check'}
    require(needed <= {d['string']['text'] for d in diagnostics},'codec diagnostics/reachability changed')
    return dict(status='AES-256 independent-block hardware path and zlib-wrapped DEFLATE established; material initialization unresolved',
        phase3_scope=dict(new_logical_functions=9,exception_ranges=7,direct_entry_windows=4,
                          continuation_ranges=[0x23d286,0x23d2e6]),
        evidence_instructions=[rows[rva] for rva in pins],
        cipher=dict(identity='AES-256 decryption, independent-block/ECB hardware path',
                    block_bytes=16,material_bytes=32,rounds=14,
                    block_function=0x120040,expansion_entry=0x11fd10,schedule_entry=0x1b47030,
                    expansion_rcons=rcons,
                    evidence='AESKEYGENASSIST from two 16-byte source loads; 14-round inverse schedule via AESIMC; AESDEC/AESDECLAST loop',
                    mode_limit='No cross-block feedback or IV in this complete block function; software fallback not independently reconstructed',
                    actual_material_available=False,executed_native_instructions=0,
                    full_block_code_sha256=cipher['code_sha256']),
        selector=dict(initial=0x907caf84,meaning='signed 64-bit ordered-map lookup selector, not the 32-byte cipher material',
                      cached_selector_global=0x12b1f88,map_pointer_global=0x12b4c20,
                      node_selector_offset=0x20,node_string_offset=0x28,
                      header_material='value string at head.leftmost_node+0x28',
                      body_material='value string at matched_node+0x28',
                      warning='initializer/cache/map startup not recovered; no selector value is substituted for missing material'),
        codec=dict(identity='zlib-wrapped DEFLATE state machine',init_stub=0x23d350,init_impl=0x23d260,
                   window_bits=15,window_bytes=32768,wrapper_flags=1,method=8,process_mode=4,
                   process_function=0x23d4b0,dispatch=codec['resolved_switch'],diagnostics=diagnostics,
                   checksum_helper=0x240bb0,checksum_evidence='single-byte path is two sums modulo 65521 (Adler-32); trailer mismatch branch observed',
                   version='not identified',input_consumption_proven=False),
        mappings=[range_mapping(view,a,n) for a,n in
                  [(0x1b469ec,0x644),(0x1b47030,0x1dd),(0x1159f40,32),
                   (0x12b1ee3,1),(0x12b1f88,8),(0x12b1f90,32),(0x12b1fb0,244),
                   (0x12b20b0,32),(0x12b22d0,244),(0x12b4c20,8)]],
        global_reference_candidates=narrow_global_refs(view),
        remaining=['selector-map construction/content and the two selected 32-byte material strings',
                   'seven-byte wrapper marker initialization at 0x1159f40',
                   'strict exact decoded length / complete EOF / old SHA verification on selected bodies'],
        selected_exported=0,codec_attempts=0,extraction_contract_proven=False)


def run_owned_tests():
    """Record only this sidecar's tests, never a shared runner or XML output."""
    import io
    import time
    import unittest
    from datetime import datetime, timezone

    class Recorded(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.records=[]

        def addSuccess(self, test):
            super().addSuccess(test)
            self.records.append(dict(id=test.id(),status='passed'))

        def addFailure(self, test, error):
            super().addFailure(test,error)
            self.records.append(dict(id=test.id(),status='failed',detail=self.failures[-1][1]))

        def addError(self, test, error):
            super().addError(test,error)
            self.records.append(dict(id=test.id(),status='error',detail=self.errors[-1][1]))

        def addSkip(self, test, reason):
            super().addSkip(test,reason)
            self.records.append(dict(id=test.id(),status='skipped',reason=reason))

    capture=io.StringIO()
    suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_pak_reader_v47')
    started=time.perf_counter()
    result=unittest.TextTestRunner(stream=capture,verbosity=2,resultclass=Recorded).run(suite)
    return dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),python=sys.version,
                executable=sys.executable,dont_write_bytecode=sys.dont_write_bytecode,
                tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                skips=len(result.skipped),success=result.wasSuccessful(),
                elapsed_seconds=time.perf_counter()-started,records=result.records,
                output=capture.getvalue())


def render(report):
    lines=['# v47 PAK reader static trace','',
        'Static bytes only; no client/DLL execution, live process, network, runtime PE or codec guessing.',
        f"Reviewed code units: {len(report['functions'])}; 41 exception ranges + 4 explicitly anchored leaf windows. Exported selected resources: 0/12.",'',
        'Entry anchor: existing v44 verified `Pak File %ls` reference inside 0x92c0e0; selected direct/transitive callees and one fallthrough fragment only.',
        'Latest: phase 3 establishes the AES-256 hardware block path and zlib-wrapped DEFLATE. Selector-map material and marker initialization remain unresolved; no plaintext exported.','']
    if report.get('tests'):
        t=report['tests']
        lines += [f"Tests: {t['tests']} run; {t['failures']} failures; {t['errors']} errors; {t['skips']} skipped. Source-pinned details: pak_tests.json.",'']
    lines += ['## 第三阶段：算法与缺失材料边界（最新）','',
        '**AES-256 不是根据 import 或16字节长度猜测：** `0x120090 AESDEC`、`0x1200a4 AESDECLAST` 位于完整独立块循环；`0x11fd10` 从输入加载32字节，以 AESKEYGENASSIST 常数 `1,0,2,0,4,0,8,0,16,0,32,0,64` 展开；`0x1b47030` 用 AESIMC 构造逆序 schedule 并写入14轮。该硬件路径每块独立，无跨块反馈/IV，是 AES-256 ECB 解密路径。软件回退尚未独立重现。全部只是静态指令，不执行 AES/native/client 代码。','',
        '**实际 codec：zlib-wrapped DEFLATE，windowBits=15。** `0x23d350` 是无异常表的10字节直接调用目标，设置 EDX=15 后跳到0x23d260。其 wrapper 标志=1、窗口位数=15，reset 写入0x8000窗口。0x23d4b0 状态机采用 method=8，校验 header/window、处理 stored/Huffman length-distance，最后有 data-check 错误分支。校验 helper 的单字节路径是模65521双和（Adler-32）；版本号未识别。',
        '状态机的30项RVA跳表位于0x23eafc；已检验所有29个不同目标落在解码指令边界，再标记可达分支。末尾120字节跳表不作为指令解码。0x23d350/0x11fd10/0x23d200/0x240bb0 没有异常记录，报告保留其直接调用来源；最后一个仅检查单字节checksum分支，未冒充完整函数。','',
        '**初始化参数是 selector，不是密钥字节。** 0x1b469ec 先缓存/比较传入64位值，在0x12b4c20指向的有序树中匹配 node+0x20；node+0x28 是材料字符串。首块上下文来自 head 的最左节点材料，正文上下文来自匹配节点材料；处理函数实际读取32字节。0x907caf84 及首块解析得到的十六进制数均不能直接代替这32字节。','',
        '### 精确可用/缺失映射','',
        '|对象|结果|', '|---|---|',
        '|0x1b469ec–0x1b47030 初始化|位于已解 .data0；完整可读，原文件 raw_size=0，不是缺失/别名|',
        '|0x1b47030–0x1b4720d schedule|位于同一已解 .data0，完整可读|',
        '|.data 已解范围|[0x1132000,0x1183000)，仅331776字节|',
        '|selector cache/map、块上下文|0x12b1ee3 等及0x12b4c20 位于 .data 虚拟范围，但超过已解字节；原文件 raw_size=0，其他已解段无覆盖|',
        '|0x1159f40 标识全局|静态字符区为零、length=7、capacity=15；不是有效已初始化标识|','',
        '仅对上述两个已知全局，在 .text/.data0 与原文件-backed .data2 做了限定 MOV/LEA RIP 引用候选检查；未检查的候选不升级为调用证据。此检查不能排除间接寻址/地址变换后的构造函数。没有补零、调用CPUID、启动游戏、访问DLL/live memory或网络。',
        '本阶段新增9个逻辑函数（7个异常范围含2个连续片段，另4个直接入口窗口）。尚缺初始化表与标识的构造来源；不会用碰撞、旧标签、密钥穷举或成功解压形状替代材料证据。当前 selected 仍0/12，未尝试新的 codec，保留严格长度/EOF/旧SHA验证作为下一步验收。','']
    lines += ['## 第二阶段：正文与wrapper参数（历史阶段，算法身份以上文为准）','',
        '`0x1eeed0 → 0x1ed0d0 → 0x24f380 → 0x11b4f0` 是按资源名进入 PAK 流的真实直接调用链。普通文件 rb/wb 路径位于另外的 0x22ec20/0x22edf0 分支，不能混作 PAK 解码器。',
        '0x24f413 调 0x11d220 查找归档；0x24f423 以 RCX=返回的归档对象、RDX=原资源名调 0x11b4f0。后者调 0x11bb30，索引非 -1 时以 `archive[+0x2d0] + index*316` 定位目录项。两个查找函数内部尚未展开。','',
        '|目录项字段|正文路径中实际用途|调用/指令 RVA|', '|---|---|---|',
        '|+256|分配正文缓冲区，并作为实际读取长度|0x11b55f、0x11b571 → 0x11b57f/0x11ae60|',
        '|+260|分配解码输出；初始化传给 codec 的 uint32 长度指针|0x11b9be、0x11b9d9 → 0x11b9e0|',
        '|+264|没有单独用于该读取/解码调用；仍随完整目录描述符复制|不能据此声称全程序不使用或是保留位|',
        '|+268|传给定位 helper 的 R8D；R9D=0|0x11b54c → 0x11b55a/0x11adf0|','',
        '### 16 字节 wrapper 的真实参数链','',
        '1. `0x11b589 → 0x11c570`：RDX=0x907caf84；此常量是初始化输入，不是已经恢复的密钥。',
        '2. 复制正文首 16 字节；`0x11b632 → 0x120230(copy,16,1)`。与全局字符串前 7 字节逐段比较；不匹配则退回原始正文的 codec 调用，不是直接报错。',
        '3. 匹配后，将处理后的字符串从第 7 字节之后取最多 16 个字符，以 base=16 的 stoll 形状解析；解析的 64 位结果于 0x11b8c6 再传入 0x11c570 的 RDX。',
        '4. 复制 `body[16:+256]`，调用 `0x120230(copy,+256-16,0)`。若此长度>16，则减去处理后最后一个字节的**有符号**值；没有观察到逐个填充字节校验，不能直接称为 PKCS#7。',
        '5. `0x23c780(dst,&out_len,processed_src,adjusted_len)`；out_len 初值=目录项+260。未命中 wrapper 的回退支路则对原正文及 +256 长度直接调用同一 codec。','',
        '0x23c780 创建流上下文，调 0x23d350 初始化、以 mode=4 调 0x23d4b0、以返回1为流成功，复制产生长度到调用者，再调 0x23eb80。结构类似 zlib uncompress，但内部 codec 尚未检查，**尚无算法身份证据或 EOF/全输入消耗验证**。PAK 调用者也未比较实际输出长度与声明 +260 是否相等。',
        '0x11ae60 的间接读取仍指向槽 0xe6d730，成功条件只要求正请求读到>=1字节，而非完整读满；0x11b4f0 还未检查此 helper 的返回值。未来离线镜像须比该路径更严格验证输入长度，不能依赖它的短读检查。','',
        '### 当前可复现边界','',
        '初始化入口 0x11c570 仅有可达 `jmp 0x1b469ec`；没有追入该目标，也未把跳转后的不透明字节当可执行流程。',
        '0x120230 对非零16字节倍数进行双分支处理：flag=0 调 0x120040，mode1/0 使用不同全局上下文；flag非0 调 0x11f4e0，栈上下文常数为8/14。这些不是独立的 AES/模式/密钥证明。',
        'RVA 0x12b1ee3、0x12b1f90、0x12b1fb0、0x12b20a0、0x12b20b0、0x12b22d0、0x12b23c0 不在 StaticView 已解可读字节范围，读取明确失败，未补零。标识全局 0x1159f40 的静态字符区为零、长度字段为7、容量15；不能猜标识文本。',
        '因此目前没有可信纯 Python 解封装实现、没有选定资源明文、没有新确认技能关联。下一阶段最短路径是初始化跳转目标、实际块变换 0x120040 和 codec 初始化 0x23d350，必要时补 0x11bb30 的名字匹配。','']
    lines += ['## 第一阶段可复核结论','',
        '`0x92c24c → 0x1eca50 →(tail 0x1eca94) 0x11ca80 →(call 0x11cddf) 0x11d000 →(call 0x11d151) 0x11b250`。',
        '根引用 `Pak File %ls`，目录枚举函数直接使用 `pak` 扩展名。0x11d000 接收 RCX=管理器、RDX=构造的窄字符 PAK 路径、R8=根目录；分配 0x300 字节对象后，以 RCX=对象、RDX=PAK 路径调用 0x11b250。此时 R8 未证明为布尔参数。','',
        '0x11b29c 的间接槽是 RVA 0xe6d658；参数为 filename, 0x80000000, 1, 0, 3, 0x80, 0，返回值存 object+0x10，符合只读文件打开形状。',
        '0x11b2e3 的间接槽是 RVA 0xe6d730；RCX=上述返回值，RDX=object+0x18，R8D=0x110，R9=读字节数地址，栈参数=0，符合 272 字节头读取形状。',
        '**限制：两个槽未与原始 PE 导入表可靠绑定，不能宣称已证实 CreateFileA/ReadFile API。没有调用任何 API 或执行客户端字节。**','',
        '|头部偏移（不是目录项）|对象偏移|直接指令证据|静态操作|',
        '|---|---|---|---|',
        '|+256|+0x118|0x11b2e9–0x11b2f5|仅 version 10/11 通过此分支|',
        '|+260|+0x11c|0x11b322、0x11b333|符号扩展 count，乘 -316|',
        '|+264|+0x120|0x11b30d、0x11b31b|v11 作为 helper 的 R8D，R9D=0|',
        '|+268|+0x124|0x11b300、0x11b307|v11 此字节=1 时转入 0x11b4d4|','',
        'v10 向 0x11adf0 传入 RDX=handle、R8=-316×count、R9D=2，并把返回 EAX 存回 object+0x120；符合从末尾定位目录的形状，但 helper 尚未检查，不能提升为已确认 Seek 契约。',
        '这些是“包头”字段，不是目录记录；第二阶段已在上文单独建立目录项正文长度/输出容量的证据。密码学算法和完整 EOF/校验仍未证明。','',
        '## 原有 12 项冻结范围','',
        f"仅引用既有报告：{report['baseline']['archive_count']} PAK / {report['baseline']['directory_entry_count']} 项；本轮正文读取 0、codec 尝试 0、selected 明文导出 0。",'',
        '|原有 selected 路径|+256|+260|+264|', '|---|---:|---:|---:|']
    for e in report['baseline']['selected']:
        lines.append(f"|{e['archive']}:{e['name']}|{e['entry_256']}|{e['entry_260']}|{e['entry_264']}|")
    lines += ['', '旧资源 SHA 和正文 SHA 保留于 JSON，仅追溯既有审计，并非本轮重新读取正文。旧版长度相等不等于当前内容等价。', '',
        '## 停止点和下一阶段入口','',
        '第一阶段10范围之后，按新增授权检查15个新的逻辑函数；异常表拆分额外读取9个连续片段，总共34个范围。逻辑归组和逐范围字节都在 JSON 中列出。停止于已确定的变换/初始化边界，不扩大密文样本或猜 codec。',
        '已经有 PAK 正文 loader 的调用及 wrapper 参数证据，但不是成功解包。当前不足以编写可信变换/codec 镜像，故不输出 selected_assets。','',
        '## 字节证据附录','']
    for f in report['functions']:
        anchor=(f"Exception record {f['exception_record_rva']:#x}: `{f['exception_record_bytes']}`"
                if f['exception_record_rva'] is not None else
                f"Direct-target window from {f['direct_entry_window']['caller']:#x}; {f['direct_entry_window']['kind']}; no exception record")
        lines += [f"## {f['begin']:#x}–{f['end']:#x}",'',
                  f"{anchor}; code SHA `{f['code_sha256']}`.",'','```text']
        for r in f['instructions']:
            comment=[]
            if 'direct_target'in r:comment.append(f"target RVA {r['direct_target']:#x}")
            for m in r.get('memory',[]):
                if m.get('string'):comment.append(repr(m['string']['text']))
            lines.append(f"{r['rva']:08x} {r['bytes']:<30} {r['instruction']}"+
                         ((' ; '+'; '.join(comment))if comment else '')+
                         (' [not entry-reachable]'if not r['entry_reachable'] else ''))
        lines+=['```','']
    lines+=['## Limits','',
        'Original exception records anchor byte ranges, not proof of runtime reachability/final fixups. Only the supplied decoded text prefix has the prior native-byte comparison; no client code is executed here.',
        f"Script SHA `{report['script_sha256']}`. Exact sources and instruction bytes are retained in pak_trace.json.",'']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test',action='store_true',help='run only tests.test_pak_reader_v47 and save pak_tests.json')
    args=parser.parse_args()
    view=StaticView()
    for name in READ_REPORTS + SOURCE_FILES:
        view.load(ROOT/name)
    functions=disassemble_selected(view,FUNCTIONS+EXTRA_FUNCTIONS)
    functions+=disassemble_selected(view,PHASE3_FUNCTIONS)
    functions+=[inspect_function(view,w['begin'],w) for w in PHASE3_LEAVES]
    require(len(functions)==45,'total phase budget mismatch')
    report=dict(schema='pak-reader-static-v47',functions=functions,input_sha256=view.inputs,
        script_sha256=digest(Path(__file__).read_bytes()),selected_limit=12,selected_exported=0,
        extraction_contract_proven=False,codec_attempts=0,client_calls=0,archive_body_bytes_read=0,
        contract=build_contract(functions),
        phase2=dict(additional_logical_functions=15,additional_continuation_ranges=9,
                    continuations={hex(k):hex(v) for k,v in CONTINUATIONS.items()}),
        body_contract=build_body_contract(view,functions),
        algorithm_contract=build_algorithm_contract(view,functions),latest_contract='algorithm_contract',
        baseline=selected_baseline(view.load(ROOT/READ_REPORTS[1]),view.load(ROOT/READ_REPORTS[3])),
        indirect_slots=[dict(rva=s, bytes=view.read(s,8).hex(),provenance=view.provenance(s,8),
                             binding='unresolved; signature only') for s in (0xe6d658,0xe6d730)])
    report['selected_read_plans']=[observed_body_plan(e) for e in report['baseline']['selected']]
    if args.test:
        report['tests']=run_owned_tests()
    require(all(digest(Path(p).read_bytes())==h for p,h in view.inputs.items()),'input changed')
    report['inputs_unchanged']=True
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'pak_trace.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf-8')
    (OUT/'pak_trace.md').write_text(render(report),'utf-8')
    if args.test:
        test_report=dict(report['tests'],input_sha256=view.inputs,inputs_unchanged=True,
                         report_sha256=digest((OUT/'pak_trace.json').read_bytes()))
        (OUT/'pak_tests.json').write_text(json.dumps(test_report,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps(dict(functions=len(functions),selected_exported=0,
        calls=[dict(begin=hex(f['begin']),targets=sorted({hex(r['direct_target']) for r in f['instructions'] if 'direct_target'in r and r['instruction'].startswith('call ')})) for f in functions]),ensure_ascii=False))
    if args.test and not report['tests']['success']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
