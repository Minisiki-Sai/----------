"""Narrow, read-only 32005/261101 evidence probe; never replay or attribution.

Run with Python -B. Only this probe's output directory may be written. ACT
next_action is control flow; signal strings (including Hit_Front), adjacent
skill rows, FFFFFFFF and context114 are never substituted for an own skill.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full
from tools.build_attack_instance_catalog import _Reader
from tools.build_live_dps_monster_catalog import parse_dnt
from tools.probe_compact_entity_attack_selectors import walk
from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.entity_owner_evidence import decode_entity_owner_records

RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
OUT = ROOT / 'outputs/duck_261101_v32_20260908'
PREVIOUS = ROOT / 'outputs/compact_entity_selectors_20260908'
SID, TEMPLATE, PLAYER = 261101, 32005, 4217
START = 'Attack_Shock_Start_Mutant'
LOOP = 'Attack_Shock_Mutant_Loop'
END = 'Attack_Shock_Mutant_End'
MAX_FRAME_BYTES = 131072


class EvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def digest(data):
    return sha256(data).hexdigest()


def anchor(data, start, end):
    require(0 <= start <= end <= len(data), 'invalid byte span')
    return dict(offset=start, end_exclusive=end, length=end-start,
                hex=data[start:end].hex(), sha256=digest(data[start:end]))


def select_dnt(path, predicate):
    """Existing DNT interpretation, plus independently walked field offsets.

    Unselected rows are used only to advance the parser, never as proof. All
    selected values are compared with the pre-existing parser before use.
    """
    data = path.read_bytes()
    r = _Reader(data, path)
    r.take(4)
    columns = struct.unpack('<H', r.take(2))[0]
    count = struct.unpack('<I', r.take(4))[0]
    fields, schema = [], {}
    for _ in range(columns):
        at = r.offset
        size = struct.unpack('<H', r.take(2))[0]
        name, kind = r.take(size).decode('ascii'), r.u8()
        require(name not in schema and kind in (1, 2, 3, 4, 5), 'DNT schema')
        fields.append((name, kind))
        schema[name] = dict(type=kind, **anchor(data, at, r.offset))
    header_end = r.offset
    selected = []
    for _ in range(count):
        start = r.offset
        row = {'RowId': struct.unpack('<I', r.take(4))[0]}
        spans = {'RowId': (start, r.offset)}
        for name, kind in fields:
            at = r.offset
            if kind == 1:
                n = struct.unpack('<H', r.take(2))[0]
                value = r.take(n).decode('utf-8', errors='replace')
            else:
                value = struct.unpack('<i' if kind in (2, 3) else '<f', r.take(4))[0]
            row[name], spans[name] = value, (at, r.offset)
        if predicate(row):
            selected.append(dict(values=row, row_span=anchor(data, start, r.offset),
                                 fields={k: anchor(data, *v) for k, v in spans.items()}))
    # The established DNT parser does not interpret trailing bytes either.
    # Preserve that fact instead of silently claiming complete-file consumption.
    expected = [row for row in parse_dnt(path) if predicate(row)]
    require([x['values'] for x in selected] == expected, 'DNT parser disagreement')
    return dict(path=str(path), sha256=digest(data), size=len(data), row_count=count,
                header_end=header_end, parsed_end=r.offset, trailing_bytes=len(data)-r.offset,
                schema=schema, selected=selected)


def one(table):
    require(len(table['selected']) == 1, 'expected exactly one DNT row')
    return table['selected'][0]['values']


def strip_lua_comments(data):
    """Preserve byte offsets; recognize strings and Lua long/line comments."""
    result = bytearray(data)
    i = 0
    while i < len(data):
        if data[i:i+2] == b'--':
            match = re.match(rb'\[(=*)\[', data[i+2:])
            if match:
                close = b']' + match[1] + b']'
                end = data.find(close, i+2+match.end())
                require(end >= 0, 'unterminated Lua long comment')
                end += len(close)
            else:
                end = data.find(b'\n', i)
                end = len(data) if end < 0 else end
            for pos in range(i, end):
                if data[pos] not in (10, 13):
                    result[pos] = 32
            i = end
        elif data[i] in (34, 39):
            quote = data[i]
            i += 1
            while i < len(data) and data[i] != quote:
                i += 2 if data[i] == 92 else 1
            require(i < len(data), 'unterminated Lua string')
            i += 1
        else:
            i += 1
    return bytes(result)


def ai_evidence(path):
    data = path.read_bytes()
    clean = strip_lua_comments(data)
    pattern = rb'\{[^{}]*\bskill_index\s*=\s*261101\b[^{}]*\}'
    matches = list(re.finditer(pattern, clean))
    require(len(matches) == 1, 'AI own skill absent/ambiguous')
    match = matches[0]
    require(re.search(rb'\bcheckpassiveruneskill\s*=\s*"4217,293119,1"', match[0]),
            'AI rune condition mismatch')
    before = clean[:match.start()]
    table = list(re.finditer(rb'\b(g_Lua_\w+)\s*=\s*\{', before))
    require(table and table[-1][1] == b'g_Lua_Skill', 'AI skill table scope')
    # This probe accepts the installed data-only AI, not arbitrary executable Lua.
    require(not re.search(rb'\b(function|dofile|require|loadfile)\b', clean),
            'AI executable state needs separate interpretation')
    references = []
    for ref in re.finditer(rb'\baction_name\s*=\s*"([^"]+)"', clean):
        references.append(dict(action=ref[1].decode('ascii'),
                               line=data[:ref.start()].count(b'\n')+1,
                               bytes=anchor(data, ref.start(), ref.end())))
    return dict(path=str(path), sha256=digest(data), size=len(data),
                state='g_Lua_Skill', line=data[:match.start()].count(b'\n')+1,
                entry=match[0].decode('ascii'), bytes=anchor(data, match.start(), match.end()),
                active_action_references=references,
                explicit_loop_action_reference=any(x['action'] == LOOP for x in references))


def anchored_action(path, parsed, index):
    data = path.read_bytes()
    action = deepcopy(parsed['actions'][index])
    r = _Reader(data, path)
    r.offset = action['offset']
    spans = {}
    for key, method in [('name', r.string), ('animation', r.string), ('frames', r.i32),
                        ('next_action', r.string), ('blend', r.i32), ('next_frame', r.i32)]:
        at = r.offset
        value = method()
        require(value == action[key], f'ACT header mismatch: {key}')
        spans[key] = anchor(data, at, r.offset)
    action.update(index=index, fields=spans)
    following = (parsed['actions'][index+1]['offset'] if index+1 < len(parsed['actions'])
                 else len(data))
    action['row_span'] = dict(offset=action['offset'], end_exclusive=following,
                              sha256=digest(data[action['offset']:following]))
    for j, signal in enumerate(action['signals']):
        end = action['signals'][j+1]['offset'] if j+1 < len(action['signals']) else following
        signal.update(index=j, bytes=anchor(data, signal['offset'], end),
                      type_bytes=anchor(data, signal['offset'], signal['offset']+4))
    return action


def prove_route(level, actions):
    """Exact own DNT entry -> explicit ACT next_action -> typed loop damage.

    This proves the static attack route only. End is independently named by DNT;
    no ACT Loop->End edge, timer behavior or processor-empty fallback is claimed.
    """
    if level.get('_SkillIndex') != SID or level.get('_ApplyType') != 0:
        return dict(closed=False, reason='wrong_own_skill_or_mode')
    if (level.get('_ProcessParam1'), level.get('_ProcessParam3')) != (START, END):
        return dict(closed=False, reason='own_start_or_end_missing')
    by_name = {}
    for a in actions:
        if a['name'] in by_name:
            return dict(closed=False, reason='ambiguous_action_name')
        by_name[a['name']] = a
    if any(n not in by_name for n in (START, LOOP, END)):
        return dict(closed=False, reason='missing_exact_actor_action')
    if by_name[START]['next_action'] != LOOP:
        return dict(closed=False, reason='no_explicit_start_to_loop')
    if by_name[LOOP]['next_action'] != LOOP:
        return dict(closed=False, reason='loop_not_self_linked')
    if level.get('_ProcessParam2') not in ('', LOOP):
        return dict(closed=False, reason='explicit_process_loop_conflict')
    phases = [by_name[n] for n in (START, LOOP, END)]
    if any(s['type'] in (15, 48, 126) for a in phases for s in a['signals']):
        return dict(closed=False, reason='unresolved_object_or_entity_child')
    typed = [s for s in by_name[LOOP]['signals'] if s['type'] == 4]
    if not typed:
        return dict(closed=False, reason='no_loop_typed_damage')
    if any(s['type'] == 1 for a in phases for s in a['signals']):
        return dict(closed=False, reason='additional_control_signal_needs_audit')
    return dict(closed=True, reason='own_DNT_start_to_explicit_ACT_next_to_type4',
                skill=SID, player_skill=PLAYER, template=TEMPLATE,
                missing_dnt_loop=level['_ProcessParam2'] == '',
                edges=[dict(source=START, target=LOOP, kind='ACT.next_action'),
                       dict(source=LOOP, target=LOOP, kind='ACT.next_action')],
                typed_signal_offsets=[s['offset'] for s in typed],
                end_source='own DNT ProcessParam3; no Loop->End ACT edge claimed',
                timing_or_hit_count_proved=False)


def build_static(resource=RESOURCE):
    tables = {}

    def table(name, predicate):
        tables[name] = select_dnt(resource/'ext'/name, predicate)
        return tables[name]

    monster = one(table('monstertable_quest.dnt', lambda r: r['RowId'] == TEMPLATE))
    installed = one(table('monsterskilltable.dnt', lambda r: r['RowId'] == monster['_SkillTable']))
    slots = [k for k, v in installed.items() if k.startswith('_SkillIndex') and v == SID]
    require(len(slots) == 1, 'own skill not uniquely installed')
    skill = one(table('skilltable_rune.dnt', lambda r: r['RowId'] == SID))
    require(skill['_NeedJob'] == 0 and skill['_Processor1'] == 2, 'own skill class/processor')
    levels = table('skillleveltable_rune.dnt', lambda r: r['_SkillIndex'] == SID)
    normal = [r['values'] for r in levels['selected'] if r['values']['_ApplyType'] == 0]
    require(len(normal) == 1, 'ambiguous own PvE row')
    player = one(table('skilltable_character.dnt', lambda r: r['RowId'] == PLAYER))
    require(player['_NeedJob'] > 0, 'player skill is not job-bound')
    runes = table('runeresonatetable.dnt', lambda r: r.get('_baseSkill1') == PLAYER
                 and r.get('_changeSkill1') == 293119)
    require(runes['selected'], 'rune->base edge missing')
    actor = one(table('actortable.dnt', lambda r: r['RowId'] == monster['_ActorTableID']))
    file = one(table('filetable.dnt', lambda r: r['RowId'] == actor['_ActName']))
    suffix = file['_FileName'].replace('\\', '/').lower()
    path = resource/'char/monster'/suffix
    require(path.resolve().is_relative_to(resource.resolve()), 'ACT path outside resources')
    parsed = parse_full(path)
    selected = [anchored_action(path, parsed, i) for i, a in enumerate(parsed['actions'])
                if a['name'] in (START, LOOP, END, 'Attack3_Saliva')]
    route = prove_route(normal[0], parsed['actions'])
    ai = ai_evidence(resource/'script/monsterai'/monster['_CustomAI'].lower())
    ai['loop_transition_from_ai'] = False
    return dict(schema='duck-261101-static-v32', tables=tables, installed_slot=slots[0],
                ai=ai, act=dict(path=str(path), sha256=digest(path.read_bytes()),
                               size=path.stat().st_size, version=parsed['version'],
                               total_actions=len(parsed['actions']), actions=selected),
                route=route, production_changed=False, admitted_hits=0,
                limitations=['Static action reachability, not live execution or coverage.',
                             'No inferred empty-processor behavior or Loop->End timing.',
                             'Runtime still needs exact own skill, owner generation, PvE and job gates.'])


def hit_fields(sample):
    wire = bytes.fromhex(sample['wire'])
    decoded = walk(wire)
    require(decoded is not None, 'incomplete or ambiguous whole record walk')
    phase, body, records = decoded
    require(phase == sample['phase'], 'phase mismatch')
    m = sample['marker']
    matches = [r for r in records if r[1:] == (m, 8, 115)]
    require(len(matches) == 1, 'no complete hit at the supplied marker')
    u32 = lambda at: int.from_bytes(body[at:at+4], 'little')
    return dict(source=u32(m+8), object=u32(m+13), target=matches[0][0],
                amount=int.from_bytes(body[m+19:m+27], 'little'),
                self_skill=u32(m+102), context114=u32(m+114),
                result_group=u32(m+4), phase=phase, marker=m,
                self_bytes=anchor(body, m+102, m+106),
                context_bytes=anchor(body, m+114, m+118), wire_sha256=digest(wire))


def supports_own_hit(fields, route):
    """Necessary narrow gate only; NOT a dynamic owner/admission certificate."""
    return route.get('closed') is True and fields['self_skill'] == SID


def capture_anchor(capture, sample):
    offset = sample.get('file_offset', sample.get('offset'))
    require(isinstance(offset, int) and offset >= 6, 'missing frame offset')
    with Path(capture).open('rb') as f:
        require(f.read(6) == b'DNDP1\0', 'capture signature')
        f.seek(offset)
        header = f.read(17)
        require(len(header) == 17, 'truncated frame header')
        time, _, original, size = struct.unpack('<dBII', header)
        require(0 < size <= MAX_FRAME_BYTES, 'frame exceeds narrow-read bound')
        raw = f.read(size)
        require(len(raw) == size, 'truncated frame')
    packet = protocol.decode_captured_frame(time, raw, original)
    require(packet is not None and packet.transport == 'UDP', 'not decoded UDP')
    messages = list(protocol.split_v576_payload(packet.payload))
    mi = sample['message']
    require(0 <= mi < len(messages), 'message outside frame')
    wire = bytes.fromhex(sample['wire'])
    require(messages[mi] == wire, 'capture wire differs from diagnostic fixture')
    at = raw.find(wire)
    return dict(capture=str(capture), file_offset=offset, frame=sample.get('frame'),
                message=mi, time=time, direction=packet.inferred_direction(),
                size=17+size, header_hex=header.hex(), frame_sha256=digest(header+raw),
                raw_sha256=digest(raw), wire_hex=wire.hex(),
                wire_file_offset=offset+17+at if at >= 0 else None,
                scope='one seeked frame; no replay, scoring, or lifecycle reconstruction')


def object_negative(registration, hit, actions):
    """Audit a supplied registration/Hit pair; never fills an absent own slot."""
    decoded = walk(bytes.fromhex(registration['wire']))
    require(decoded is not None, 'registration whole-record walk')
    phase, body, records = decoded
    m = registration['marker']
    found = [r for r in records if r[1] == m and r[2] == 9 and r[3] in (41, 50)]
    require(len(found) == 1 and phase == registration['phase'], 'registration selector')
    u32 = lambda at: int.from_bytes(body[at:at+4], 'little')
    actor, obj, ai, si = found[0][0], u32(m+3), u32(m+34), u32(m+38)
    a = next((a for a in actions if a['index'] == ai), None)
    require(a is not None and 0 <= si < len(a['signals']), 'unresolved ACT selector')
    s = a['signals'][si]
    require(s['type'] == 15, 'expected projectile negative control')
    h = hit_fields(hit)
    require((actor, obj) == (h['source'], h['object']), 'object/entity mismatch')
    return dict(entity=actor, object=obj, action_index=ai, signal_index=si,
                action=a['name'], signal_type=s['type'], projectile=s['u32'][0],
                selector_bytes=anchor(body, m+34, m+42), hit=h,
                own_261101=False, reason='Saliva projectile route is outside own Shock route',
                lifecycle_claim=False)


def collect_raw(static, previous=PREVIOUS):
    sources, samples, objects, raw_anchors = [], [], [], []
    seen = set()

    def load(path):
        data = path.read_bytes()
        sources.append(dict(path=str(path), sha256=digest(data), size=len(data)))
        return json.loads(data)

    def add(sample, source, capture=None):
        fields = hit_fields(sample)
        key = (capture, fields['wire_sha256'], fields['marker'])
        if key not in seen:
            seen.add(key)
            samples.append(dict(source=source, capture=capture, sample=sample,
                                decoded=fields,
                                narrow_own_gate=supports_own_hit(fields, static['route'])))
        if capture and ('file_offset' in sample or 'offset' in sample):
            raw_anchors.append(capture_anchor(capture, sample))

    inventory = load(previous/'self_slot_inventory.json')
    inv_rows = [r for r in inventory['rows'] if r['template'] == TEMPLATE]
    for row in inv_rows:
        sample = row['example']
        add(sample, 'self_slot_inventory', sample['capture'])
    gaps = load(previous/'engineer_chain_gaps.json')
    gap_rows = [r for r in gaps['rows'] if r['template'] == TEMPLATE]
    gap_unwalked = []
    for row in gap_rows:
        for ex in row['examples']:
            decoded = walk(bytes.fromhex(ex['wire']))
            if decoded is None:
                gap_unwalked.append(dict(serial=ex['serial'], reason='whole_walk_unavailable'))
                continue
            phase, _, records = decoded
            markers = [m for _, m, op, size in records if (op, size) == (8, 115)
                       and m == ex['event']['wire_record_offset']]
            if not markers:
                gap_unwalked.append(dict(serial=ex['serial'], reason='not_complete_0873_hit'))
            for m in markers:
                add(dict(wire=ex['wire'], marker=m, phase=phase, serial=ex['serial']),
                    'engineer_chain_gaps', gaps['capture'])
    probe_counts = []
    for path in sorted((previous/'engineer_probe').glob('*.json')):
        doc = load(path)
        for row in doc['rows']:
            if row['template'] != TEMPLATE:
                continue
            probe_counts.append(dict(capture=doc['capture'], action=row['action'],
                                     signal=row['signal'], self_slots=row['self_slots'],
                                     joined_hit_candidates=row['joined_hit_candidates']))
            for pair in row['joins']:
                reg, hit = pair['registration'], pair['hit']
                negative = object_negative(reg, hit, static['act']['actions'])
                negative['capture'] = doc['capture']
                objects.append(negative)
                add(hit, 'engineer_probe_join', doc['capture'])
                for sample in (reg, reg['birth'], reg['owner']):
                    require(sample is not None, 'negative control lacks birth/owner fixture')
                    raw_anchors.append(capture_anchor(doc['capture'], sample))
                birth = protocol.decode_v576_monster_spawn(bytes.fromhex(reg['birth']['wire']), None)
                require(birth and birth.template_id == TEMPLATE and birth.actor_id == negative['entity'],
                        'negative birth template/entity mismatch')
                owners = decode_entity_owner_records(bytes.fromhex(reg['owner']['wire']))
                require(any(o.entity_id == negative['entity'] and o.owner_id == reg['owner']['owner']
                            for o in owners), 'negative owner record mismatch')
    counts = Counter(str(s['decoded']['self_skill']) for s in samples)
    anchors = {(a['capture'], a['file_offset'], a['message']): a for a in raw_anchors}
    return dict(schema='duck-261101-bounded-raw-v32', sources=sources,
                scope='ONLY saved diagnostic examples and seeked frames; not full corpus',
                sample_count=len(samples), own_slot_sample_counts=dict(sorted(counts.items())),
                exact_261101_samples=sum(s['decoded']['self_skill'] == SID for s in samples),
                raw_frame_count=len({(a['capture'], a['file_offset']) for a in anchors.values()}),
                raw_anchors=list(anchors.values()), samples=samples, object_negatives=objects,
                unwalked_gap_examples=gap_unwalked, existing_probe_counts=probe_counts,
                inventory_261101_groups=sum(r['self_skill'] == SID for r in inv_rows),
                production_changes=0, new_admitted_hits=0, coverage_recomputed=False)


def report_text(static, raw):
    by_name = {a['name']: a for a in static['act']['actions']}
    start, loop = by_name[START], by_name[LOOP]
    level = next(r for r in static['tables']['skillleveltable_rune.dnt']['selected']
                 if r['values']['_ApplyType'] == 0)
    offset = lambda a, key: a['fields'][key]['offset']
    return f'''# 32005 / 261101 独立窄缺口探针 v32

结论：静态攻击路径闭合={static['route']['closed']}。261101 自己的 DNT 起始动作经 ACT 的显式 next_action 到达带 type 4 的 Loop；没有从相邻技能补字段。

证据链：32005 -> 安装技能表 {one(static['tables']['monsterskilltable.dnt'])['RowId']} ({static['installed_slot']}) -> AI g_Lua_Skill 中 261101、checkpassiveruneskill="4217,293119,1" -> Rune DNT 4217/293119 -> 261101 自身 PvE DNT -> ACT。

关键原始锚点（十进制，文件起点为零，字符串锚点包含长度前缀）：

- skillleveltable_rune.dnt RowId={level['values']['RowId']}；_ProcessParam1 @{level['fields']['_ProcessParam1']['offset']}；_ProcessParam2 @{level['fields']['_ProcessParam2']['offset']}，原始字节 `{level['fields']['_ProcessParam2']['hex']}`，值仍为空；_ProcessParam3 @{level['fields']['_ProcessParam3']['offset']}。
- academic_mechadug.lua 第 {static['ai']['line']} 行，261101 条目 @{static['ai']['bytes']['offset']}。AI 明确技能和符文条件，但没有显式 Shock Loop 状态动作边。
- mechaduck.act action {start['index']}，{START} @{start['offset']}；next_action 字段 @{offset(start, 'next_action')}，值 {LOOP}。
- action {loop['index']}，{LOOP} @{loop['offset']}；next_action @{offset(loop, 'next_action')} 指回自身。
- Loop 内 type 4 攻击信号起点：{', '.join(str(x) for x in static['route'].get('typed_signal_offsets', []))}；这三个自身动作没有 type 15/48/126 对象或实体创建子链。
- _ProcessParam3 独立给出 {END}；未声称 ACT 有 Loop->End 边，未解释空字段的底层处理器行为、持续时间或命中次数。

原始窗口审查：从指定历史输出解码 {raw['sample_count']} 条去重 Hit 示例，并按已有偏移读取 {raw['raw_frame_count']} 个单帧小窗口。自身槽分布：{json.dumps(raw['own_slot_sample_counts'])}。其中精确 261101 示例为 {raw['exact_261101_samples']}。该范围不能用于断言整个捕获没有 261101，也不能计算职业覆盖率。

负例：engineer_probe 保存的对象注册/Hit 对应 ACT action 50 / signal 5，Attack3_Saliva -> type 15 / projectile 5054；Hit 自身槽为 FFFFFFFF。这些对象虽可连接同一实体与对象，但不能进入 261101 的 Shock 路径。type 4 的 Hit_Front 字符串是待解释的信号参数，不能当作实体控制流；本证书只采用 next_action 字段。

交付边界：静态路径可供后续精确自身技能接入审阅；动态闭合仍需真实 261101 Hit、当前出生代际与主人、PvE、职业及已准入 Hit 校验。本次新增准入/确认 Hit=0，生产变更=0，未重放或评分，用户给定 89.8178% 未重新测量，也未宣称达到 95%。

复现：`py -3.12 -B tools/probe_duck_261101_v32.py`。测试：`py -3.12 -B -m pytest tests/test_duck_261101_v32.py -q -p no:cacheprovider`。
完整字段原始字节、文件 SHA256、ACT 信号区间见 static_evidence.json；原始包与单帧偏移见 raw_evidence.json。
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource-root', type=Path, default=RESOURCE)
    args = parser.parse_args()
    static = build_static(args.resource_root)
    raw = collect_raw(static)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, doc in [('static_evidence.json', static), ('raw_evidence.json', raw)]:
        (OUT/name).write_text(json.dumps(doc, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (OUT/'report.md').write_text(report_text(static, raw), encoding='utf-8')
    print(json.dumps(dict(static_route_closed=static['route']['closed'],
                          exact_261101_raw_samples=raw['exact_261101_samples'],
                          raw_frames=raw['raw_frame_count'], new_confirmed_hits=0,
                          output=str(OUT)), ensure_ascii=False))


if __name__ == '__main__':
    main()
