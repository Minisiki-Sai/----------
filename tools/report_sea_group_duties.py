"""Render local split-party A/B evidence; does not upload or alter captures."""
import html
import json
from pathlib import Path


ROOT = Path('outputs/verification/sea_group_duties_20261018')
NAMES = ('20261018冰灵海龙第一把', '1018海龙游侠第三把', '1018海龙游侠第六把')


def esc(value):
    return html.escape(str(value))


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(
        '<th>'+esc(v)+'</th>' for v in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows
    ) + '</tbody></table></div>'


def main():
    sections = []
    for name in NAMES:
        before = json.loads((ROOT/(name+'.comparison.json')).read_text(encoding='utf-8'))
        after = json.loads((ROOT/'after_cohort_fix'/(name+'.comparison.json')).read_text(encoding='utf-8'))
        old = {p['source_id']:p for p in before['players']}
        duties, outputs = [], []
        for p in after['players']:
            b = old[p['source_id']]
            outputs.append([p['name'], p['job'], f"{b['output_score']:.2f} → {p['output_score']:.2f}",
                            f"{b['support_score']:.2f} → {p['support_score']:.2f}"])
            if 'after' in p:
                duties.append([p['name'], p['job'], f"{b['before']['score']:.2f}",
                               f"{p['after']['score']:.2f}",
                               f"{b['before']['contribution_40']:.2f} → {p['after']['contribution_40']:.2f}"])
        print(name, 'duties', duties, 'scores', outputs)
        sections.append('<section><h2>'+esc(name)+'</h2><p>有连续攻击证据支持的分组时长：'
                        +f"{after['split_seconds']:.0f} 秒。未确认的时段不擅自豁免职责。</p><h3>T／奶职业特色</h3>"
                        +table(['角色','职业','修改前（100分制）','修改后（100分制）','折合40分贡献：前 → 后'],duties)
                        +'<h3>全队输出分与辅助分</h3>'
                        +table(['角色','职业','输出分：前 → 后','辅助分：前 → 后'],outputs)+'</section>')
    source = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>1018 海龙分组职责与评分核对</title>
<style>body{font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;background:#f1f6f7;color:#20383e;margin:0}
main{max-width:1200px;margin:32px auto;padding:0 22px}section,.intro{background:white;border:1px solid #d4e2e4;border-radius:12px;padding:24px;margin:20px 0}
h1{font-size:28px}h2{font-size:22px;color:#06767b}h3{font-size:18px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:10px 13px;border-bottom:1px solid #dae5e7;text-align:left;white-space:nowrap}th{background:#e7f3f3}tr:nth-child(even){background:#f7fafb}.note{background:#fff8e8;padding:16px;border-left:4px solid #dba946}li{margin:8px 0}</style>
<main><h1>1018 海龙：分组职责与评分核对</h1><div class="intro">
<p>本地源码已修改；本报告未发布安装包、未上传或覆盖云端评分。三个原包均完成本地全队重算。</p>
<p>比较口径：修改前后均包含前一步的 05 血量同步、机制扣血及未知回复处理。本次前后差异是分组职责范围与错误 Boss 合组修正，不代表历史 v4.83 与新版的所有差异。</p>
<ol><li>狮蝎与天启分组不是固定四人。按连续攻击目标证据逐段确定参与者；同组没覆盖仍会扣分，未知成员不因缺少 Buff 而自动豁免。</li>
<li>挑衅按负责目标；蛋壳的实战与理论使用相同的可覆盖队友范围。奶只分担可负责队友的未解决需求与救急机会，真实跨组治疗贡献仍保留。</li>
<li>冰灵第一把有两条持续召唤物“召唤魔法草”释放记录跨过两个 Boss，旧逻辑据此把整段狮蝎／天启永久合组。现在明确分离这类已核实的分组机制，避免理论错误使用另一组的辅助增益。</li></ol>
<p class="note">用户已确认：地狱犬另一边打的是召唤牛头怪，不是另一个 Boss。已加入“地狱犬模板 4237012／牛头怪模板 4237036”的分组规则。冰灵第一把的 0x00000017 仍缺模板记录，本次自动计算不硬编码该实体 ID，也不伪造为 Boss；因此这部分旧包的分组尚未完全还原。报告数字不是“所有分组问题已彻底解决”的结论。</p></div>'''
    source += ''.join(sections)
    source += '''<section><h2>为什么还有低分</h2>
<p>确认有算法问题，但不是所有低分都来自同一个错误。第三、六把没有第一把的跨 Boss 合组问题，因此输出分、辅助分在本次重算中保持不变；T／奶的职责分仍会改变。</p>
<p>例如修正后的冰灵第一把，冰灵冰刺记录为 48 次／理论 50 次，伤害完成率约 63.68%；风行者双飞为 13 次／理论 54 次。释放次数、单次 Hit 与倍率完成率仍会影响输出分；这些是当前解析结果，不能据此断言玩家操作差，也不能保证没有其他漏识别。</p>
<p>加速技能还按相同“移除该加速”的基线比较真实时机与理论时机，并不是伤害覆盖率的简单比值。已检查改时机重排保留分组有效区间；未为消除低分而改权重或抬高得分。剩余差异仍需按技能／释放逐条验证。</p>
<h2>验证与边界</h2><p>本次相关回归测试 345 项通过，覆盖分组、挑衅、蛋壳、治疗与救急、血量解析、辅助分组、加速反事实和 V2 理论轴。</p><ul><li>分组算法只在评分阶段运行，不加入逐包解码、悬浮窗或语音实时通道。</li>
<li>采用 10 秒证据分段，至少相邻两段一致、每边至少两名有稳定目标证据的队友；单次转火不直接作为分组。它不是精确距离检测，也不覆盖单人独立负责、缺少攻击记录等所有情况。</li>
<li>两轮重算缓存状态不同，不能用总耗时数字推断优化比例。这里不宣称本次修改加快评分。</li></ul></section></main></html>'''
    target = ROOT/'1018海龙分组职责与评分对比.html'
    target.write_text(source, encoding='utf-8')
    print(target.resolve())


if __name__ == '__main__':
    main()
