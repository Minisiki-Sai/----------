"""Generate exact client ACT tuples from the version-agreement audit.

The audit joins SkillLevel ApplyType=0 action keys to one canonical SkillTable
family, resolves every type15 signal in the merged player manifests, and
requires old/current action, signal, config and canonical family agreement.
No observed damage or time window contributes to these identities.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

CLASS_JOBS = {
    'warrior': ('剑皇', '月之领主', '狂战士', '毁灭者', '黑暗复仇者'),
    'archer': ('狙翎', '魔羽', '影舞者', '风行者', '银色猎人'),
    'cleric': ('圣骑士', '十字军', '圣徒', '雷神'),
    'soceress': ('火舞', '冰灵', '时空领主', '黑暗女王'),
    'academic': ('重炮手', '机械大师', '炼金圣士', '药剂师'),
    'kali': ('黑暗萨满', '噬魂者', '刀锋舞者', '灵魂舞者'),
    'assassin': ('烈', '影', '曜', '暗'),
}

def build(input_path: Path):
    raw = input_path.read_bytes()
    payload = json.loads(raw.decode('utf8'))
    old = {(r['class'], r['action_index'], r['signal_key'], r['config_row']): r
           for r in payload['old_rows']}
    current = {(r['class'], r['action_index'], r['signal_key'], r['config_row']): r
               for r in payload['current_rows']}
    rows = {}
    for row in payload['stable_rows']:
        key = (row['class'], row['action_index'], row['signal_key'], row['config_row'])
        skill = int(row['canonical_skill_id'])
        if (key not in old or key not in current or skill <= 0
                or old[key]['canonical_skill_id'] != skill
                or current[key]['canonical_skill_id'] != skill
                or old[key]['action_name'].casefold() != row['action_name'].casefold()
                or current[key]['action_name'].casefold() != row['action_name'].casefold()):
            raise ValueError(f'Unverified resource-version tuple: {key}')
        if key in rows and rows[key] != skill:
            raise ValueError(f'Conflicting canonical family: {key}')
        rows[key] = skill
    return rows, hashlib.sha256(raw).hexdigest()

def write_module(rows, digest, output_path):
    lines = ['"""Generated complete ACT coordinates with old/current resource agreement."""',
             'from types import MappingProxyType', '', f'INPUT_SHA256 = {digest!r}',
             f'PLAYER_CLASS_JOBS = MappingProxyType({CLASS_JOBS!r})',
             'PLAYER_ROOT_CLASS_ACTION_SIGNAL_SKILLS = MappingProxyType({']
    for key, skill in sorted(rows.items()):
        lines.append(f'    {key!r}: {skill},')
    lines.extend(['})', '', 'FULL_PLAYER_ROOT_ACTION_SIGNAL_SKILLS = MappingProxyType({',
                  '    (job, action, signal, config): skill',
                  '    for (player_class, action, signal, config), skill',
                  '    in PLAYER_ROOT_CLASS_ACTION_SIGNAL_SKILLS.items()',
                  '    for job in PLAYER_CLASS_JOBS[player_class]', '})', ''])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('\n'.join(lines), encoding='utf8')

def main():
    p = argparse.ArgumentParser(); p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] /
                   'dragon_nest_builder/live_dps/generated_player_root_full_action_signal_specs_v474.py')
    args = p.parse_args(); rows, digest = build(args.input)
    write_module(rows, digest, args.output)
    print(json.dumps({'class_tuples': len(rows), 'input_sha256': digest}))

if __name__ == '__main__': main()
