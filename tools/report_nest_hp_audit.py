"""Render audited references with explicit unresolved phase budgets."""
import json, sys, html
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.nest_hp_reference import profiles, damage_budget

out=ROOT/'outputs/verification/nest_hp_audit'
samples={}
for file in out.glob('*.json'):
    if file.name=='client_candidates.json': continue
    payload=json.loads(file.read_text(encoding='utf-8'))
    for row in payload.get('rows',[]):
        if row.get('hp_samples',0):
            samples.setdefault(str(row['template']),[]).append((file.stem,row))
parts=['<!doctype html><meta charset="utf-8"><title>巢穴 Boss 血量核对</title>',
 '<style>body{font:15px system-ui;margin:32px;color:#20333c}table{border-collapse:collapse;width:100%;margin-bottom:36px}td,th{padding:9px;border:1px solid #ccd8db;text-align:left}th{background:#e8f3f2}.note{background:#fff5d9;padding:16px}small{color:#596b73}</style>',
 '<h1>巢穴 Boss 血量核对 · 2026-10-18</h1>',
 '<p class="note">实体满血来自客户端基础 HP × 对应权重；用 UDP 原始当前血量核对倍率，不使用解析器旧 max_hp 或累计伤害反推。需扣血量不等于满血；“待核”不会退回同名经典值。表内需扣值为单个实体口径，同名多实体会累加。沙龙123、梦魇海龙缺少当前实战样本，不能称为全部实测通过。阶段比例未闭合的条目保留待核。</p>']
for p in profiles():
    parts.append('<h2>'+html.escape(p['label'])+'</h2><p>资源来源：'+html.escape(p.get('source',''))+'</p><table><tr><th>模板</th><th>名称</th><th>实体满血</th><th>单实体需扣参考</th><th>独立血量记录</th></tr>')
    for tid,r in p['entities'].items():
        observations=[]
        for name,row in samples.get(tid,[]):
            current=row['max_observed_current_hp']
            observations.append(f'{name}: 最大观测当前 HP {current:,}（满血的 {current/r["full_hp"]:.2%}）')
        budget=damage_budget(p,['audit@'+tid])
        cells=[tid,r['name'],f'{r["full_hp"]:,}',f'{budget:,}' if budget is not None else '待核，不用满血替代',
               '；'.join(observations) or '未取得该模板原始 HP 样本']
        parts.append('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in cells)+'</tr>')
    parts.append('</table>')
file=out/'巢穴Boss血量核对_20261018.html'
file.write_text('\n'.join(parts),encoding='utf-8')
print(file)
