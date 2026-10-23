"""Generate/review integration patches in outputs ONLY; never edit runtime."""
from __future__ import annotations
import ast
import difflib
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.summon_self_attack_bridge_v26 import SCHEMA_FIELDS


def unique_replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Integration anchor not unique: {old[:100]!r}')
    return text.replace(old, new, 1)


def build():
    out = ROOT / 'outputs/v26_summon_attack_chain/integration'
    out.mkdir(parents=True, exist_ok=True)
    results = []
    paths = ['dragon_nest_builder/live_dps/models.py',
             'dragon_nest_builder/live_dps/v576/core_decoder.py',
             'dragon_nest_builder/live_dps/v576/status_decoder.py']
    for rel in paths:
        path = ROOT / rel
        before_bytes = path.read_bytes()
        before = before_bytes.decode('utf8').replace('\r\n', '\n')
        after = before
        if path.name == 'models.py':
            definition = ''.join(f'    {name}: {typ} = {default!r}\n' for name, (typ, default) in SCHEMA_FIELDS.items())
            after = unique_replace(after, '    entity_owner_evidence_id: str = ""\n',
                '    entity_owner_evidence_id: str = ""\n'
                '    # Summon-owned attack metadata; not a player cast or a UI grouping decision.\n' + definition)
            assignment = ''.join(
                f'            {name}=max(0, int(value.get({name!r}, 0) or 0)),\n' if typ == 'int'
                else f'            {name}=str(value.get({name!r}, "") or ""),\n'
                for name, (typ, _) in SCHEMA_FIELDS.items())
            anchor = '            entity_owner_evidence_id=str(value.get("entity_owner_evidence_id", "") or ""),\n'
            after = unique_replace(after, anchor, anchor + assignment)
        elif path.name == 'core_decoder.py':
            after = unique_replace(after, 'from hashlib import sha256\n',
                'from hashlib import sha256\nfrom ..summon_self_attack_bridge_v26 import annotate_existing_event\n')
            anchor = '        self.entity_owner_evidence = EntityOwnerEvidenceLedger()\n'
            after = unique_replace(after, anchor, anchor +
                '        # Opt-in metadata bridge. Main supplies current physical-hit/owner context.\n'
                '        self.summon_self_attack_context_provider = None\n'
                '        self.summon_self_attack_specs = {}\n'
                '        self.summon_self_attack_encounter_mode = "unknown"\n')
            anchor = '        return CombatEvent(\n            timestamp_ms=max(0, round((packet.timestamp_seconds - origin) * 1000)),\n'
            after = unique_replace(after, anchor, anchor.replace('return CombatEvent(', 'event = CombatEvent('))
            anchor = '            **entity_evidence,\n        )\n\n    def feed_events('
            after = unique_replace(after, anchor,
                '            **entity_evidence,\n        )\n'
                '        return annotate_existing_event(self, packet, decoded, event)\n\n    def feed_events(')
        else:
            anchor = 'from ..damage_attribution import should_prefer_damage_object_skill\n'
            after = unique_replace(after, anchor, anchor +
                'from ..summon_self_attack_bridge_v26 import annotate_existing_event\n')
            anchor = '                    **entity_evidence,\n                )\n            )\n        return events\n'
            after = unique_replace(after, anchor,
                '                    **entity_evidence,\n                )\n            )\n'
                '            events[-1] = annotate_existing_event(self, packet, decoded, events[-1])\n'
                '        return events\n')
        ast.parse(after, filename=rel)
        patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                      fromfile='a/' + rel, tofile='b/' + rel))
        (out / (path.name + '.patch')).write_text(patch, encoding='utf8')
        (out / (path.stem + '_candidate.py')).write_text(after, encoding='utf8')
        assert path.read_bytes() == before_bytes, 'runtime was modified'
        results.append(dict(source=rel, source_sha256=hashlib.sha256(before_bytes).hexdigest(),
            candidate_sha256=hashlib.sha256(after.encode()).hexdigest(),
            patch=str(out / (path.name + '.patch')), source_untouched=True))
    manifest = dict(files=results, semantics='METADATA ONLY; opt-in provider; no knownhits or UI grouping automatic change',
        context_provider_contract='(packet, decoded, existing_event) -> (PhysicalHit, CURRENT EntityLife) | None',
        provider_must_not=['invent physical IDs', 'use latest cast/class', 'reuse stale owner snapshots',
                           'synthesize birth fingerprint/order from present hit', 'fill missing self slot from object/creation'],
        schema={name:dict(type=typ, default=default) for name,(typ,default) in SCHEMA_FIELDS.items()})
    (out / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(manifest,ensure_ascii=False))


if __name__ == '__main__':
    build()
