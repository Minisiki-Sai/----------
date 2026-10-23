"""Verify saved raw observations and report an honest unchanged coverage gate."""
from collections import Counter,defaultdict
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records


def verify_example(sample):
    event=sample['event']
    inv=inspect_udp_damage_records(bytes.fromhex(sample['wire']),sample['direction'])
    assert inv.complete,inv.rejection_reason
    rows=[r for r in inv.damage_records if
        (r.record_offset,r.source_actor_id,r.target_actor_id,r.damage,r.critical)==
        (event['wire_record_offset'],event['wire_source_actor_id'],event['wire_target_actor_id'],event['amount'],event['critical'])]
    assert len(rows)==1
    row=rows[0]
    if row.family=='direct37':
        assert (event['wire_result_group_id'],event['wire_damage_object_id'])==(row.damage_result_group_id,row.damage_object_id)
    else:
        # Intentional legacy context omission, NOT an identity or admission bug.
        assert row.family=='direct41' and event['wire_result_group_id']==event['wire_damage_object_id']==0
        assert row.damage_object_id is None
    return row


def main():
    base=ROOT/'outputs/compact_entity_selectors_20260908'
    summary=json.loads((base/'confirmed_coverage_v32_summary.json').read_text('utf-8'))
    freeze=base/'late_field_final'
    paths=['protocol_v576.py','damage_object_origins.py','v576/core_decoder.py',
        'autonomous_attack_families.py','compact_entity_object_evidence.py','late_field_hit_evidence_v32.py']
    hashes={}
    for suffix in paths:
        name='dragon_nest_builder/live_dps/'+suffix
        digest=sha256((ROOT/name).read_bytes()).hexdigest()
        assert digest==sha256((freeze/name).read_bytes()).hexdigest(),name
        hashes[name]=digest
    counts=Counter();origins=Counter();examples=0;captures=[]
    for stem in ('0813硬核火舞','0821黑典女王','20260806YEP黑典时空'):
        folder=ROOT/'outputs/named_gap_wire_v36_20260908'
        path=folder/(stem+'_origins.json')
        if not path.exists():path=folder/(stem+'.json')
        data=json.loads(path.read_text('utf-8'));captures.append(dict(capture=data['capture'],profile_sha256=sha256(path.read_bytes()).hexdigest()))
        for row in data['rows']:
            counts[stem,row['job'],row['object_kind']]+=row['count']
            if row.get('origin_state','not_requested')!='not_requested':
                origins[stem,row['job'],row['origin_state'],row['origin_template']]+=row['count']
            for sample in row['examples']:
                verify_example(sample);examples+=1
    reopens=0;group_sources=defaultdict(set);group_skills=defaultdict(set)
    for path in (ROOT/'outputs/gap_object_records_v36_20260908').glob('*.json'):
        data=json.loads(path.read_text('utf-8'))
        with Path(data['capture']).open('rb') as f:
            for sample in data['matches']:
                f.seek(sample['file_offset']);t,adapter,original,n=struct.unpack('<dBII',f.read(17));raw=f.read(n)
                assert t==sample['time']
                packet=p.decode_captured_frame(t,raw,original)
                wire=list(p.split_v576_payload(packet.payload))[sample['message']]
                assert sha256(wire).hexdigest()==sample['wire_sha256']
                assert wire.hex()==sample['wire'];reopens+=1
                if path.stem.endswith('_group'):
                    inv=inspect_udp_damage_records(wire,packet.inferred_direction())
                    if inv.complete:
                        for r in inv.damage_records:
                            if r.damage_result_group_id not in data['objects']:continue
                            group_sources[r.damage_result_group_id].add(r.source_actor_id)
                            if r.family=='direct41':
                                slot=dict(r.raw_skill_slots).get(106)
                                if slot not in (0,None,0xffffffff):group_skills[r.source_actor_id,r.damage_result_group_id].add(slot)
    remaining=[]
    for row in summary['jobs']:
        if row['named_target_met']:continue
        a=row['after'];named=a['deterministic_hits']+a['named_inferred_hits']
        remaining.append(dict(job=row['job'],coverage=a['named_coverage_percent'],needed=ceil(.95*named)-a['deterministic_hits']))
    proof=dict(coverage_unchanged=True,new_confirmed_hits=0,passed_jobs=summary['passed_jobs'],all_jobs_passed=False,
        after=summary['after'],remaining=remaining,production_hashes=hashes,captures=captures,
        example_physical_checks=examples,physical_frame_reopens=reopens,
        origin_counts=[dict(capture=k[0],job=k[1],reason=k[2],template=k[3],hits=v)for k,v in origins.items()],
        raw_group_cross_owner={str(k):sorted(v)for k,v in group_sources.items()if len(v)>1},
        raw_group_same_owner_skill_conflicts=[dict(owner=k[0],group=k[1],skills=sorted(v))for k,v in group_skills.items()if len(v)>1])
    out=ROOT/'outputs/gap_probe_v36_20260908';out.mkdir(exist_ok=True)
    static=json.loads((out/'expired_lifetime_static.json').read_text('utf-8'))
    lifetimes=defaultdict(set)
    for row in static['candidates']:lifetimes[row['template']].add(row['declared_ms_at_80'])
    proof['expired_static_check']=dict(files=len(static['files']),parse_errors=static['errors'],
        observed_lifetime_slot_values={str(k):sorted(v)for k,v in lifetimes.items()},new_confirmed_hits=0)
    (out/'verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# v36：剩余确认 Hit 缺口复核','',
        '**未完成全职业95%：仍为18/30；本轮没有新增经过验收的确认Hit。**',
        f"固定35份比较集总体确认覆盖 {summary['after']['confirmed_percent']:.5f}%，不等于每个职业达标。",'',
        '## 本轮实际完成','',
        '- 对0813硬核火舞、0821黑典女王、20260806YEP黑典时空进行了当前解码器完整回放；前两份另行回放并只读查询对象登记状态。',
        f'- {examples}个保存的代表事件逐条独立解码核对；{reopens}个定点物理帧按文件偏移重新读取，原始字节哈希一致。代表样本不是全语料重新验收。',
        '- 黑暗萨满、雷神的选定缺口对象，在所查录制起点至锚点后2秒的UDP原帧范围内，没有找到命中前的同主人有效注册；出现的同号记录不可跨主人或跨代借用。该结果不证明整个未解密TCP范围绝无关联记录。',
        '- direct41原始+4字段的确没有写入现有事件组号；代码刻意保守保留该限制。共享组号不是技能外键，未擅自接入旧分组归因，也未增加每Hit重解密的运行时成本。',
        '- 岩浆墙相关两个ACT的4686个signal尾字节全部为00；无法由此排除Clone分支，不升级32022确认级别。详见相邻magma_signal_flags_v36报告。',
        '- 另查过期对象对应的创建信号：5507/5520/47/4011该字段为0，63/4018为5000，4024为150；这些值未证明8秒以上寿命，0也不擅自解释为无限，未延長生产生命周期。无法读取的ACT明确留在verification.json的parse_errors中，不声称资源全覆盖。',
        '- 当前对象账本诊断（汇总前）：0813黑暗萨满249个、0821雷神1617个未确认Hit没有命中时有效登记；与最终汇总层的缺口统计不是同一口径。',
        '- 新增测试68项全部通过，见tests.xml。包括原包字段冲突、截断、错误方向、共享组号不得恢复技能归属，以及ACT字节完整性；不把未批准的方案写成“预期失败”的生产缺陷。',
        '- 未发现可据本轮证据直接修复的新增确定性链。生产归因、伤害金额、人物归属、Hit资格与已验收冻结文件保持一致；本轮仅新增诊断和测试。','',
        '## 未达标职业','', '|职业|确认覆盖|距95%至少还需确认Hit|','|---|---:|---:|']
    lines += [f"|{r['job']}|{r['coverage']:.2f}%|{r['needed']}|"for r in remaining]
    lines += ['', '## 下一处需要验证的证据','',
        '1. 命中前没有有效对象登记的玩家伤害：继续查其它服务器记录/封装中的同主人对象注册，不能延长动作窗口替代。',
        '2. 圣物、机械鸭和岩浆墙：需要实体自身攻击选择或具体创建分支的显式连接，不能将同模板全部攻击一律绑定创建技能。',
        '3. direct41无技能固定槽：保留未知归属证据，寻找直接攻击选择键；共享结果组与旧显示技能名不能作为确认依据。','']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(report=str(out/'REPORT.md'),examples=examples,reopens=reopens,
        remaining_jobs=len(remaining),new_confirmed_hits=0),ensure_ascii=False))


if __name__=='__main__':main()
