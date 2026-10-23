"""Read only the user-specified PE file. No execution, injection or unpacking."""
from hashlib import sha256
import json
from pathlib import Path
import re
import pefile

ROOT = Path(__file__).resolve().parents[1]
CLIENT = Path('D:/DragonNest/Reborn/dragonnest_reborn.exe')


def inspect(path=CLIENT):
    raw = path.read_bytes()
    pe = pefile.PE(data=raw)
    terms = rb'(Projectile|DamageObject|HitResult|AttackResult|DamageResult|GenerateObject|CreateObject|SkillAction|RecvDamage|ActionInstance|DamageInstance)'
    strings = [(m.start(), m.group().decode('ascii')) for m in re.finditer(rb'[ -~]{7,240}', raw)
               if re.search(terms, m.group(), re.I)]
    sections = [dict(name=s.Name.decode('ascii', errors='replace').rstrip('\0'),
        rva=s.VirtualAddress, virtual_size=s.Misc_VirtualSize, raw_offset=s.PointerToRawData,
        raw_size=s.SizeOfRawData, entropy=s.get_entropy()) for s in pe.sections]
    return dict(path=str(path), sha256=sha256(raw).hexdigest(), size=len(raw),
        machine=pe.FILE_HEADER.Machine, image_base=pe.OPTIONAL_HEADER.ImageBase,
        entry_rva=pe.OPTIONAL_HEADER.AddressOfEntryPoint,
        entry_bytes=pe.get_data(pe.OPTIONAL_HEADER.AddressOfEntryPoint, 96).hex(),
        sections=sections, candidate_strings=strings,
        imports=[d.dll.decode('ascii', errors='replace') for d in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])],
        exports=[dict(name=s.name.decode('ascii', errors='replace') if s.name else None, rva=s.address)
                 for s in getattr(getattr(pe, 'DIRECTORY_ENTRY_EXPORT', None), 'symbols', [])],
        signature_offsets={s.decode(): raw.find(s) for s in
            (b'UPX!', b'UPX0', b'VMProtect', b'Themida', b'WinLicense', b'ASPack', b'PECompact', b'Enigma', b'.pdb')},
        limitation='Zero raw .text/.rdata plus high-entropy relocated storage is consistent with packing/protection; exact packer and plaintext code are not established. No game execution or memory read was attempted.',
        confirmed_new_hits=0, game_modified=False)


if __name__ == '__main__':
    result = inspect()
    output = ROOT / 'outputs/client_static_v37_20260908'
    output.mkdir(exist_ok=True)
    (output / 'pe_inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    text = ['# 游戏客户端静态检查', '',
        f"文件：{result['path']}；SHA-256：{result['sha256']}。", '',
        '主程序是64位PE。原.text/.rdata节没有磁盘原始代码，主体位于高熵.data2节，符合压缩/保护存储特征，但未确证具体保护器。',
        '静态关键词检索未找到Projectile、DamageObject、AttackResult等可用字符串；仅有两项显卡选择导出，未发现PDB路径。不能据此断言运行后的客户端没有这些结构。', '',
        '本次只读文件，没有启动游戏、注入、读取进程内存、解保护或改动客户端。没有从该文件新增确认Hit。已有证据见pe_inventory.json。', '']
    (output / 'REPORT.md').write_text('\n'.join(text), 'utf-8')
    print(json.dumps(dict(size=result['size'], machine=result['machine'], matched_strings=len(result['candidate_strings']), new_confirmed_hits=0)))
