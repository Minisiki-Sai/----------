"""One duck's saved raw evidence and one bounded UDP window; no production edits.

No skill promotion, scoring, full-capture replay, client execution or resource
version equivalence. All ledgers below are fresh local diagnostic instances.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.compact_entity_object_evidence import CompactEntityObjectLedger, decode_selector_records
from dragon_nest_builder.live_dps.entity_owner_evidence import EntityOwnerEvidenceLedger
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

OUT = ROOT / 'outputs/projectile_gap_v46_20260909'
POSITIVE = ROOT / 'tests/data/compact_entity_selectors_raw.json'
DEFAULT = ROOT / 'outputs/compact_rejection_v33_20260908/duck_default_object_occurrences.json'
PROFILE = ROOT / 'outputs/compact_rejection_v33_20260908/0821硬核04.json'
ENGINEER = ROOT / 'outputs/compact_entity_selectors_20260908/engineer_probe/0821硬核04.json'
HEADER = struct.Struct('<dBII')
DEPENDENCIES = [POSITIVE, DEFAULT, PROFILE, ENGINEER, Path(__file__),
    ROOT/'tests/test_summon_gap_v46.py',
    *[ROOT/'dragon_nest_builder/live_dps'/p for p in ('protocol_v576.py',
      'compact_entity_object_evidence.py', 'entity_owner_evidence.py', 'udp_damage_records.py')]]


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(raw):
    return sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text('utf-8'))


def serial(anchor):
    return anchor['frame'] * 1000 + anchor['message']


def selected_case():
    return next(c for c in read(POSITIVE)['cases'] if c['template']==32005
                and Path(c['capture']).stem=='0821硬核04')


def positive_chain(case):
    """Revalidate four actual wires; saved labels cannot supply an object join."""
    r, h = case['registration'], case['hit']
    birth, owner = r['birth'], r['owner']
    raw_birth = bytes.fromhex(birth['wire'])
    spawn = protocol.decode_v576_monster_spawn(raw_birth, 'server_to_client', {32005})
    require(spawn is not None and spawn.actor_id==r['entity'] and spawn.template_id==case['template'], 'birth mismatch')
    require(birth['t'] <= owner['t'] <= r['t'] <= h['t'], 'time order')
    require(serial(birth) < serial(owner) <= serial(r) <= serial(h), 'packet order')
    owners = EntityOwnerEvidenceLedger()
    owners.observe_birth(spawn.actor_id, spawn.template_id, birth['t'], sha(raw_birth), serial(birth))
    owners.observe_packet(bytes.fromhex(owner['wire']), 'server_to_client', owner['t'], serial(owner))
    binding = owners.resolve(r['entity'], case['template'], h['t'])
    require(binding is not None and binding.record.owner_id==owner['owner'], 'owner mismatch')
    objects = CompactEntityObjectLedger()
    objects.observe_packet(bytes.fromhex(r['wire']), 'server_to_client', r['t'], serial(r), owners)
    evidence = objects.objects.get((r['entity'], binding.generation, h['object']))
    require(evidence is not None and evidence.spec is not None, 'exact selector object missing')
    require(evidence.record.offset==r['marker'] and evidence.record.action==r['action']
            and evidence.record.signal==r['signal'], 'selector metadata mismatch')
    require(0 <= h['t']-evidence.timestamp <= evidence.spec[3], 'expired selector')
    raw = bytes.fromhex(h['wire'])
    inventory = inspect_udp_damage_records(raw, 'server_to_client')
    require(inventory.complete, 'malformed hit')
    hits = [v for v in inventory.damage_records if v.record_offset==h['marker']]
    require(len(hits)==1, 'exact hit missing')
    hit = hits[0]
    require(hit.admissible_fields and (hit.source_actor_id,hit.damage_object_id,hit.target_actor_id,hit.damage)==
            (r['entity'],h['object'],h['target'],h['amount']), 'physical identity mismatch')
    require(dict(hit.raw_skill_slots)[102]==0xffffffff, 'positive self slot may not be overwritten')
    return dict(structural_chain=True, existing_positive_control=True, new_confirmed_hits=0,
        birth=asdict(spawn), generation=binding.generation, owner=binding.record.owner_id,
        selector=asdict(evidence.record), existing_spec=evidence.spec,
        hit=hit_evidence(hit), delay=h['t']-evidence.timestamp,
        four_anchors=[dict(kind=kind, **{k:a[k] for k in ('frame','file_offset','message','t')},
                         wire_sha256=sha(bytes.fromhex(a['wire'])))
                      for kind,a in (('birth',birth),('owner',owner),('selector',r),('hit',h))],
        scope='Structural fresh-ledger control, not current PvE/admission proof or any new accepted Hit.')


def hit_evidence(hit):
    return dict(marker=hit.record_offset, end=hit.record_end, source=hit.source_actor_id,
        target=hit.target_actor_id, object=hit.damage_object_id, amount=hit.damage,
        critical=hit.critical, group=hit.damage_result_group_id, slots=dict(hit.raw_skill_slots),
        phase=hit.xor_phase, record_hex=hit.record_bytes.hex(), wire_sha256=hit.wire_sha256,
        record_sha256=hit.wire_record_sha256)


def default_inventory(data):
    rows = []
    for index, row in enumerate(data['matches']):
        raw = bytes.fromhex(row['wire'])
        require(sha(raw)==row['wire_sha256'], 'saved wire digest mismatch')
        inv = inspect_udp_damage_records(raw, row['direction'])
        require(inv.complete, 'saved raw framing failure')
        hits = [h for h in inv.damage_records if h.source_actor_id==data['entity'] and h.damage_object_id==data['object']]
        require(len(hits)==1 and hits[0].admissible_fields, 'selected default hit mismatch')
        rows.append(dict(source_pointer=f'/matches/{index}', frame=row['frame'],file_offset=row['file_offset'],
            message=row['message'], time=row['time'], **hit_evidence(hits[0])))
    skills = Counter(r['slots'][102] for r in rows)
    return dict(entity=data['entity'], object=data['object'], rows=rows,
        observed_self_slots=dict(skills), context_slots=dict(Counter(r['slots'][114] for r in rows)),
        contradicts_one_skill_per_object=len(set(skills)-{0,0xffffffff})>1,
        negative_conclusion='Same entity+object has multiple positive self slots then empty slots. Context4216/object/timing cannot pick the empty-slot attack.',
        new_confirmed_hits=0)


def take_window(case, default):
    """Exactly one physical interval starting at the known birth file offset."""
    birth = case['registration']['birth']
    path = Path(case['capture'])
    require(str(path)==default['capture'], 'capture mismatch')
    cutoff = default['anchor'] + .01
    require(0 < cutoff-birth['t'] <= 12, 'window exceeds 12 seconds')
    start = birth['file_offset']
    stats = path.stat()
    selected, counts, chunk = [], Counter(), bytearray()
    with path.open('rb') as stream:
        require(stream.read(6)==b'DNDP1\0', 'capture format')
        stream.seek(start)
        frame = birth['frame']-1
        while True:
            offset = stream.tell()
            header = stream.read(HEADER.size)
            require(len(header)==HEADER.size, 'early EOF')
            time, adapter, original, length = HEADER.unpack(header)
            if time > cutoff:
                break
            require(time >= birth['t'] and length <= 65536, 'window timestamp/size')
            require(len(chunk)+HEADER.size+length <= 1024*1024, 'window exceeds 1 MiB')
            raw = stream.read(length)
            require(len(raw)==length, 'truncated frame')
            chunk.extend(header+raw)
            frame += 1
            counts['physical_frames'] += 1
            require(counts['physical_frames']<=6000, 'frame budget')
            packet = protocol.decode_captured_frame(time, raw, original)
            if packet is None or packet.transport!='UDP':
                counts['non_udp_or_undecoded_frames'] += 1
                continue
            for mi,wire in enumerate(protocol.split_v576_payload(packet.payload)):
                counts['udp_messages'] += 1
                direction = packet.inferred_direction()
                inv = inspect_udp_damage_records(wire, direction)
                spawn = protocol.decode_v576_monster_spawn(wire,direction,{32005})
                related = bool(spawn and spawn.actor_id==default['entity'])
                typed = []
                if inv.complete:
                    counts['fully_framed_messages'] += 1
                    for record in inv.typed_records:
                        actor = int.from_bytes(record.record_bytes[:4],'little')
                        if actor==default['entity']:
                            related = True
                            typed.append(dict(actor=actor, marker=record.record_offset,
                                opcode=record.record_type,length=record.declared_length,
                                record_hex=record.record_bytes.hex()))
                    if any(h.source_actor_id==default['entity'] for h in inv.damage_records):
                        related = True
                else:
                    counts['not_in_typed_inventory'] += 1
                # Keep all unframed messages too, so negative evidence does not hide an unknown family.
                if related or not inv.complete:
                    selected.append(dict(frame=frame,file_offset=offset,message=mi,time=time,
                        direction=direction,wire=wire.hex(),wire_sha256=sha(wire),typed=typed,
                        related=related,inventory_complete=inv.complete,
                        spawn=asdict(spawn) if spawn else None,
                        physical_frame_sha256=sha(raw)))
        end = start+len(chunk)
    require(path.stat().st_size==stats.st_size and path.stat().st_mtime_ns==stats.st_mtime_ns, 'capture changed')
    return dict(schema='one-duck-bounded-window-v46',capture=str(path),entity=default['entity'],
        object=default['object'],start_time=birth['t'],cutoff=cutoff,start_file_offset=start,
        end_file_offset_exclusive=end,physical_interval_sha256=sha(chunk),physical_interval_bytes=len(chunk),
        counts=dict(counts),messages=selected,
        completeness='All physical frames in this interval inspected; all unframed UDP messages retained. No TCP reassembly or claim outside interval.')


def inspect_window(window):
    selectors, actions, hits = [], [], []
    for message in window['messages']:
        raw=bytes.fromhex(message['wire'])
        require(sha(raw)==message['wire_sha256'],'window wire digest mismatch')
        locator={k:message[k] for k in ('frame','file_offset','message','time','wire_sha256')}
        for row in decode_selector_records(raw,message['direction']):
            if row.entity==window['entity']:
                selectors.append(dict(**locator,selector=asdict(row)))
        inv=inspect_udp_damage_records(raw,message['direction'])
        if inv.complete:
            for record in inv.typed_records:
                actor=int.from_bytes(record.record_bytes[:4],'little')
                if actor==window['entity'] and record.record_type==3:
                    actions.append(dict(**locator,marker=record.record_offset,length=record.declared_length,
                        record_hex=record.record_bytes.hex(),
                        raw_action_byte=record.record_bytes[7] if len(record.record_bytes)>7 else None,
                        object_join_proven=False))
            for hit in inv.damage_records:
                if hit.source_actor_id==window['entity']:
                    hits.append(dict(**locator,physical=hit_evidence(hit)))
    target=[r for r in selectors if r['selector']['object_id']==window['object']]
    return dict(selectors=selectors,actions=actions,hits=hits,default_object_selectors=target,
        unique_selector_wires=len({r['wire_sha256'] for r in selectors}),
        selectors_missing_for_default=not target,
        unframed_saved_messages=sum(not m['inventory_complete'] for m in window['messages']),
        candidate_status='missing_exact_selector_for_default_object' if not target else 'selector_found_requires_independent_review',
        new_confirmed_hits=0)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--take-window',action='store_true',help='Read exactly one <=12s,<=1MiB interval; otherwise only use saved fixture')
    args=ap.parse_args()
    hashes={str(p):sha(p.read_bytes()) for p in DEPENDENCIES}
    case, default=selected_case(),read(DEFAULT)
    positive=positive_chain(deepcopy(case))
    negative=default_inventory(default)
    fixture=OUT/'summon_window.json'
    OUT.mkdir(parents=True,exist_ok=True)
    if args.take_window:
        require(not fixture.exists(),'window fixture already exists; use saved-only default')
        window=take_window(case,default)
        fixture.write_text(json.dumps(window,ensure_ascii=False,indent=2)+'\n','utf-8')
    else:
        window=read(fixture)
    hashes[str(fixture)]=sha(fixture.read_bytes())
    findings=inspect_window(window)
    profile=read(PROFILE)
    engineer=read(ENGINEER)
    rows=[{k:v for k,v in r.items() if k not in ('examples','joins')} for r in engineer['rows'] if r['template']==32005]
    report=dict(schema='summon-gap-v46',template=32005,capture=case['capture'],
        input_sha256=hashes,script_sha256=hashes[str(Path(__file__))],
        positive_control=positive,default_object_failure=negative,window_findings=findings,
        window_scope={k:v for k,v in window.items() if k!='messages'},
        historical_profile=[{k:v for k,v in r.items() if k!='examples'} for r in profile['rows']],
        historical_engineer_selector_rows=rows,
        static_reference=case['static'],
        resource_scope='Saved old extracted ACT certificate only; no equivalence to the current installed client/resource version is asserted.',
        new_confirmed_hits=0,production_changed=False,full_capture_replays=0,
        decision='Do not map 4216 or all same-object hits. Existing exact selector101704 is a positive control; default101687 needs a distinct proven action/object link.')
    require(all(sha(Path(p).read_bytes())==h for p,h in hashes.items()),'input changed')
    report['inputs_unchanged']=True
    target=OUT/'summon_audit.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf-8')
    lines=['# v46 单模板召唤物缺口复核','',
        '仅检查32005重磅鸭，0821硬核04。0新增确认Hit、0生产修改、0全包重放。','',
        '## 可复核结论','',
        f"- 已有正控：出生实体138/template32005 → 重复主人{positive['owner']} → 09/32 selector(action50,signal5)、object101704 → direct37。四条原始wire重新解码匹配；这是已存在的4352/5054结构链，不是新增覆盖。",
        f"- 默认对象101687的14条保存命中：m+102分布 `{negative['observed_self_slots']}`，m+114分布 `{negative['context_slots']}`。同一实体/对象已有260408与260608两种正自身技能，然后出现FFFFFFFF，不能按对象或4216上下文统一升级。",
        f"- 唯一新窗口：出生起至首个空槽命中后10ms，{window['cutoff']-window['start_time']:.6f}秒、{window['physical_interval_bytes']}物理字节、{window['counts']['physical_frames']}帧。提取{len(findings['selectors'])}条该实体selector、{len(findings['actions'])}条03动作记录；目标101687 selector={len(findings['default_object_selectors'])}。",
        f"- Selector实际对象：{sorted({r['selector']['object_id'] for r in findings['selectors']})}；只有{findings['unique_selector_wires']}条不同wire，重传不算新生成。两个03/14记录的动作原始字节均为0x30（48），不据此猜测旧ACT动作或技能。",
        f"- 未完整进入typed inventory的消息保留{findings['unframed_saved_messages']}条，故阴性结论只限已解析记录；未声称所有消息族/TCP均不存在关联。03动作不带已证明的object外键，不能用时间邻近接到Hit。",'',
        '## 精确失败锚点','',
        '|帧|文件偏移|object|自身槽m+102|上下文m+114|伤害|','|---:|---:|---:|---:|---:|---:|']
    for r in negative['rows']:
        lines.append(f"|{r['frame']}|{r['file_offset']}|{r['object']}|{r['slots'][102]}|{r['slots'][114]}|{r['amount']}|")
    lines+=['','## 证据与复现','',
        'summon_window.json保存唯一窗口的相关及未解析原始wire、物理帧位置/哈希；summon_audit.json保存出生→主人→selector正控、14条失败记录的decoded record hex、实际selector/动作记录。',
        '旧工程师profile的144个候选、当前拒绝profile的137通过/111缺注册属于历史汇总前口径，不转换成重炮手1129缺口的收益预测。',
        '静态关联只引用旧拆包ACT的固定哈希及动作/信号坐标，未重新普查资源、未证明版本等价，也未用旧技能显示名称倒推技能。','',
        '```powershell',
        "& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B tools/audit_summon_gap_v46.py",
        "& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B -m unittest discover -s tests -p test_summon_gap_v46.py -v",'```','',
        f"脚本SHA256 `{report['script_sha256']}`；所有输入前后哈希一致。默认复现只读保存窗口，不再打开抓包。",'']
    target.with_suffix('.md').write_text('\n'.join(lines),'utf-8')
    print(json.dumps(dict(output=str(target),default_self_slots=negative['observed_self_slots'],
        window_seconds=window['cutoff']-window['start_time'],window_bytes=window['physical_interval_bytes'],
        selectors=len(findings['selectors']),default_selectors=len(findings['default_object_selectors']),
        actions=len(findings['actions']),new_confirmed_hits=0),ensure_ascii=False))


if __name__=='__main__':
    main()
