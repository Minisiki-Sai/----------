"""Recheck saved combat evidence with current exact level resolution. No uploads."""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.combat_skill_levels import infer_combat_skill_levels
from dragon_nest_builder.live_dps.models import CombatSnapshot, PlayerSnapshot, SkillCastEvent, SkillParameterEvent


def snapshot_from_audit(data):
    players = tuple(PlayerSnapshot(source_id=p['source_id'], name=p['name'], job=p['job'],
        damage=0, healing=0, dps=0, hits=0, critical_hits=0, critical_rate=0,
        active_seconds=data['recording_seconds']) for p in data['players'])
    events = tuple(SkillParameterEvent(timestamp_ms=r['timestamps_ms'][0], source_id=r['source_id'],
        source_name='', job=r['job'], skill_id=r['skill_id'], raw_skill_id=r['raw_skill_id'],
        skill_name=r['skill_name'], target_id='', skill_level_row_id=r['level_row'],
        effect_class_id=r['effect_class'], parameters=r['parameter'],
        duration_ms=r['duration_ms'], permanent=r['permanent']) for r in data['parameter_groups'])
    casts = tuple(SkillCastEvent(timestamp_ms=t, source_id=r['source_id'], source_name='',
        job='', skill_id=r['skill_id'], skill_name=r['skill_name'], packet_kind=r['packet_kind'])
        for r in data.get('casts', ()) for t in r['timestamps_ms'])
    return CombatSnapshot(elapsed_seconds=data['recording_seconds'], total_damage=data['total_damage'],
        total_healing=0, players=players, damage_series=(), skill_parameter_events=events, skill_cast_events=casts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    store = DataStore.load()
    audits = []
    lines = ['# 战斗技能等级识别核对', '',
        '来源：此前完整解码保存的技能响应/动作审计；本次重新执行等级识别，不声称重新解码了原包。',
        '等级行与原始技能ID共同匹配拆包表。不使用查看装备/登录档案，不通过伤害大小猜等级。',
        '未使用为理论轴1级策略；已使用但未知或冲突的等级保留基准并标记。被动、EX开关不按未使用降级。', '']
    for path in args.inputs:
        data = json.loads(path.read_text(encoding='utf-8'))
        snapshot = snapshot_from_audit(data)
        reports = infer_combat_skill_levels(snapshot, store)
        audits.append({'capture': data['capture'], 'capture_sha256':data['capture_sha256'],
            'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'reports':reports})
        lines.extend(['## '+Path(data['capture']).name, '',
            '|角色|职业|已确认等级|未使用按1级|已使用等级未知/冲突|', '|---|---|---:|---:|---:|'])
        for player in snapshot.players:
            report = reports.get(player.source_id, {})
            counts = Counter(r['status'] for r in report.get('skills', ()))
            lines.append(f"|{player.name}|{player.job}|{counts['confirmed_combat_row']}|{counts['unused_theory_level_1']}|{counts['used_level_unknown']+counts['conflicting_combat_levels']}|")
        for player in snapshot.players:
            report = reports.get(player.source_id, {})
            lines.extend(['', f'### {player.name}（{player.job}）', '',
                '|技能|原基准有效等级|本次有效等级|依据|', '|---|---:|---:|---|'])
            for r in report.get('skills', ()):
                label = {'confirmed_combat_row':'战斗等级行确认', 'unused_theory_level_1':'未使用：理论策略1级',
                         'used_level_unknown':'已使用但等级未知：保留基准',
                         'conflicting_combat_levels':'等级冲突：保留基准'}.get(r['status'],r['status'])
                lines.append(f"|{r['skill']}|{r['baseline_level']}|{r['level']}|{label}|")
    (args.output/'combat_skill_levels.json').write_text(json.dumps(audits, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'战斗技能等级核对.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps([{'capture':a['capture'], 'counts':dict(Counter(r['status'] for p in a['reports'].values() for r in p['skills']))} for a in audits],ensure_ascii=False))


if __name__ == '__main__':
    main()
