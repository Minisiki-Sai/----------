"""Classify already-observed guest reads against hash-pinned static PE exports."""
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.emulate_client_entry_v39 import checked_image


def review(report):
    result = []
    for state in report['static_reads']:
        if state['kind'] != 'section_view':
            continue
        module = next(m for m in report['read_only_system_export_tables'] if m['name']==state['name'])
        # Strict allowlist; never follow a guest- or report-supplied file path.
        if module['name'] not in ('ntdll.dll','kernel32.dll','kernelbase.dll'):
            raise ValueError('unexpected static module')
        raw = (Path('C:/Windows/System32')/module['name']).read_bytes()
        if sha256(raw).hexdigest() != module['sha256']:
            raise ValueError('system image changed')
        pe, image = checked_image(raw, require_entry=False)
        table = pe.DIRECTORY_ENTRY_EXPORT.struct
        arrays = [('function_rva', table.AddressOfFunctions, table.NumberOfFunctions, 4),
            ('name_rva', table.AddressOfNames, table.NumberOfNames, 4),
            ('name_ordinal', table.AddressOfNameOrdinals, table.NumberOfNames, 2)]
        names = []
        for index in range(table.NumberOfNames):
            offset = struct.unpack_from('<I', image, table.AddressOfNames+index*4)[0]
            end = image.find(b'\0', offset, min(len(image),offset+512))
            if end == -1:
                raise ValueError('unbounded export name')
            names.append((offset,end+1,index,image[offset:end].decode('ascii')))
        tail = []
        for read in state['tail']:
            offset, width = read['offset'], read['size']
            item = dict(read, category='other_static_image_data')
            for name, start, count, stride in arrays:
                if start <= offset and offset+width <= start+count*stride:
                    item.update(category=name,index=(offset-start)//stride,count=count)
                    break
            else:
                for start,end,index,name in names:
                    if start <= offset and offset+width <= end:
                        item.update(category='export_name', name=name, index=index, count=table.NumberOfNames)
                        break
            tail.append(item)
        result.append(dict(module=module['name'],reads=state['reads'],export_names=table.NumberOfNames,tail=tail))
    return dict(instructions=report['instructions'],stop=report['stop'],views=result,
        interpretation='Observed export-read progress, not proof of resource decoding or skill attribution.')


if __name__ == '__main__':
    out = ROOT/'outputs/client_protection_v40_20260908'
    source = out/'section_emulation_tracked.json'
    data = source.read_bytes()
    result = review(json.loads(data))
    result['checkpoint_sha256'] = sha256(data).hexdigest()
    (out/'section_progress.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
