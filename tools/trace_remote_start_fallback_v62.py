"""One-hit remote 3019 START / action-name / meter fallback audit.

Disk bytes only. No client execution, emulation, live-memory access, production
writes, or meter/corpus replay. Outputs only in this tool's v62 directory.
"""
from __future__ import annotations
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import trace_chain_lightning_meter_v59 as v59
from tools.trace_remote_skill_init_v61 import references
from tools.review_client_damage_types_v44 import BASE
from tools.trace_actor_action_namespace_v47 import manifest_tokens
from tools.probe_magma_signal_flags_v36 import parse_act6

OUT = ROOT / "outputs/remote_start_fallback_v62_20260909"
RESOURCE = Path("E:/ext0811.rar/Resource00/Resource00/resource")
INPUTS = {}


def load(path):
    data = path.read_bytes()
    INPUTS[str(path)] = sha256(data).hexdigest()
    return data


def read(path):
    return json.loads(load(path))


def write(name, obj):
    (OUT / name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


class Audit:
    def __init__(self):
        self.view = v59.StaticView()
        self.ranges = {}

    def add(self, name, start, end=None):
        f = self.view.function(start)
        if end is None:
            if not f:
                raise ValueError(f"leaf needs exact reviewed length: {start:#x}")
            start,end = f[:2]
        if f:
            assert start in {r['rva'] for r in v59._disassemble(self.view,f[0],f[1])}
        rows = v59._disassemble(self.view,start,end)
        self.ranges[name] = {"start":hex(start),"end":hex(end),"sha256":sha256(self.view.read(start,end-start)).hexdigest(),"rows":rows}
        (OUT / (name+".txt")).write_text("\n".join(f"{r['rva']:08X} {r['bytes']:<32} {r['instruction']}" for r in rows)+"\n",encoding="utf-8")
        return rows

    def member_refs(self, start, end, offsets):
        refs = []
        for f in self.view.functions:
            if not start <= f[0] < end:
                continue
            rows = v59._disassemble(self.view,f[0],f[1])
            for i,r in enumerate(rows):
                if any(f"+ {hex(o)}]" in r['instruction'] for o in offsets):
                    refs.append({"at":hex(r['rva']),"fragment":[hex(x) for x in f[:2]],"context":rows[max(0,i-4):i+7]})
        return refs


def select_hit():
    prior = ROOT / "outputs/chain_lightning_meter_v60_20260909"
    evidence = read(prior / "v59_completed.json")["capture_evidence"]
    hit = read(prior / "remaining_325.json")["rows"][0]["physical"]
    assert (hit['frame'],hit['damage_object_id']) == (42061,100558)
    events = evidence['focus_control_events']
    before = [r for r in events if r['sequence'] < hit['sequence'] and r['record_type']=='0x0E'][-1]
    after = next(r for r in events if r['sequence'] > hit['sequence'] and r['record_type']=='0x0E' and r['skill_id'] != 3019)
    relevant = [r for r in evidence['focus_hits'] if before['sequence'] <= r['sequence'] <= after['sequence']]
    result = {"target_hit":hit,"previous_request":before,"next_other_request":after,
        "controls_between":[r for r in events if before['sequence'] <= r['sequence'] <= after['sequence']],
        "hits_between":relevant,"method_counts":dict(Counter(r['native_method'] for r in relevant)),
        "sequence_is_capture_order_not_identity_or_success_proof":True}
    write("one_hit_selection.json",result)
    return result


def old_action_namespace():
    directory = RESOURCE / "char/player/cleric"
    manifest = load(directory / "cleric.act")
    entries, selected, index = [],[],0
    for ordinal, token in enumerate(manifest_tokens(manifest)):
        path = directory / token.decode("ascii")
        raw = load(path)
        parsed = parse_act6(raw)
        entry = {"ordinal":ordinal,"token":token.decode('ascii'),"sha256":sha256(raw).hexdigest(),
            "first_action":index,"actions":len(parsed['actions'])}
        entries.append(entry)
        for local,a in enumerate(parsed['actions']):
            if a['name'] in ("Skill_ChargeVolt","Skill_LightningBolt","Stand") or index+local in (72,75):
                selected.append({"global_ordinal":index+local,"local_ordinal":local,"file":str(path),**a})
        index += len(parsed['actions'])
    result = {"authority":"old snapshot, cold successful load in manifest order; runtime cache/PAK precedence not assumed",
        "manifest_sha256":sha256(manifest).hexdigest(),"entries":entries,"total_actions":index,"selected_actions":selected}
    write("old_cleric_action_namespace.json",result)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    a = Audit()
    select_hit()
    nodes = {
        "registration_post_callback":0x3771B0,
        "skill_start":0xB97BE0,"skill_action_name_set":0xB97910,
        "handle_validator":0x272210,"skill_handle_register":0x28EAA0,
        "name_to_action":0x4EF60,"name_dictionary_loader":0x4EDF0,
        "local_error_evaluation":0xB932D0,
    }
    for name,rva in nodes.items():
        a.add(name,rva)
    for name,start,end in (
        ("base_level_tree",0x376C70,0x376D12),
        ("index_to_action_name",0x4EF30,0x4EF56),
        ("network_0e",0xA59780,0xA59A07),
        ("network_12",0xA59220,0xA5950F),
        ("action_change",0x5286C0,0x528D40),
        ("meter_key_resolution",0x6A5646,0x6A5926),
    ):
        a.add(name,start,end)
    write("level_member_references.json",a.member_refs(0x373920,0x37D700,(0x1A0,0x1C0,0x1E8,0x1F8,0x210,0x120,0x128)))
    write("targeted_callers.json",references(a.view,{0x376C70:"base level tree",0xB97BE0:"skill start",0x4EF30:"action ordinal lookup",0x28EAA0:"skill handle register"}))
    old_action_namespace()
    write("static_ranges.json",a.ranges)
    write("provenance.json",{"inputs":INPUTS|a.view.inputs,"tool_sha256":sha256(Path(__file__).read_bytes()).hexdigest()})
    print(json.dumps({"out":str(OUT),"static_ranges":len(a.ranges)}))


if __name__ == "__main__":
    main()
