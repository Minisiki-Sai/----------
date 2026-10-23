"""Consolidate bounded v39 experiments; never promote hypotheses to Hits."""
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/client_protection_v39_20260908'


def read(path):
    return json.loads(path.read_text('utf-8'))


def main():
    baseline = read(ROOT/'outputs/magma_runtime_v37_20260908/acceptance.json')
    static = read(OUT/'static_review.json')
    wrapper = read(ROOT/'outputs/pak_wrapper_v39_20260908/probe_review.json')
    emulation = read(OUT/'entry_emulation_shared_version.json')
    frozen = read(ROOT/'outputs/magma_runtime_v37_20260908/after/frozen_manifest.json')
    compared = {key: value for key, value in frozen.items()
                if key.startswith('dragon_nest_builder/live_dps/') and key.endswith('.py')}
    different = [key for key, value in compared.items() if sha256((ROOT/key).read_bytes()).hexdigest() != value]
    sources = ['tools/inspect_client_protection_v39.py', 'tools/emulate_client_entry_v39.py',
               'tools/probe_pak_wrapper_v39.py', 'tools/summarize_client_protection_v39.py',
               'tools/run_client_probe_tests_v39.py', 'tests/test_client_entry_v39.py',
               'tests/test_client_guest_heap_v39.py', 'tests/test_pak_wrapper_v39.py']
    tests = ET.parse(OUT/'tests.xml').getroot()
    suites = [tests] if tests.tag == 'testsuite' else list(tests.iter('testsuite'))
    counts = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if counts['failures'] or counts['errors']:
        raise ValueError('unresolved test failures')
    if emulation['script_changed_during_run'] or emulation['script_sha256'] != sha256((ROOT/'tools/emulate_client_entry_v39.py').read_bytes()).hexdigest():
        raise ValueError('emulator result not from current source snapshot')
    result = dict(
        objective_complete=False, new_confirmed_hits=0,
        last_full_replay=dict(passed_jobs=baseline['passed_jobs'], total_jobs=30,
            fixed_capture_count=baseline['fixed_coverage_captures'], pooled=baseline['totals']['after'],
            scope='Last full v37 replay, not a new v39 replay or independent true-accuracy estimate.'),
        client_sha256=static['current_pe']['sha256'],
        old_dump_usable_complete_code=False,
        resource_wrapper=wrapper['summary'],
        emulator={key: emulation[key] for key in ('instructions', 'total_seconds', 'stop', 'modified_sections',
            'resolved_external_symbols', 'guest_allocations', 'safety', 'script_sha256', 'checkpoints')},
        tests=counts,
        test_run=read(OUT/'test_run.json'),
        source_hashes={key: sha256((ROOT/key).read_bytes()).hexdigest() for key in sources},
        production_comparison=dict(compared=len(compared), differences_from_last_frozen_replay=different,
            note='Concurrent scoring/healing edits were preserved. This v39 work edited only diagnostics and their tests, not production. No fresh full replay was run because no attribution candidate was produced.'),
        not_done=['Original executable code/data sections not unpacked.',
            'No validated decoder for the 44 sampled wrapped PAK bodies.',
            'No new independently validated object-to-skill relationship.',
            'Eleven jobs remain below the target in the last full replay.'])
    (OUT/'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    lines = ['# 客户端保护封装解析 v39：阶段结果', '',
        '**全职业≥95%的目标尚未完成。** 本轮新增确认 Hit 为0；最近完整回放验收仍为19/30职业达标。', '',
        '固定35份抓包的总体确认覆盖率为96.0803%，不能用总体平均代替每个职业达标，也不是独立真值标注的准确率。', '',
        '## 本轮实际完成的检查', '',
        '- 静态核对客户端：原始 .text/.rdata 等区段在磁盘上的原始数据长度为0，入口位于封装区。仅凭区段形态不能认定具体保护产品。',
        '- 检查一份已有旧崩溃文件的主模块元数据：版本不同，缺少完整代码段和主模块头，不能当作当前客户端解包结果。未提取聊天、账号等运行数据。',
        f"- 离线指令模拟跟踪了 {emulation['instructions']:,} 条指令；累计 {emulation['total_seconds']:.3f} 秒。没有启动游戏进程、注入、读取实时游戏内存或调用真实游戏/系统DLL函数。",
        '- 模拟器只显式提供映像/模块表、当前系统版本等已注明的环境数据；仅在隔离内存中模拟有界堆分配，未初始化读取、未知环境字段及其他外部功能都会截停。',
        '- 解析出6个外部入口位置：GetModuleHandleA、LoadLibraryA、GetProcAddress、RtlAllocateHeap、HeapFree、ExitProcess/FatalExit。它们是解包初始化线索，不是技能对应表。',
        '- 44个资源正文均未通过完整解码校验。41个样本符合“旧正文zlib一级压缩长度 + 16字节开销及块对齐”的长度关系，但不足以证明具体密码算法、密钥或新旧正文相同。', '',
        '## 本次停止位置与证据边界', '',
        '`'+json.dumps(emulation['stop'], ensure_ascii=False)+'`', '',
        '本次明确停在NtOpenSection/ZwOpenSection调用边界：程序请求打开Windows内存映射区。没有调用该真实系统接口，也没有虚构成功返回值；这不意味着该接口本身就是资源解密函数。', '',
        '截至停止点，只有封装区 .data1 的6个函数指针被写入，原始 .text/.rdata 未还原。因此没有把任何新假设加入正式伤害归因，也没有重命名推断结果冒充确认命中。', '',
        f"隔离及资源校验测试：{counts['tests']}项，失败{counts['failures']}，错误{counts['errors']}，跳过{counts['skipped']}。测试使用人工PE、虚构DLL和内存中的压缩样本，不启动游戏。", '',
        '首轮集成测试发现拒绝执行后的重复调用可复用错误缓存，已修复；标准unittest复测通过。首轮pytest还打印了原生访问异常诊断，但继续完成了全部测试，并未因该日志退出；其日志原因尚未单独确认。这个研究用模拟器及依赖没有被加入ACT运行时。', '',
        '## 保留现状', '',
        '本轮只新增诊断脚本、测试和报告，未改游戏文件、正式归因规则或抓包文件，未打包、未上传评分。',
        '与上次冻结快照相比，工作区另有以下并行任务修改，均保留；不能把本轮报告说成当前整个工作区重新完成了一次完整回放：', '',
        *['- `'+key+'`' for key in different], '',
        '## 仍需完成', '',
        '需要先得到经过验证的资源加载/解包路径，再从还原的对象创建与伤害结果处理代码中寻找“对象→技能”的关联；这些关联还必须在原始抓包中逐条核对，才可计入各职业确认覆盖率。仅完成脱壳本身并不能保证抓包里缺失的关联可以全部恢复。', '',
        f"历史完整回放的逐职业数据见 [v37验收表](<{(ROOT/'outputs/magma_runtime_v37_20260908/REPORT.md').as_posix()}>)；资源样本检查见 [资源封装报告](<{(ROOT/'outputs/pak_wrapper_v39_20260908/REPORT.md').as_posix()}>).", '',
        '模拟环境的接口依据为 [Unicorn官方文档](https://www.unicorn-engine.org/docs/tutorial.html)；模块元数据结构参考 [Microsoft PEB_LDR_DATA](https://learn.microsoft.com/en-us/windows/win32/api/winternl/ns-winternl-peb_ldr_data)。这些资料不包含本客户端的解包算法。', '']
    (OUT/'REPORT.md').write_text('\n'.join(lines), 'utf-8')
    print(json.dumps(dict(passed_jobs=baseline['passed_jobs'], new_confirmed_hits=0,
        objective_complete=False, tests=counts, stop=emulation['stop']), ensure_ascii=False))


if __name__ == '__main__':
    main()
