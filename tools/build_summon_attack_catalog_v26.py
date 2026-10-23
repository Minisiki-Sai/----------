"""Build a narrow, per-monster-skill catalog. Never certify whole templates."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_live_dps_monster_catalog import parse_dnt
from tools.build_attack_instance_catalog import (
    ActParseError,
    parse_act,
    _unique_action_continuations,
)

TARGETS = {
    # Cleric relic families observed in live v576 captures.
    *range(30001, 30021),
    30101, 30102, 30151, 30152, 30153,
    30201, 30202, 30203, 30251, 30252, 30253, 30254,
    *range(30301, 30312), 30399,
    30401, 30402,
    *range(30501, 30521), 30601,
    # Archer/Academic/Sorceress/Assassin skill actors used by the live
    # attribution pipeline.  Keep this list explicit: this builder certifies
    # individual (template, monster-skill) pairs, never a numeric range by
    # resemblance alone.
    31001,
    32002, 32003, 32004, 32005, 32008, 32011, 32012, 32013, 32017,
    32018, 32021, 32022, 32026, 32029,
    33101, 33500, 33502, 33503,
    33601, 33604, 33627, 33632,
    *range(33607, 33615),
}
SHADOW_SKILLS = {262466, 262494, 262524, 262525, 262526}

# Some skill actors are selected directly by the learned player skill and do
# not have a SummonSkillTable row for their private monster attack.  These
# singleton families are independently reviewed in the live decoder's skill
# object table.  They may complete the final player-skill edge only after this
# builder has also proved the exact template, live AI monster-skill call and
# ACT action chain.  Ambiguous templates (for example Alfredo 32008) are
# intentionally absent.
TEMPLATE_PLAYER_FAMILIES = {
    **{template_id: 3409 for template_id in range(30001, 30021)},
    **{template_id: 3403 for template_id in (30101, 30102, 30151, 30152, 30153)},
    **{template_id: 3422 for template_id in (30201, 30202, 30203, 30251, 30252, 30253, 30254)},
    **{template_id: 3423 for template_id in (*range(30301, 30312), 30399)},
    **{template_id: 3412 for template_id in (30401, 30402)},
    **{template_id: 3409 for template_id in range(30501, 30521)},
    30601: 3409,
    31001: 1303,
    32004: 4209,
    32011: 4351,
    32026: 4351,
    32029: 4351,
    33101: 2410,
    33601: 6318,
}


def build(resource: Path):
    hashes = {}

    def digest(path):
        key = path.relative_to(resource).as_posix()
        hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        return key

    def dnt(name):
        path = resource / 'ext' / name
        digest(path)
        return parse_dnt(path)

    monsters = {r['RowId']: r for r in dnt('monstertable_quest.dnt') if r['RowId'] in TARGETS}
    actors = {r['RowId']: r for r in dnt('actortable.dnt')}
    files = {r['RowId']: r for r in dnt('filetable.dnt')}
    skilltables = {r['RowId']: r for r in dnt('monsterskilltable.dnt')}
    players = {r['RowId']: r for r in dnt('skilltable_character.dnt')}
    summons = dnt('summonskilltable.dnt')
    projectiles = {r['RowId']: r for r in dnt('weapontable_projectile.dnt')}
    levels = collections.defaultdict(list)
    for cls in ('academic', 'archer', 'assassin', 'cleric', 'etc', 'kali', 'soceress', 'warrior'):
        for r in dnt(f'skillleveltable_character{cls}.dnt'):
            levels[r['_SkillIndex']].append(dict(r, _SourceTable=cls))

    def canonical(sid):
        seen = set()
        while sid in players and players[sid].get('_BaseSkillID', 0) > 0:
            if sid in seen:
                raise ValueError('cyclic player skill base')
            seen.add(sid)
            sid = players[sid]['_BaseSkillID']
        return sid

    all_paths = list(resource.rglob('*.act'))
    parsed = {}

    def act(path):
        if path not in parsed:
            digest(path)
            parsed[path] = parse_act(path)
        return parsed[path]

    def action_params(row):
        # ProcessParam2/3 may contain selector programs such as
        # ``101;2;Skill_SummonSlime1;101;4;Skill_SummonSlime2;...``.
        # Treat every explicit action token as an independent static edge;
        # keeping the whole selector string as one value silently lost the
        # alternate summon forms.
        result = set()
        for i in range(1, 11):
            value = str(row.get(f'_ProcessParam{i}', '')).strip()
            # Preserve ordinary single action names (Attack, Heal, Cure,
            # Activate, ...); only selector programs need token expansion.
            if value and ';' not in value and re.search(r'[A-Za-z_]', value):
                result.add(value)
            result.update(re.findall(r'(?:Skill|Attack)_[A-Za-z0-9_]+', value))
        return result

    # Exact player action aliases, including PVP variants, establish identity of
    # a canonical skill family, not an inference from player class/proximity.
    player_entries = collections.defaultdict(set)
    player_rows_by_action = collections.defaultdict(list)
    for sid, rows in levels.items():
        # skilltable_character also contains monster skills (NeedJob=0).
        # This is static table typing, NOT a cast-owner/class uniqueness claim.
        if sid not in players or players[sid].get('_NeedJob', 0) <= 0:
            continue
        for row in rows:
            for name in action_params(row):
                if name.startswith(('Skill_', 'Attack_')):
                    player_entries[name.casefold()].add(canonical(sid))
                    player_rows_by_action[name.casefold()].append(dict(
                        player_skill=sid, canonical=canonical(sid), row_id=row['RowId'],
                        apply_type=row['_ApplyType'], action=name))

    player_action_proofs = collections.defaultdict(list)
    player_act_paths = [p for p in all_paths if p.parent.name.lower() == 'assassin'
                        and p.name.lower() in ('assassin_skill_2st_lightfury.act',
                                              'assassin_skill_2st_abysswalker.act')]
    for path in player_act_paths:
        for entry, actions in _unique_action_continuations(act(path), player_entries).items():
            for action in actions:
                player_action_proofs[action.name.casefold()].append(dict(
                    canonical=next(iter(player_entries[entry])), entry=entry,
                    action=action.name, act=path.relative_to(resource).as_posix(),
                    player_rows=player_rows_by_action[entry],
                    edge_policy='ACT next_action or type1 indexed-string1; stop at another learned family'))

    # Player ACT signals 48 and 126 are the two observed skill-actor template
    # constructors.  Their first u32 is the concrete monster/entity template;
    # the remaining constructor payload controls placement/lifetime.  This
    # provides the missing deterministic edge for skills such as Relic of
    # Lightning, Soul Gate, Ice Palm and Pact of Ajna.  Build the relation from
    # the exact learned action instead of guessing it from a cast-time window
    # or inheriting a whole monster family.
    template_player_proofs = collections.defaultdict(list)
    player_act_files = [
        p for p in all_paths
        if '/char/player/' in p.as_posix().lower()
        and 'skill' in p.name.lower()
    ]
    for path in player_act_files:
        try:
            parsed_player_act = act(path)
        except ActParseError:
            # A few files use the legacy ACT container.  They cannot provide
            # typed signal-126 proof and must not block the verified catalog.
            continue
        for action in parsed_player_act.actions:
            families = player_entries.get(action.name.casefold(), set())
            if len(families) != 1:
                continue
            family = next(iter(families))
            for signal in action.signals:
                if signal.signal_type not in (48, 126) or len(signal.payload) < 4:
                    continue
                template = int.from_bytes(signal.payload[:4], 'little')
                if template not in TARGETS:
                    continue
                template_player_proofs[template].append(dict(
                    method=(
                        'player_act_type48_entity_constructor'
                        if signal.signal_type == 48
                        else 'player_act_type126_entity_constructor'
                    ),
                    template=template,
                    canonical=family,
                    base_skill=canonical(family),
                    action=action.name,
                    act=path.relative_to(resource).as_posix(),
                    signal_type=signal.signal_type,
                    signal_value=template,
                    range_policy=(
                        'exact player action plus typed skill-actor constructor; '
                        'never inherited by another template'
                    ),
                ))

    accepted, rejected, template_rows = [], [], []
    for tid, monster in sorted(monsters.items()):
        filename = files[actors[monster['_ActorTableID']]['_ActName']]['_FileName'].replace('\\', '/').lower()
        paths = [p for p in all_paths if p.as_posix().lower().endswith('/' + filename)]
        if len(paths) != 1:
            raise ValueError((tid, filename, paths))
        path = paths[0]
        parsed_act = act(path)
        own_actions = {a.name.casefold(): a for a in parsed_act.actions}
        ai_path = resource / 'script/monsterai' / monster['_CustomAI'].lower()
        digest(ai_path)
        ai_text = ai_path.read_text('utf8', errors='replace')
        ai_text = re.sub(r'--\[\[.*?\]\]', lambda m: '\n' * m.group().count('\n'), ai_text, flags=re.S)
        ai = collections.defaultdict(list)
        for number, line in enumerate(ai_text.splitlines(), 1):
            code = line.split('--', 1)[0]
            for sid in re.findall(r'\bskill_index\s*=\s*(\d+)', code):
                ai[int(sid)].append(dict(line=number, text=code.strip()))
        template_rows.append(dict(template=tid, actor=monster['_ActorTableID'],
            skilltable=monster['_SkillTable'], act=path.relative_to(resource).as_posix(),
            ai=ai_path.relative_to(resource).as_posix(),
            action_indices=[dict(index=i, name=a.name, damage_signals=sum(s.signal_type == 4 for s in a.signals),
                                 projectiles=sorted({int.from_bytes(s.payload[:4], 'little') for s in a.signals if s.signal_type == 15}))
                            for i, a in enumerate(parsed_act.actions)]))
        table = skilltables.get(monster['_SkillTable'])
        if table is None:
            rejected.append(dict(
                template=tid,
                monster_skill=0,
                encounter_mode='pve',
                actions=[],
                projectiles=[],
                direct_damage_signals=0,
                ai_calls=[],
                static_mappings=[],
                level_rows=[],
                act=path.relative_to(resource).as_posix(),
                reason='monster_skill_table_row_missing',
            ))
            continue
        pool = {v for k, v in table.items() if k.startswith('_SkillIndex') and v > 0}
        candidates = pool & set(ai)
        if tid >= 33607:
            candidates &= SHADOW_SKILLS  # bounded review; no whole shadow pool certification
        for sid in sorted(candidates):
            normal = [r for r in levels[sid] if r['_ApplyType'] == 0]
            primary = {r['_ProcessParam1'] for r in normal}
            named = {name for r in normal for name in action_params(r) if name.casefold() in own_actions}
            reason = None
            if not primary or any(name.casefold() not in own_actions for name in primary):
                reason = 'missing_exact_monster_process_action'
            # DNT ProcessParam2/3 explicitly name phases (duck self-bomb END is
            # NOT guessed from suffix). Limit closure by other monster entries.
            monster_entries = collections.defaultdict(set)
            for other in pool:
                for row in levels[other]:
                    if row['_ApplyType'] == 0:
                        for name in action_params(row):
                            if name.casefold() in own_actions:
                                monster_entries[name.casefold()].add(other)
            # Same-action level IDs share a family; collect explicit current
            # phase names first, then next/type1 edges without entering another
            # primary learned action not explicitly named by this skill.
            reached = set()
            pending = [n.casefold() for n in named]
            while pending:
                name = pending.pop()
                if name in reached or name not in own_actions:
                    continue
                if name in monster_entries and sid not in monster_entries[name]:
                    continue
                reached.add(name)
                action = own_actions[name]
                refs = {action.next_action.casefold()}
                refs.update(v.casefold() for s in action.signals if s.signal_type == 1 for i, v in s.strings if i == 1)
                # Generic locomotion/stand is not attack proof.
                pending.extend(r for r in refs if r.startswith(('skill_', 'attack_')))
            attack_actions = [own_actions[n] for n in sorted(reached)]
            direct = sum(s.signal_type == 4 for a in attack_actions for s in a.signals)
            pids = sorted({int.from_bytes(s.payload[:4], 'little') for a in attack_actions for s in a.signals if s.signal_type == 15})
            if not direct and not pids:
                reason = reason or 'no_typed_damage_or_projectile_in_explicit_action_chain'
            if any(pid not in projectiles for pid in pids):
                reason = reason or 'unknown_projectile_row'
            mappings = []
            for row in summons:
                parts = [int(n) for n in str(row['_MonsterSkill']).split(',')]
                matches = sid == parts[0] if len(parts) == 1 else len(parts) == 2 and parts[0] <= sid <= parts[1]
                if matches:
                    mappings.append(dict(method='summonskilltable', row_id=row['RowId'],
                        monster_spec=row['_MonsterSkill'], canonical=row['_SkillIndex'],
                        base_skill=canonical(row['_SkillIndex']),
                        range_policy='inclusive endpoints, additionally require exact live AI call and actual monster level row'))
            # Shadow/transformation actors (33607+) reuse one entity template
            # across several learned player actions.  For those rows the
            # constructor proves only the entity family, while the exact
            # monster action below proves the player skill.  Do not merge the
            # broader constructor family into that narrower action proof.
            if not mappings and tid < 33607 and tid in template_player_proofs:
                constructor_families = {
                    row['canonical'] for row in template_player_proofs[tid]
                }
                if len(constructor_families) == 1:
                    mappings.extend(template_player_proofs[tid])
            if not mappings and tid in TEMPLATE_PLAYER_FAMILIES:
                family = TEMPLATE_PLAYER_FAMILIES[tid]
                mappings.append(dict(
                    method='reviewed_singleton_skill_object_template',
                    template=tid,
                    canonical=family,
                    base_skill=canonical(family),
                    range_policy=(
                        'exact template plus live AI monster-skill call and '
                        'exact ACT action chain; never inherited by another template'
                    ),
                ))
            if tid >= 33607:
                for name in primary:
                    mappings.extend(player_action_proofs[name.casefold()])
            families = {r['canonical'] for r in mappings}
            if len(families) != 1:
                reason = reason or 'missing_or_ambiguous_player_skill_family'
            proof = dict(template=tid, monster_skill=sid, encounter_mode='pve',
                actions=sorted(a.name for a in attack_actions), projectiles=pids,
                direct_damage_signals=direct, ai_calls=ai[sid], static_mappings=mappings,
                level_rows=[dict(row_id=r['RowId'], level=r['_SkillLevel'],
                                process={f'_ProcessParam{i}': r.get(f'_ProcessParam{i}', '') for i in range(1, 4)}) for r in normal],
                act=path.relative_to(resource).as_posix())
            if reason:
                rejected.append(dict(proof, reason=reason))
            else:
                proof['player_skill'] = families.pop()
                proof['canonical_player_skill'] = canonical(proof['player_skill'])
                proof['proof_id'] = hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                accepted.append(proof)
    return dict(schema='summon-self-attack-v26.1', resource_root=str(resource),
        policy='Per exact (template, raw monster attack skill), never whole-template. PVE only. No object fallback enabled.',
        hashes=hashes, templates=template_rows, accepted=accepted, rejected=rejected)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--resource', type=Path, default=Path('E:/ext0811.rar/Resource00/Resource00/resource'))
    ap.add_argument('--output', type=Path, default=ROOT / 'outputs/v26_summon_attack_chain/static_catalog.json')
    ap.add_argument('--python-output', type=Path)
    args = ap.parse_args()
    result = build(args.resource)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    if args.python_output:
        rows = []
        for item in result['accepted']:
            rows.append((
                item['template'], item['monster_skill'], item['player_skill'],
                item['proof_id'], tuple(item['actions']), tuple(item['projectiles']),
            ))
        catalog_hash = hashlib.sha256(args.output.read_bytes()).hexdigest()
        source = [
            '"""Generated exact summon/self-attack specs (entity constructors v100).',
            '',
            'Each row requires an exact entity template, exact private monster',
            'skill slot, independently bound owner lifetime, and PvE target.',
            'Generated from DNT + AI + ACT evidence; no runtime filesystem read.',
            '"""',
            'from types import MappingProxyType',
            'from .summon_self_attack_evidence_v26 import AttackSpec',
            '',
            f'CATALOG_SHA256 = {catalog_hash!r}',
            '',
            '_ROWS = (',
        ]
        source.extend(f'    {row!r},' for row in rows)
        source.extend([
            ')',
            '',
            'SUMMON_SELF_ATTACK_SPECS = MappingProxyType({',
            '    (row[0], row[1]): AttackSpec(*row, encounter_mode="pve") for row in _ROWS',
            '})',
            f'assert len(SUMMON_SELF_ATTACK_SPECS) == {len(rows)}',
            '',
        ])
        args.python_output.parent.mkdir(parents=True, exist_ok=True)
        args.python_output.write_text('\n'.join(source), encoding='utf8')
    print(json.dumps(dict(accepted=len(result['accepted']), rejected=len(result['rejected']), output=str(args.output)), ensure_ascii=False))


if __name__ == '__main__':
    main()
