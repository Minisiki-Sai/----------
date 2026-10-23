"""Bounded raw pillar life: user observation is a hypothesis, not a proof.

Reuse exact old birth anchors, inspect five seconds AFTER birth instead of
stopping at the first hit. No replay, promotion, or production mutation.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.probe_magma_exact_route_v28 import record_chains, xor
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records


def probe(sample):
    birth = sample['anchors']['birth']
    entity, owner = birth['entity'], sample['owner_fields']['owner']
    start = min(a['anchor']['file_offset'] for a in sample['prior_actions'])
    start_frame = min(a['anchor']['frame'] for a in sample['prior_actions'])
    rows, unknown, counts = [], [], Counter()
    with Path(sample['capture']).open('rb') as stream:
        stream.seek(start)
        for number in range(start_frame, start_frame + 5000):
            offset = stream.tell()
            header = stream.read(17)
            if not header:
                break
            timestamp, _, original, size = struct.unpack('<dBII', header)
            if timestamp > birth['time'] + 5:
                break
            raw = stream.read(size)
            packet = p.decode_captured_frame(timestamp, raw, original)
            counts['physical_frames'] += 1
            if packet is None or packet.transport != 'UDP':
                continue
            for mi, wire in enumerate(p.split_v576_payload(packet.payload)):
                anchor = dict(frame=number, file_offset=offset, message=mi,
                    time=timestamp, age=timestamp-birth['time'],
                    direction=packet.inferred_direction(), wire=wire.hex(),
                    wire_sha256=sha256(wire).hexdigest())
                if len(wire) == 88 and wire[5:9] == bytes.fromhex('06275500'):
                    body = xor(wire, 5)
                    if int.from_bytes(body[10:14], 'little') == entity:
                        rows.append(dict(anchor, kind='spawn88', plaintext=body.hex()))
                    continue
                chains = record_chains(wire)
                if len(chains) != 1:
                    counts['not_unique_full_chain'] += 1
                    unknown.append(dict(anchor, candidates=len(chains)))
                    continue
                chain = chains[0]
                selected = [r for r in chain['records'] if r['actor'] in (entity, owner)]
                inv = inspect_udp_damage_records(wire, packet.inferred_direction())
                hits = []
                if inv.complete:
                    for hit in inv.damage_records:
                        if hit.source_actor_id == entity or hit.target_actor_id == entity:
                            hits.append(dict(family=hit.family, source=hit.source_actor_id,
                                target=hit.target_actor_id, amount=hit.damage,
                                critical=hit.critical, group=hit.damage_result_group_id,
                                admissible_fields=hit.admissible_fields,
                                object=hit.damage_object_id, marker=hit.record_offset))
                if selected or hits:
                    rows.append(dict(anchor, kind='complete_chain', phase=chain['phase'],
                        records=selected, hits=hits))
    return dict(capture=sample['capture'], entity=entity, owner=owner,
        scope='bounded raw window; NOT new confirmed hits', counts=dict(counts),
        rows=rows, unparsed=unknown)


def summarize(result):
    """Report observations, without turning a candidate 1b02 into a death."""
    outgoing = [dict(age=row['age'], **hit) for row in result['rows']
                for hit in row.get('hits', [])
                if hit['source'] == result['entity'] and hit['admissible_fields']
                and hit['family'] == 'direct37']
    end_candidates = [row['age'] for row in result['rows']
        for record in row.get('records', [])
        if record['actor'] == result['entity'] and record['type'] == 0x1b
        and record['length'] == 2 and bytes.fromhex(record['record'])[7:] == b'\0\0']
    return dict(capture=result['capture'], entity=result['entity'],
        raw_outgoing_hits=outgoing, candidate_end_1b02_ages=end_candidates,
        unresolved_frames=result['counts'].get('not_unique_full_chain', 0),
        new_confirmed_hits=0, runtime_changed=False,
        limitation='The two damage stages are observations; 1b02 is only a candidate '
                   'end signal. No creator selector is established. No promotion.')


def main():
    source=ROOT/'outputs/magma_exact_route_v28_20260908/dynamic_evidence.json'
    samples=json.loads(source.read_text('utf-8'))['samples']
    results=[probe(sample) for sample in samples]
    out=ROOT/'outputs/magma_burst_lifecycle_v34_20260908'
    out.mkdir(exist_ok=True)
    (out/'evidence.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=[summarize(result) for result in results]
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 岩浆墙短寿命柱子：原始记录复核','',
        '用户提供的技能表现用于提出验证方向，不作为技能归属证书。此次不改生产解析、不新增确认Hit。','',
        '|抓包|柱子实体|首个直接伤害（出生后）|第二个直接伤害|1B02候选结束记录|',
        '|---|---:|---:|---:|---:|']
    for row in summary:
        ages=[h['age'] for h in row['raw_outgoing_hits']]
        values=[f'{v:.4f}s' for v in ages[:2]]
        values += ['未观察到']*(2-len(values))
        last=', '.join(f'{v:.4f}s' for v in row['candidate_end_1b02_ages']) or '未观察到'
        lines.append(f"|{Path(row['capture']).name}|{row['entity']}|{values[0]}|{values[1]}|{last}|")
    lines+=['','## 能确认与不能确认的边界','',
        '- 两个样例均先有出生/完整重复主人记录，然后同一柱子对同一Boss产生两条完整direct37伤害，第二条金额更大。完整原文、方向、偏移和哈希保存在evidence.json。',
        '- 四条伤害的对象字段均为FFFFFFFF，不能把这个哨兵值当作一个真实Projectile对象去关联。需要走实体自身伤害链。',
        '- 静态academic_magmawall_ex.act的Summon_On有1～20帧与70～83帧的typed4伤害段，与两段伤害现象相符；这仅是结构印证，不靠延迟反推准确ACT动作或技能。',
        '- 约2秒出现同一实体的1B02/0000。此处只标为结束候选，未把它擅自加入实体删除规则；两段窗口仍有未支持/不唯一的完整消息链，不能宣称所有内容已解密。',
        '- 正常EX根已独立解析，但具体子实体的创建选择键/静态Clone入口仍未闭合，暂不升为确认；不能为了95%指标直接把32022整模板强制归类。',
        '- 当前已验收确认覆盖率仍以outputs/compact_entity_selectors_20260908/RUNTIME_RESULT.md为准，本小窗口不计入覆盖率或Hit总量。','']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    for result in results:
        own_records=[dict(age=round(r['age'],4), type=f"{s['type']:02x}",
            length=s['length'],record=s['record']) for r in result['rows']
            for s in r.get('records',[]) if s['actor']==result['entity']]
        own_hits=[dict(age=round(r['age'],4),**h) for r in result['rows'] for h in r.get('hits',[])]
        print(json.dumps(dict(capture=result['capture'],counts=result['counts'],
            records=own_records,hits=own_hits),ensure_ascii=False))


if __name__ == '__main__':
    main()
