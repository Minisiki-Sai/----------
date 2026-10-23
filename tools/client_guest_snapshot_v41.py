"""Hash-pinned, bounded snapshots of OUR offline Unicorn guest, not live game memory.

Never unpickle an object. Opaque CPU bytes use Unicorn's own context serialization
contract and are accepted only with an independently supplied whole-archive hash,
matching architecture/version/size and the same guest image and system PE hashes.
"""
from collections import Counter, deque
from hashlib import sha256
import io
import json
import zipfile

from tools.emulate_client_sections_v40 import SectionExperiment, VIEW_BASE, VIEW_STRIDE, v39

MAX_BYTES = 256*1024*1024
SCHEMA = 'offline-client-guest-v41-1'
CONFIG = ('synthetic_image_pointer','module_metadata','heap_handle','guest_heap','os_version','shared_version','enable_sections')
STATE = ('count','total_seconds','has_run','stop_reason','allocations','heap_reserved',
    'first_writes','section_handles','next_section_handle','views','section_calls','model_writes')


def integer(value, lo=0, hi=2**63-1):
    if type(value) is not int or not lo<=value<=hi:
        raise ValueError('invalid bounded integer')
    return value


def normalized(regions):
    result=[]
    for base,size,perms in sorted(regions):
        integer(base,1); integer(size,1,MAX_BYTES); integer(perms,1,7)
        if base%v39.PAGE or size%v39.PAGE or base+size>=2**63:
            raise ValueError('invalid guest region extent')
        if result and base<result[-1][0]+result[-1][1]:
            raise ValueError('overlapping guest regions')
        if result and base==result[-1][0]+result[-1][1] and perms==result[-1][2]:
            a,b,c=result.pop();result.append((a,b+size,c))
        else:
            result.append((base,size,perms))
    if sum(size for _,size,_ in result)>MAX_BYTES:
        raise ValueError('guest memory budget')
    return result


def encode_snapshot(experiment):
    if not isinstance(experiment,SectionExperiment) or not experiment.has_run or not experiment.stop_reason or experiment.count<=0:
        raise ValueError('only stopped offline experiments may be saved')
    context=experiment.uc.context_save()
    cpu=bytes(context)
    if not 0<len(cpu)<=1024*1024:
        raise ValueError('unexpected CPU context size')
    state={k:getattr(experiment,k) for k in STATE}
    state.update(writes=dict(experiment.writes),blocks=dict(experiment.blocks),tail=list(experiment.tail))
    static=[]
    for record in experiment.static_reads.values():
        static.append({k:list(v) if k=='tail' else dict(v) if k=='pages' else v for k,v in record.items() if k!='hook'})
    metadata=dict(schema=SCHEMA,client_sha256=experiment.sha,base=experiment.base,size=experiment.size,
        unicorn_version=v39.unicorn.__version__,config={k:getattr(experiment,k) for k in CONFIG},state=state,
        cpu=dict(size=len(cpu),arch=context.arch,mode=context.mode,sha256=sha256(cpu).hexdigest()),
        readonly_modules=experiment.readonly_modules,static_reads=static,regions=[])
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        archive.writestr('cpu.bin',cpu)
        total=0
        for index,(lo,hi,perms) in enumerate(experiment.uc.mem_regions()):
            size=hi-lo+1;total+=size
            if total>MAX_BYTES or index>=256:
                raise ValueError('snapshot exceeds memory/region budget')
            body=bytes(experiment.uc.mem_read(lo,size))
            name=f'memory/{index:03}.bin'
            metadata['regions'].append(dict(base=lo,size=size,perms=perms,name=name,sha256=sha256(body).hexdigest()))
            archive.writestr(name,body)
        document=json.dumps(metadata,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        if len(document)>8*1024*1024:
            raise ValueError('metadata budget')
        archive.writestr('manifest.json',document)
    blob=output.getvalue()
    if len(blob)>MAX_BYTES:
        raise ValueError('archive budget')
    return blob


def _counter(value, maximum=100000):
    if not isinstance(value,dict) or len(value)>maximum:
        raise ValueError('counter budget')
    result=Counter()
    for key,number in value.items():
        offset=int(key)
        integer(offset);integer(number,0,10**15)
        result[offset]=number
    return result


def restore_snapshot(raw,blob,expected_sha256,*,instruction_limit=50000000,seconds=40,experiment_type=None,extra_readonly_regions=()):
    if not isinstance(blob,bytes) or len(blob)>MAX_BYTES or not isinstance(expected_sha256,str) or sha256(blob).hexdigest()!=expected_sha256:
        raise ValueError('snapshot identity/size mismatch')
    if experiment_type is None:
        experiment_type=SectionExperiment
    elif not isinstance(experiment_type,type) or not issubclass(experiment_type,SectionExperiment):
        raise ValueError('snapshot experiment must retain the bounded section sandbox')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        infos=archive.infolist()
        if len(infos)>258 or len({i.filename for i in infos})!=len(infos) or sum(i.file_size for i in infos)>MAX_BYTES:
            raise ValueError('archive member/expanded-size budget')
        if any(i.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED) or i.flag_bits&1 for i in infos):
            raise ValueError('unsupported archive member')
        info=archive.getinfo('manifest.json')
        if info.file_size>8*1024*1024:
            raise ValueError('manifest budget')
        meta=json.loads(archive.read(info))
        if meta['schema']!=SCHEMA or meta['client_sha256']!=sha256(raw).hexdigest() or meta['unicorn_version']!=v39.unicorn.__version__:
            raise ValueError('snapshot image/engine/schema mismatch')
        config=meta['config']
        if set(config)!=set(CONFIG) or any(type(config[k]) is not bool for k in CONFIG if k!='os_version'):
            raise ValueError('invalid snapshot configuration')
        if config['os_version'] is not None:
            config['os_version']=tuple(config['os_version'])
        experiment=experiment_type(raw,instruction_limit=instruction_limit,seconds=seconds,**config)
        if (meta['base'],meta['size'])!=(experiment.base,experiment.size) or meta['readonly_modules']!=experiment.readonly_modules:
            raise ValueError('guest/static module identity mismatch')
        expected=[(lo,hi-lo+1,perms) for lo,hi,perms in experiment.uc.mem_regions()]
        immutable=[(lo,bytes(experiment.uc.mem_read(lo,hi-lo+1))) for lo,hi,perms in experiment.uc.mem_regions() if perms==v39.unicorn.UC_PROT_READ]
        # Optional regions are independently supplied immutable bytes, never
        # trusted archive content or writable/executable mapping permissions.
        if not isinstance(extra_readonly_regions,(list,tuple)) or len(extra_readonly_regions)>4:
            raise ValueError('extra readonly snapshot region budget')
        for base,data in extra_readonly_regions:
            if not isinstance(data,bytes) or not data or len(data)%v39.PAGE:
                raise ValueError('extra readonly snapshot region size/type')
            expected.append((base,len(data),v39.unicorn.UC_PROT_READ))
            immutable.append((base,data))
        normalized(expected)
        state=meta['state']
        integer(state['count'],1,10**13)
        if state['has_run'] is not True or not isinstance(state['stop_reason'],dict) or not isinstance(state['stop_reason'].get('reason'),str):
            raise ValueError('snapshot is not a stopped experiment')
        if type(state['total_seconds']) not in (int,float) or not 0<=state['total_seconds']<=10**8:
            raise ValueError('invalid guest elapsed time')
        allocations=state['allocations']
        if not isinstance(allocations,list) or len(allocations)>128 or (allocations and not config['guest_heap']):
            raise ValueError('heap allocation budget')
        cursor=v39.HEAP_BASE
        for item in allocations:
            size=integer(item['size'],1,32*1024*1024)
            if item['base']!=cursor or item['mapped']!=v39.align(size) or item['flags'] not in (0,8):
                raise ValueError('heap layout mismatch')
            cursor+=item['mapped']+v39.PAGE
            if cursor>v39.HEAP_BASE+v39.HEAP_BUDGET:
                raise ValueError('heap extent budget')
            spans=item['initialized']
            if len(spans)>4096:
                raise ValueError('heap initialized-span budget')
            last=-1
            for lo,hi in spans:
                integer(lo,0,size);integer(hi,1,size)
                if lo>=hi or lo<=last:
                    raise ValueError('invalid initialized spans')
                last=hi
            integer(item['writes'],0,10**15)
            item['initialized']=[tuple(span) for span in spans]
            expected.append((item['base'],item['mapped'],v39.unicorn.UC_PROT_READ|v39.unicorn.UC_PROT_WRITE))
        if state['heap_reserved']!=cursor-v39.HEAP_BASE:
            raise ValueError('heap reservation mismatch')
        modules={m['name']:m for m in experiment.readonly_modules}
        views=state['views']
        if not isinstance(views,list) or len(views)>8:
            raise ValueError('section view budget')
        for index,item in enumerate(views):
            module=modules.get(item['name'])
            if not module or item['size']!=module['size'] or item['source_sha256']!=module['sha256'] or item['base']!=VIEW_BASE+index*VIEW_STRIDE or type(item.get('unmapped',False)) is not bool:
                raise ValueError('invalid section view')
            if not item.get('unmapped'):
                expected.append((item['base'],item['size'],v39.unicorn.UC_PROT_READ))
                immutable.append((item['base'],bytes(experiment.uc.mem_read(module['base'],module['size']))))
        handles=state['section_handles']
        if not isinstance(handles,dict) or len(handles)>8:
            raise ValueError('handle budget')
        handle_cursor=integer(state['next_section_handle'],0xb000,0xb000+4*64)
        rebuilt={}
        for handle,module in handles.items():
            handle=int(handle)
            if handle%4 or not 0xb000<=handle<handle_cursor or modules.get(module['name'])!=module:
                raise ValueError('invalid section handle')
            rebuilt[handle]=module
        state['section_handles']=rebuilt
        writes=_counter(state['writes']);blocks=_counter(state['blocks'])
        if any(rva>=experiment.size or rva%v39.PAGE for rva in writes) or any(rva>=experiment.size for rva in blocks):
            raise ValueError('invalid image counter offset')
        if not isinstance(state['tail'],list) or len(state['tail'])>96:
            raise ValueError('instruction tail budget')
        for rva,width in state['tail']:
            integer(rva,0,experiment.size-1);integer(width,1,15)
            if rva+width>experiment.size:
                raise ValueError('instruction tail outside image')
        if not isinstance(state['first_writes'],list) or len(state['first_writes'])>64:
            raise ValueError('image write log budget')
        for item in state['first_writes']:
            integer(item['rva'],0,experiment.size-1);integer(item['size'],1,64)
            integer(item['instruction_rva'],0,experiment.size-1)
            if item['rva']+item['size']>experiment.size:
                raise ValueError('image write outside image')
        if not isinstance(state['section_calls'],list) or len(state['section_calls'])>65:
            raise ValueError('section call log budget')
        for item in state['section_calls']:
            if item.get('function') not in ('NtOpenSection','NtMapViewOfSection','NtUnmapViewOfSection','NtClose') or type(item.get('modelled')) is not bool:
                raise ValueError('invalid section call diagnostic')
        if not isinstance(state['model_writes'],list) or len(state['model_writes'])>130:
            raise ValueError('API write log budget')
        for item in state['model_writes']:
            integer(int(item['address'],16),1);integer(item['size'],1,4096)
        expected_static={m['base']:(m['name'],m['size'],'loader') for m in experiment.readonly_modules}
        expected_static.update({v['base']:(v['name'],v['size'],'section_view') for v in views})
        if not isinstance(meta['static_reads'],list) or len(meta['static_reads'])!=len(expected_static) or {i['base'] for i in meta['static_reads']}!=set(expected_static):
            raise ValueError('static read source set mismatch')
        for item in meta['static_reads']:
            if (item['name'],item['size'],item['kind'])!=expected_static[item['base']]:
                raise ValueError('static read source identity mismatch')
            integer(item['reads'],0,10**15);integer(item['bytes'],0,10**16)
            pages=_counter(item['pages'],65536)
            if any(page>=item['size']//v39.PAGE for page in pages) or sum(pages.values())!=item['reads']:
                raise ValueError('static read page bounds/count mismatch')
            if item['reads']:
                integer(item['minimum'],0,item['size']-1);integer(item['maximum'],1,item['size'])
                if item['minimum']>=item['maximum']:
                    raise ValueError('invalid static read extent')
            elif item['minimum'] is not None or item['maximum'] is not None or item['bytes']:
                raise ValueError('invalid empty static read extent')
            if not isinstance(item['tail'],list) or len(item['tail'])>8:
                raise ValueError('static read tail budget')
            for read in item['tail']:
                integer(read['offset'],0,item['size']-1);integer(read['size'],1,64)
                integer(read['instruction_rva'],0,experiment.size-1)
                if read['offset']+read['size']>item['size']:
                    raise ValueError('static read outside image')
        regions=meta['regions']
        if len(regions)>256 or normalized([(r['base'],r['size'],r['perms']) for r in regions])!=normalized(expected):
            raise ValueError('unexpected snapshot memory map or permissions')
        names=['manifest.json','cpu.bin']+[r['name'] for r in regions]
        if set(names)!=set(archive.namelist()) or len(set(names))!=len(names) or any(r['name']!=f'memory/{i:03}.bin' for i,r in enumerate(regions)):
            raise ValueError('unexpected snapshot members')
        fresh=experiment.uc.context_save()
        cpu=archive.read('cpu.bin')
        if meta['cpu']!=dict(size=fresh.size,arch=fresh.arch,mode=fresh.mode,sha256=sha256(cpu).hexdigest()) or len(cpu)!=fresh.size:
            raise ValueError('CPU context identity/extent mismatch')
        bodies=[]
        for region in regions:
            if archive.getinfo(region['name']).file_size!=region['size']:
                raise ValueError('memory member extent mismatch')
            body=archive.read(region['name'])
            if sha256(body).hexdigest()!=region['sha256']:
                raise ValueError('memory member digest mismatch')
            if region['perms']==v39.unicorn.UC_PROT_READ and not any(lo<=region['base'] and region['base']+region['size']<=lo+len(reference) and body==reference[region['base']-lo:region['base']-lo+region['size']] for lo,reference in immutable):
                raise ValueError('immutable memory differs from pinned source')
            bodies.append(body)
    # All archive validation completed before changing guest memory.
    for lo,hi,perms in list(experiment.uc.mem_regions()):
        experiment.uc.mem_unmap(lo,hi-lo+1)
    for region,body in zip(regions,bodies):
        experiment.uc.mem_map(region['base'],region['size'],region['perms'])
        experiment.uc.mem_write(region['base'],body)
    for key in STATE:
        setattr(experiment,key,state[key])
    experiment.writes=writes
    experiment.blocks=blocks
    experiment.tail=deque((tuple(x) for x in state['tail']),maxlen=96)
    for view in views:
        if not view.get('unmapped'):
            experiment.watch_static_image(view['base'],view['size'],view['name'],'section_view')
    for item in meta['static_reads']:
        existing=experiment.static_reads.get(item['base'])
        if existing is None:
            view=next((v for v in views if v['base']==item['base'] and v.get('unmapped')),None)
            if view is None:
                raise ValueError('unknown static read source')
            existing={};experiment.static_reads[item['base']]=existing
        existing.update({k:v for k,v in item.items() if k not in ('pages','tail','hook')})
        existing['pages']=_counter(item['pages'],65536)
        existing['tail']=deque(item['tail'],maxlen=8)
    # Use Unicorn's bytes-context contract, not pickle or host-process context.
    restored=type(fresh).__new__(type(fresh))
    restored.__setstate__((cpu,fresh.size,fresh.arch,fresh.mode))
    experiment.uc.context_restore(restored)
    experiment.snapshot_sha256=expected_sha256
    return experiment
