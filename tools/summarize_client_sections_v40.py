"""Report bounded offline observations separately from runtime attribution gains."""
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/client_protection_v40_20260908'


if __name__ == '__main__':
    report = json.loads((OUT/'section_emulation_tracked.json').read_text('utf-8'))
    tests = json.loads((OUT/'test_run.json').read_text('utf-8'))
    progress = json.loads((OUT/'section_progress.json').read_text('utf-8'))
    checksum = json.loads((OUT/'checksum_review.json').read_text('utf-8'))
    if not tests['successful']:
        raise SystemExit('Refusing final report: diagnostic regression tests still fail.')
    client = Path('D:/DragonNest/Reborn/dragonnest_reborn.exe')
    client_unchanged = sha256(client.read_bytes()).hexdigest()==report['client_sha256']
    source = OUT/'emulator_source_0835ab23837c.py'
    pinned = source.exists() and sha256(source.read_bytes()).hexdigest()==report['source_hashes']['emulate_client_sections_v40.py']
    result = dict(stage='bounded offline protection initialization, not completed unpacking',
        client_unchanged=client_unchanged, observed_run_source_frozen=pinned,
        observed_run_source_hashes=report['source_hashes'], observed_sources_changed_during_run=report['sources_changed'],
        tests=tests, instructions=report['instructions'], seconds=report['total_seconds'],
        checksum_review=checksum,
        stop=report['stop'], modified_sections=report['modified_sections'],section_calls=report['section_calls'],
        new_confirmed_hits=0, last_full_replay=dict(passed_jobs=19,total_jobs=30,
            named_hits=941271,confirmed_hits=904376,inferred_hits=36895,unknown_hits=4536),
        safety=report['safety'],source_note='Tests cover the later stricter ASCII/symbol checks. The long trace used its separately hash-pinned pre-hardening source; no full replay of the later source is claimed.')
    if not client_unchanged or not pinned or report['sources_changed']:
        raise SystemExit('Client/source identity needs review before reporting.')
    if any(s['name']!='.data1' for s in report['modified_sections']):
        raise SystemExit('New image writes need manual review; old stage conclusion no longer applies.')
    (OUT/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    stop=json.dumps(report['stop'],ensure_ascii=False)
    calls='\n'.join(f"- `{c['function']}`：{'已由限定离线模型处理' if c['modelled'] else '仍未处理'}。" for c in report['section_calls'])
    text=f'''# 客户端封装解析 v40：越过映射边界，尚未完成解包

可以继续解析；v39 停在未实现的外部接口，不是证明保护封装无法解开。

## 本轮实测

本轮离线运行 {report['instructions']:,} 条指令、{report['total_seconds']:.3f} 秒。实际请求打开的对象为 `\\KnownDlls\\ntdll.dll`，不是抓包、账号数据或网络连接。

{calls}

读取的是已核对哈希的系统 DLL 文件映像，映射只存在于 Unicorn 隔离内存中。未调用真实 NtOpenSection / NtMapViewOfSection，未执行系统 DLL 导出函数。

已观察到新映射的函数名表、序号表和函数地址表访问，随后正常解除映射并关闭句柄；这次导出表遍历已经结束，不能把它当作死循环。

后续发现分段逐字节校验循环。独立复算两个同指令位置检查点之间的1,087,130字节，所得状态与客体寄存器相同，且剩余长度的变化一致。这验证了该段递推过程，未跳过、改写或强行通过校验。它仍不是资源解密结果。

最终停止状态：`{stop}`。

截至本次停止，游戏原始 `.text` / `.rdata` 没有被还原；`.data1` 仍仅写入6个函数指针，共48字节。尚未得到新的资源密钥、技能对象关联表或伤害处理代码。

## 回归与隔离

诊断测试 {tests['tests']} 项通过。新增测试包括名称/参数限制、未初始化读取、句柄与映射生命周期、只读/不可执行、拒绝后不修改客体状态、返回槽及输出槽重叠等。

测试曾发现并修复特殊 Unicode 字符扩大路径白名单、不同函数名歧义分派两类问题。这次长运行使用的修复前来源已另行冻结保存；实际记录为规范 ASCII 路径及相同接口 Nt/Zw 别名，未触发这两类情况。这里不把后续版本的单元测试称为完整客户端重新回放。

游戏文件哈希未变。未启动或注入游戏，未读取实时游戏进程内存，未改抓包/ACT正式代码，未上传或打包。

## 与95%目标的关系

**本轮新增确认 Hit 为0。最近完整回放仍为19/30个二转职业达标，全职业95%尚未完成。**

接下来应跟踪分段校验后的控制流，继续还原资源加载及对象创建/伤害结果处理路径；只有关联在原始抓包中核实后才计入覆盖率。不以延长窗口、总平均或推断改标签补足比例。

接口语义参考 [Microsoft OpenSection](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-zwopensection) 与 [MapViewOfSection](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-zwmapviewofsection)。这些资料不包含本客户端的解包算法。
'''
    (OUT/'REPORT.md').write_text(text,'utf-8')
    print(json.dumps({k:result[k] for k in ('client_unchanged','instructions','seconds','stop','new_confirmed_hits')},ensure_ascii=False))
