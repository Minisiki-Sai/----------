"""Fresh full-path-correct offline guest with resumable self-file state."""
from hashlib import sha256
import argparse
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import client_guest_snapshot_v41 as snapshots
from tools.emulate_client_file_v42 import FileExperiment,v39,RAW_FILE_BASE
from tools.client_checksum_model_v41 import install_checksum_accelerator
from tools.client_file_checksum_v42 import FileChecksumController
from tools.continue_client_v41 import atomic

OUT=ROOT/'outputs/client_protection_v42_20260908'


def encode(e):
    raw=snapshots.encode_snapshot(e)
    result=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source,zipfile.ZipFile(result,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as target:
        for info in source.infolist():
            data=source.read(info)
            if info.filename=='manifest.json':
                meta=json.loads(data)
                meta['file_state_v42']=dict(handles=e.file_handles,next_handle=e.next_file_handle,
                    calls=e.file_calls,read_bytes=e.total_file_read_bytes,full_paths=True,
                    sections=e.file_sections,next_section=e.next_file_section,
                    views=e.file_views,view_reads=e.file_view_reads,protections=e.page_protections)
                data=json.dumps(meta,separators=(',',':')).encode()
            target.writestr(info.filename,data)
    return result.getvalue()


def restore(raw,blob,digest):
    if len(blob)>snapshots.MAX_BYTES or sha256(blob).hexdigest()!=digest:
        raise ValueError('v42 snapshot hash/size mismatch')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if archive.getinfo('manifest.json').file_size>8*1024*1024:
            raise ValueError('v42 manifest budget')
        state=json.loads(archive.read('manifest.json'))['file_state_v42']
    state.setdefault('sections',{});state.setdefault('next_section',0xd000)
    state.setdefault('views',[]);state.setdefault('view_reads',dict(count=0,bytes=0,minimum=None,maximum=None))
    state.setdefault('protections',{})
    if set(state)!=set(('handles','next_handle','calls','read_bytes','full_paths','sections','next_section','views','view_reads','protections')) or state['full_paths'] is not True:
        raise ValueError('v42 file snapshot schema')
    expected_view=dict(base=RAW_FILE_BASE,size=v39.align(len(raw)),source_sha256=sha256(raw).hexdigest(),unmapped=False)
    views=state['views']
    if not isinstance(views,list) or len(views)>1 or (views and (type(views[0].get('unmapped')) is not bool or views[0]!={**expected_view,'unmapped':views[0]['unmapped']})):
        raise ValueError('invalid v42 readonly file view')
    extra=[(RAW_FILE_BASE,raw.ljust(v39.align(len(raw)),b'\0'))] if views and not views[0]['unmapped'] else []
    e=snapshots.restore_snapshot(raw,blob,digest,experiment_type=FileExperiment,extra_readonly_regions=extra)
    calls=state['calls']
    if not isinstance(calls,list) or len(calls)>256:
        raise ValueError('v42 call budget')
    live={};opened=0;total=0;sections={};created=0;proved_views=[]
    for call in calls:
        if type(call.get('modelled')) is not bool:
            raise ValueError('invalid v42 call record')
        if not call['modelled']:
            continue
        if call['function']!='NtReadFile' and call.get('status')!='0x0':
            raise ValueError('successful file-model evidence has a failure status')
        handle=int(call['handle'],16)
        if call['function']=='NtOpenFile':
            expected_path='\\??\\'+str(v39.CLIENT).replace('/','\\')
            insensitive=call.get('path_case_insensitive',False)
            if type(insensitive) is not bool:
                raise ValueError('v42 path comparison mode')
            key=lambda value: ''.join(chr(ord(c)+32) if 'A'<=c<='Z' else c for c in value)
            path_equal=key(call['path'])==key(expected_path) if insensitive else call['path']==expected_path
            if handle!=0xc000+4*opened or call['source_sha256']!=e.sha or not path_equal:
                raise ValueError('v42 open evidence mismatch')
            live[handle]=dict(position=0);opened+=1
        elif call['function']=='NtReadFile':
            offset=snapshots.integer(call['offset']);length=snapshots.integer(call['requested'],1,4096)
            data=e.file_bytes[offset:offset+length]
            if handle not in live or call['returned']!=len(data) or call['data_sha256']!=sha256(data).hexdigest() or call.get('status')!=hex(0 if data else 0xc0000011):
                raise ValueError('v42 read evidence mismatch')
            if data:
                live[handle]['position']=offset+len(data)
            total+=len(data)
        elif call['function']=='NtClose' and handle in live:
            del live[handle]
        elif call['function']=='NtCreateSection':
            if handle!=0xd000+4*created or int(call['file_handle'],16) not in live or call['source_sha256']!=e.sha or call['size']!=len(e.file_bytes):
                raise ValueError('v42 section evidence mismatch')
            sections[handle]=dict(size=len(e.file_bytes),source_sha256=e.sha);created+=1
        elif call['function']=='NtClose' and handle in sections:
            del sections[handle]
        elif call['function']=='NtMapViewOfSection':
            if handle not in sections or proved_views or call['base']!=hex(RAW_FILE_BASE) or call['size']!=expected_view['size'] or call['source_sha256']!=e.sha:
                raise ValueError('v42 map evidence mismatch')
            proved_views.append(dict(expected_view))
        elif call['function']=='NtUnmapViewOfSection':
            if handle or call['base']!=hex(RAW_FILE_BASE) or not proved_views or proved_views[0]['unmapped']:
                raise ValueError('v42 unmap evidence mismatch')
            proved_views[0]['unmapped']=True
        elif call['function']=='NtProtectVirtualMemory' and not handle:
            from tools.client_virtual_protection_v43 import replay
            replay(e,call)
        else:
            raise ValueError('unknown v42 file call')
    if len(live)>4 or {str(k):v for k,v in live.items()}!=state['handles'] or state['next_handle']!=0xc000+4*opened or total!=state['read_bytes'] or total>64*1024*1024:
        raise ValueError('v42 file state disagrees with evidence')
    if len(sections)>4 or {str(k):v for k,v in sections.items()}!=state['sections'] or state['next_section']!=0xd000+4*created:
        raise ValueError('v42 section state disagrees with evidence')
    if proved_views!=views:
        raise ValueError('v42 view state disagrees with evidence')
    if {str(k):v for k,v in e.page_protections.items()}!=state['protections']:
        raise ValueError('v42 logical protection state disagrees with evidence')
    reads=state['view_reads']
    if set(reads)!=set(('count','bytes','minimum','maximum')):
        raise ValueError('v42 view read schema')
    snapshots.integer(reads['count'],0,10**13);snapshots.integer(reads['bytes'],0,10**15)
    if reads['count']:
        snapshots.integer(reads['minimum'],0,len(raw)-1);snapshots.integer(reads['maximum'],reads['minimum']+1,len(raw))
    elif reads['minimum'] is not None or reads['maximum'] is not None or reads['bytes']:
        raise ValueError('v42 empty view read metadata')
    e.file_handles=live;e.next_file_handle=state['next_handle'];e.file_calls=calls;e.total_file_read_bytes=total
    e.file_sections=sections;e.next_file_section=state['next_section']
    e.file_views=views;e.file_view_reads=reads
    if extra:
        e.watch_file_view()
    return e


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume-sha256')
    parser.add_argument('--resume-previous',action='store_true')
    parser.add_argument('--accelerate-file-checksum',action='store_true')
    parser.add_argument('--segments',type=int,choices=range(1,121),default=40)
    args=parser.parse_args()
    OUT.mkdir(exist_ok=True)
    path=OUT/'checkpoint.zip'
    if path.exists() and not args.resume_sha256:
        raise SystemExit('Existing v42 checkpoint retained; require exact resume hash.')
    if args.resume_previous and not args.resume_sha256:
        raise SystemExit('Previous checkpoint requires its independently recorded hash.')
    sources=[Path(__file__),Path(snapshots.__file__),ROOT/'tools/emulate_client_file_v42.py',
             ROOT/'tools/emulate_client_sections_v40.py',Path(v39.__file__),ROOT/'tools/client_checksum_model_v41.py',ROOT/'tools/client_file_checksum_v42.py',ROOT/'tools/client_virtual_protection_v43.py']
    hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in sources}
    raw=v39.CLIENT.read_bytes()
    if args.resume_sha256:
        selected=OUT/'previous_resumable.zip' if args.resume_previous else path
        e=restore(raw,selected.read_bytes(),args.resume_sha256)
        if e.stop_reason['reason'] not in ('timeout','instruction_limit'):
            raise SystemExit('Unresolved boundary retained; not silently skipped.')
        if args.resume_previous and path.exists():
            current=path.read_bytes();current_sha=sha256(current).hexdigest()
            atomic(OUT/('boundary_'+current_sha+'.zip'),current)
            atomic(OUT/('boundary_'+current_sha+'.json'),(OUT/'progress.json').read_bytes())
    else:
        e=FileExperiment(raw,instruction_limit=50000000,seconds=40,module_metadata=True,
                         heap_handle=True,guest_heap=True,os_version=tuple(sys.getwindowsversion().platform_version),shared_version=True)
    checksum=install_checksum_accelerator(e)
    file_checksum=FileChecksumController(e) if args.accelerate_file_checksum else None
    history=[]
    if args.resume_sha256:
        history=json.loads((OUT/'progress.json').read_text('utf-8')).get('checkpoints',[])
    for segment in range(args.segments):
        report=e.run(resume=bool(args.resume_sha256) or segment>0)
        blob=encode(e);digest=sha256(blob).hexdigest()
        # Keep the last resumable checkpoint when a NEW unresolved boundary is
        # reached. An audited model can then replay the last segment normally;
        # there is no need to relabel a stopped unknown call as a timeout.
        if e.stop_reason['reason'] not in ('timeout','instruction_limit') and path.exists():
            prior=json.loads((OUT/'checkpoint_identity.json').read_text('utf-8'))
            prior_blob=path.read_bytes()
            if prior['stop']['reason'] in ('timeout','instruction_limit') and sha256(prior_blob).hexdigest()==prior['sha256']:
                atomic(OUT/'previous_resumable.zip',prior_blob)
                atomic(OUT/'previous_resumable_identity.json',json.dumps(prior,indent=2).encode())
        atomic(path,blob)
        info=dict(sha256=digest,size=len(blob),instructions=e.count,stop=e.stop_reason,
                  source_hashes=hashes,source_changes=[p.name for p in sources if sha256(p.read_bytes()).hexdigest()!=hashes[p.name]])
        atomic(OUT/'checkpoint_identity.json',json.dumps(info,indent=2).encode())
        item=dict(index=len(history)+1,instructions=e.count,seconds=e.total_seconds,stop=e.stop_reason,
                  modified_sections=report['modified_sections'],file_calls=len(e.file_calls),snapshot_sha256=digest)
        history.append(item)
        report.update(checkpoints=history,snapshot=info,checksum_model=checksum.report(),
                      file_checksum_model=file_checksum.report() if file_checksum else None,
                      checksum_model_scope='since_current_resume',resumed_from=args.resume_sha256)
        atomic(OUT/'progress.json',json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
        print(json.dumps(item,ensure_ascii=False),flush=True)
        if e.stop_reason['reason'] not in ('timeout','instruction_limit') or report['error'] or info['source_changes']:
            break
    if sha256(v39.CLIENT.read_bytes()).hexdigest()!=e.sha:
        raise RuntimeError('External client file changed; evidence invalidated.')
