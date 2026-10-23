"""Summarize verified offline evidence without relabeling coverage."""
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/native_wire_v47_20260909'


def read(path):return json.loads(path.read_text('utf-8'))
def link(title,path):return f'[{title}](<{path.as_posix()}>)'


def main():
    corpus=read(OUT/'corpus_summary.json')
    assert corpus['successful']==35 and corpus['all_physical_fields_unchanged'] and not corpus['failed']
    inputs={}
    for item in corpus['reports']:
        path=Path(item['report']);raw=path.read_bytes()
        assert sha256(raw).hexdigest()==item['report_sha256']
        report=json.loads(raw)
        for source,digest in report['source_sha256'].items():
            assert sha256(Path(source).read_bytes()).hexdigest()==digest
            inputs.setdefault(source,set()).add(digest)
    assert all(len(v)==1 for v in inputs.values())
    tests=read(OUT/'regression.json');assert tests['successful'] and not tests['changed_sources']
    suites=ET.parse(OUT/'regression.xml').getroot()
    assert all(int(s.get('errors',0))==int(s.get('failures',0))==int(s.get('skipped',0))==0 for s in suites.iter('testsuite'))
    baseline_path=Path(corpus['certification_baseline']['path'])
    assert sha256(baseline_path.read_bytes()).hexdigest()==corpus['certification_baseline']['sha256']
    baseline=read(baseline_path);counts=corpus['counts']
    lines=[
        '# 客户端网络读取与轻量状态复现：本轮结果',
        '',
        '日期：2026-09-09。这是离线研究/验收版本 v47/v48，不是 ACT 发布版本号。',
        '',
        '## 结论',
        '',
        '**轻量读取器和状态模型已经实现并完成35份固定抓包的原始字段复验；全职业95%目标仍未完成。**',
        '本轮没有将新候选升级为正式确认 Hit，没有改动生产解析、伤害统计、评分、悬浮窗、上传或版本。历史正式验收仍为19/30个二转职业达到95%；不把总体覆盖率、原始字段一致率或“有对象编号”当成逐职业技能确认率。',
        '',
        '## 已完成的复现',
        '',
        '- 按独立记录边界读取来源类型/ID、目标ID、32位索引与64位UID两个命名空间、带符号伤害、结果标志；不按固定长度套同一偏移。',
        '- 按接收实体类型区分通用投射物流与召唤实体的紧凑动作/signal流；不把所有kind9都解释成同一种结构。',
        '- 有界地保存连接、实体代际、对象注册、父对象快照；区分同值刷新、旧重传和真正ID复用。场景切换、连接淘汰、时间回退会撤销相应证据。',
        '- 只使用在当前记录之前已获得的事实；动作适配器没有给出可靠偏移时，事实延迟到该消息结束后才可使用。',
        '- 保存未知后缀和失败原因，不补零、不按相邻技能猜编号、不靠复制伤害凑覆盖率。',
        '说明：这些是保守的离线状态管理规则，并非已重现客户端全部运行状态。角色类型/出生/归属仍部分依赖现有结构化适配器；对象存活上界及容量限制是分析保护，不冒充游戏内精确生命周期。',
        '',
        '## 真实抓包验证',
        '',
        f'- 固定35份抓包，{counts["physical_frames"]:,}个物理帧；当前35份结果均对应同一组读取器源码哈希，失败0。运行中依赖发生变化的中间结果已重新计算。',
        f'- 对比{counts["compared_physical_damage_records"]:,}条完整分帧的旧direct37/41记录：来源ID、目标ID、伤害原始位值、暴击位和对象引用差异均为0。符合旧数值门槛的伤害和也完全一致。',
        f'- 共读取{counts["native_registration_prefixes"]:,}条服务端注册前缀；相较旧候选登记路径另见{corpus["candidate_counts"]["additional_native_registration_prefixes"]:,}条观察，并有{corpus["candidate_counts"]["physical_joins_to_additional_prefixes"]:,}次原始伤害引用连接。**这些不是新增确认技能/Hit，也不计入95%。**',
        '- 3份追加变长记录调查共3,223条，首个带符号数值为正的有1,409条；全部在给定接收类假设下有兼容读取方案。其中body107出现来源kind2，不能走普通玩家实体查询。',
        '- 例如0821硬核04的945条body111，分别353正、588零、4负；0905黑龙经典冰灵的1,605条分别617正、988零。第二个i64才是现有HP路径读取的位置。不能因body111有正值就认作“漏玩家输出”。',
        '- 联合测试的完整数量和结果以 '+link('regression.log',OUT/'regression.log')+' 为准；包含已有归因回归、真实字节样本、状态负面控制和新读取器测试，失败/错误/跳过均为0。独立审查的R1–R9反例已修复。',
        '',
        '**验证边界：**这是原始字段对照，不是全量伤害接纳、技能构成、评分或所有UDP包的正确性证明。未完整分帧及方向未知的消息明确留在范围外；本轮没有重跑评分。',
        '',
        '## 重要新发现与尚未闭合的证据',
        '',
        '1. 后缀中的两个短整数会进入Player的+1344/+1348，随后进入CDnComboDlg和计时/到期逻辑。证据支持“连击计数＋超时状态”，不是可用的技能编号或召唤物攻击编号。来源为小ID时，它还可能更新关联玩家，不能据此认定召唤物自己的动作。',
        '2. ACT动作数组按实际加载的文件列表顺序拼接，各文件版本、加载成功情况及原始signal序号都会影响索引。旧资源中找到同编号，不足以证明当前角色加载了完全相同的动作表。',
        '3. 部分实例已有索引但没有独占技能模板，或一个默认实例会被多个实体攻击动作复用；必须补全实例/实体自身动作，而不能统一归为最后一次玩家施放。',
        '4. 当前PAK正文路径的AES-256 ECB和zlib-wrapped DEFLATE已有静态指令依据；selector不是密钥本身，selector对应材料和标识初始化仍未闭合。选定的12项资源尚未获得可校验明文，不能宣称当前资源表已全部解开。最新静态边界见PAK报告。',
        '5. 混合感染的一个真实窗口已连接到对象104453，再连接到两个目标的伤害。已有动作门槛正控接受2条，无动作/错误职业技能/错误来源/过旧动作负控拒绝；但它仍是候选桥接验证，不是正式覆盖率新增2。',
        '',
        '## 30职业目标核对',
        '',
        '下表引用上一轮固定35包正式验收，**不是本轮新算出的技能覆盖率**。口径是已有真实命中证据、已有技能名的直接Hit中，确认归因的比例；无名伤害和缺少真实命中证据的更宽候选仍单独保留，不能从此表推出全伤害95%。',
        '',
        '|职业|最近正式确认覆盖率|达到95%|',
        '|---|---:|---|',
    ]
    for job in baseline['jobs']:
        lines.append(f'|{job["job"]}|{job["after"]["named_coverage_percent"]:.4f}%|{"是" if job["named_target_met"] else "否"}|')
    lines.extend([
        '',
        '目标剩余11职业：'+ '、'.join(j['job'] for j in baseline['jobs'] if not j['named_target_met'])+'。',
        '',
        '## 后续可执行的最短路径',
        '',
        '先补当前ACT资源/实体自身动作的可靠绑定，再把有完整前因的候选接入现有归因门槛；之后用同一固定语料重新做接纳后事件的逐项对照，按30职业分别验收。缺少运行时初始状态或资源材料的分支继续标为未知，不能通过放宽门槛、改变分母或删除困难样本达标。',
        '',
        '本轮没有后台评分或云端上传任务。以下文件可用于继续复现和审计：',
        '',
        '- '+link('35份原始字段复验汇总',OUT/'corpus_summary.json'),
        '- '+link('独立状态审查',OUT/'state_review.md'),
        '- '+link('ACT加载编号空间',OUT/'action_namespace.md'),
        '- '+link('后缀条件读取',OUT/'damage_suffix.md'),
        '- '+link('连击字段的真实消费者',OUT/'actor_pair.md'),
        '- '+link('PAK当前读取链与缺口',OUT/'pak_trace.md'),
        '- '+link('候选与历史接纳事件交叉核对（仅45个保存样例）',OUT/'candidate_existing_events.json'),
        '- '+link('联合测试及源码哈希',OUT/'regression.json'),
        '- '+link('历史正式覆盖率验收',baseline_path),
        '',
        '研究脚本：'+link('状态模型',ROOT/'tools/native_wire_state_v47.py')+'；'+link('流式复验',ROOT/'tools/replay_native_wire_state_v47.py')+'；'+link('固定语料入口',ROOT/'tools/run_native_corpus_v47.py')+'。',
    ])
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Verified35 reports; wrote',OUT/'REPORT.md')


if __name__=='__main__':main()
