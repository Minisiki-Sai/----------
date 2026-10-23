"""3019-only action/control/meter shadow audit; one process, no production edits.

The physical replay is completed by trace_chain_lightning_meter_v59.py. This
tool consumes that hash-checked replay, original native rows, fixed35 saved
checkpoint, and hash-pinned client bytes. It never promotes action proximity.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import trace_chain_lightning_meter_v59 as v59

OUT = ROOT / "outputs/chain_lightning_meter_v60_20260909"
PRIOR = OUT / "v59_completed.json"
CHECKPOINT = ROOT / "outputs/current_native_baseline_v57_20260909/profession_direct_damage_checkpoints/bbd41263be74e20b7a298d1ff8c94e5391d8b602e247054b3325177a7508831b.json"
ACCESS = ROOT / "outputs/chain_lightning_meter_v59_20260909/meter_local_vector_accesses.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def static_audit():
    view = v59.StaticView()
    ranges = dict(v59.STATIC_RANGES)
    ranges.update({
        "alternate_action_full": (0x37C280,0x37C460),
        "read_bytes_thunk": (0x22F260,0x22F265),
        "compressed_reader_code": (0x22F270,0x22F578),
        "type_12_action_branch": (0xA59220,0xA5950F),
        "set_intersection": (0x6A4C70,0x6A4F70),
        "actor_clear_channel_thunk": (0xA63650,0xA63661),
        "string_containment_helper": (0xDD0EE8,0xDD10E8),
    })
    # Full unwind-bounded functions, including lifecycle clear sites.
    for at in (0x378076, 0x3788DE, 0x379090, 0x376D50, 0x5286C0,
               0x38EB30, 0xA59A0C, 0x51E560, 0xB97910, 0xB98260,
               0x37C280, 0x376F00, 0x376E10, 0x374750, 0x22F260,
               0x22F270, 0xA566B0, 0x8E5440, 0x6A5160,
               0xA63650, 0xA4D4D0):
        interval = view.function(at)
        if interval:
            ranges[f"function_{interval[0]:x}"] = interval[:2]
    accesses = read(ACCESS)
    meter_contexts = {
        k: v for k, v in accesses["contexts"].items()
        if 0x690000 <= int(k, 16) < 0x6B0000
    }
    for key in meter_contexts:
        interval = view.function(int(key, 16))
        if interval:
            ranges[f"function_{interval[0]:x}"] = interval[:2]
    evidence = {k: v59._disassemble(view, *interval) for k, interval in ranges.items()}
    for key, rows in evidence.items():
        (OUT / f"static_{key}.txt").write_text(
            "\n".join(f"0x{r['rva']:08X}  {r['bytes']:<30} {r['instruction']}" for r in rows)+"\n",
            encoding="utf-8")
    # Exact direct-call destinations, checked at decoded instruction boundaries
    # inside candidate unwind functions. Numeric E8 bytes alone are not xrefs.
    targets = (0x5286C0, 0x6A6C60, 0x375940, 0x37C460, 0x378000)
    xrefs = defaultdict(list)
    base, data = view.segments[".text"]
    candidate_functions = set()
    import struct
    pos = data.find(b"\xe8")
    while pos >= 0:
        if pos + 5 <= len(data):
            dest = base + pos + 5 + struct.unpack_from("<i", data, pos + 1)[0]
            if dest in targets:
                interval = view.function(base + pos)
                if interval:
                    candidate_functions.add(interval[:2])
        pos = data.find(b"\xe8", pos + 1)
    for interval in sorted(candidate_functions):
        rows = v59._disassemble(view, *interval)
        for i, row in enumerate(rows):
            for target in targets:
                if row["instruction"] == f"call {hex(v59.BASE + target)}":
                    xrefs[hex(target)].append({"function": hex(interval[0]), "rva": hex(row["rva"]),
                                              "context": rows[max(0, i-10):i+8]})
    vtable = {hex(slot): hex(struct.unpack("<Q", view.read(0xE9E788+slot, 8))[0]-v59.BASE)
              for slot in (0x10, 0x30, 0x58, 0x80)}
    assert vtable == {"0x10":"0x375940", "0x30":"0x378a30", "0x58":"0x374750", "0x80":"0x37c460"}
    # Remote actor's ACTUAL secondary vtable, not just the MASkillUser base.
    remote_locator = struct.unpack("<Q",view.read(0xF24788-8,8))[0]-v59.BASE
    locator_fields = struct.unpack("<6I",view.read(remote_locator,24))
    assert remote_locator == 0x101FF70 and locator_fields[1] == 0x6A0
    type_name = view.read(locator_fields[3]+16,24).split(b"\0",1)[0].decode("ascii")
    assert type_name == ".?AVCDnPlayerActor@@"
    remote_slots = {hex(slot):hex(struct.unpack("<Q",view.read(0xF24788+slot,8))[0]-v59.BASE)
                    for slot in (0x10,0x58,0x80)}
    assert remote_slots == {"0x10":"0x375940","0x58":"0x374750","0x80":"0x37c460"}
    control_slots = {hex(slot):hex(struct.unpack("<Q",view.read(0xF24310+slot,8))[0]-v59.BASE)
                     for slot in (0x410,0x418,0x2E8)}
    assert control_slots == {"0x410":"0xa4a950","0x418":"0xa63650","0x2e8":"0xa4d4d0"}
    jump_targets = {hex(kind):hex(struct.unpack("<I",view.read(0xA5AC38+view.read(0xA5ACEC+kind-1,1)[0]*4,4))[0])
                    for kind in (0x0E,0x12,0x66,0x67,0x68)}
    assert jump_targets == {"0xe":"0xa59780","0x12":"0xa59220","0x66":"0xa5a801","0x67":"0xa5a884","0x68":"0xa5a8ce"}
    # Reader encoding tag 6 consumes four bytes, not six; tag 9 consumes nine.
    reader_tags = {str(tag):hex(struct.unpack("<I",view.read(0x22F57C+tag*4,4))[0]) for tag in (6,9)}
    assert reader_tags == {"6":"0x22f2d6","9":"0x22f2e6"}
    by_rva = {r["rva"]:r["instruction"] for rows in evidence.values() for r in rows}
    anchors = {0x375A92:"[rsi + 0x10], 0xffffffffffffffff",0x375ACC:"[rsi + 0x10], xmm0",
               0x37C416:"[rax + 0x10]",0x37C69B:"[rax + 0x10]",0x379738:"[rbx + 0x10], 0xffffffffffffffff",
               0x379740:"[rbx + 0x18], r15",0x38ECFA:"0x140379090",0x378076:"0xffffffffffffffff",
               0x3788DE:"0xffffffffffffffff",0x528B61:"0x140b97910",0x528C3B:"[rdx + 0x10]",
               0x528C4B:"0x1406a6c60",0x528C9E:"[r13 + 8], 0",0x528CCD:"[r13], 0xffffffffffffffff",
               0x6A6E47:"[r11 + rcx*8 + 0x10], r14d",0x6A7847:"0x1406a7130",
               0x6A7940:"[r12 + 0x660], -0x28",0x22F2D6:"r10d, 4",0x22F2E6:"r10d, 9",
               0xA63650:"[rcx + 0x1c58]",0xA6365A:"0x14038eb30"}
    for at, fragment in anchors.items():
        assert fragment in by_rva.get(at,""),(hex(at),by_rva.get(at))
    result = {"inputs": view.inputs, "ranges": evidence, "direct_call_xrefs": xrefs,
              "MASkillUser_vtable_0xE9E788": vtable,
              "remote_actor_secondary_vtable":{"type_name":type_name,"locator_rva":hex(remote_locator),
                  "locator_raw_hex":view.read(remote_locator,24).hex(),"subobject_offset":"0x6A0",
                  "vtable_rva":"0xF24788","slots":remote_slots,"primary_control_slots":control_slots},
              "network_dispatch_jump_targets":jump_targets,"reader_encoding_tags":reader_tags,
              "asserted_instructions":{hex(at):by_rva[at] for at in anchors},
              "meter_vector_context_functions": list(meter_contexts)}
    write("static_evidence.json", result)
    return result


def quantities(prior):
    native = read(v59.NATIVE_ROWS)
    cap = next(c for c in native["captures"] if c["capture_sha256"] == v59.CAPTURE_SHA256)
    rows = [r for r in cap["rows"] if r["source_actor_id"] == v59.FOCUS_ACTOR_ID and r["skill_id"] == 3019]
    checkpoint = read(CHECKPOINT)["report"]
    assert checkpoint["capture_sha256"] == v59.CAPTURE_SHA256
    sample = [r for r in checkpoint["details"] if r["job"] == "雷神" and r["skill_id"] == "3019"]
    baseline = read(v59.FIXED_BASELINE)
    fixed = [r for r in baseline["details"] if r["职业"] == "雷神" and str(r["skill_id"]) == "3019"]
    sc = Counter()
    fc = Counter()
    for r in sample:
        sc[r["classification"]] += r["hits"]
    for r in fixed:
        fc[r["归因分类"]] += r["Hit数"]
    assert (len(rows), sc["deterministic"], sc["nondeterministic"], fc["nondeterministic"]) == (1521, 820, 325, 842)
    return {
        "native_raw_rows": len(rows),
        "native_raw_by_method": dict(Counter(r["method"] for r in rows)),
        "sample_fixed35_included_hits": dict(sc), "sample_fixed35_details": sample,
        "fixed35_all_inquisitor_3019": dict(fc), "fixed35_details": fixed,
        "raw_to_sample_net_difference": len(rows)-sum(sc.values()),
        "other_captures_remaining_hits": fc["nondeterministic"]-sc["nondeterministic"],
        "policy_difference": "native sidecar: positive physical direct37/direct41 rows before CombatAggregator, no has_real_hit_evidence filter; fixed35: after CombatAggregator and _scope_reason, real positive outgoing direct Hits only",
        "difference_attribution_limit": "376 is a net label-count difference. Read event_quantities.json for exact pre/post-aggregation reconciliation; do not call these filtered-out Hits.",
        "input_hashes": {str(p): v59._digest(p) for p in (v59.NATIVE_ROWS, v59.FIXED_BASELINE, CHECKPOINT)},
    }


def object_identity_audit(hits):
    by_object = defaultdict(list)
    for hit in hits:
        obj = hit["damage_object_id"]
        if obj and obj != 0xFFFFFFFF and hit["family"] == "direct37":
            by_object[obj].append(hit)
    summaries = []
    for obj, rows in by_object.items():
        if not any(r["native_skill_id"] == 3019 for r in rows):
            continue
        independent = [r for r in rows if r["native_deterministic"]]
        seeds = [r for r in independent if r["native_skill_id"] == 3019]
        unknown = [r for r in rows if r["native_skill_id"] == 3019 and not r["native_deterministic"]]
        labels = sorted(set(r["native_skill_id"] for r in independent))
        summaries.append({"damage_object_id": obj, "rows": len(rows),
                          "independent_skill_ids": labels, "independent_3019_rows": len(seeds),
                          "inferred_3019_rows": len(unknown),
                          "inferred_3019_after_independent_same_id": sum(any(s["sequence"] < h["sequence"] for s in seeds) for h in unknown),
                          "first_sequence": rows[0]["sequence"], "last_sequence": rows[-1]["sequence"],
                          "evidence": [{k: h[k] for k in ("sequence", "frame", "elapsed_ms", "record_offset", "wire_record_sha256", "result_group_id", "native_skill_id", "native_method", "native_deterministic")} for h in rows]})
    return {"scope": "same capture/source actor/explicit direct37 damage_object_id only; numeric identity is a candidate until object generation/lifetime is independently bound",
            "objects": summaries,
            "counts": {"objects_containing_3019": len(summaries),
                       "inferred_3019_rows_with_independent_same_id": sum(s["inferred_3019_rows"] for s in summaries if s["independent_3019_rows"]),
                       "inferred_3019_after_independent_same_id": sum(s["inferred_3019_after_independent_same_id"] for s in summaries),
                       "objects_with_conflicting_independent_skills": sum(len(s["independent_skill_ids"]) > 1 for s in summaries)},
            "promotion_allowed": False}


def control_shadow(prior):
    cap = prior["capture_evidence"]
    events = [r for r in cap["focus_control_events"] if r["actor_id"] == v59.FOCUS_ACTOR_ID]
    counts = Counter((r["record_type"],r["operation"]) for r in events)
    actions = [r for r in events if r["operation"] == "ACTION"]
    samples = [r for r in actions if r["skill_id"] == 3019]
    return {"focus_control_counts": {"|".join(k):v for k,v in counts.items()},
            "action_3019_count": len(samples),
            "action_3019_registered_count": sum(r["registry_contains_skill"] for r in samples),
            "action_3019_variant_flags": {str(k):v for k,v in Counter((r["action_variant"],r["branch_flag"]) for r in samples).items()},
            "physical_counts": cap["counts"], "focus_control_events": events,
            "rejected_candidate": "Set actor candidate RowId on every 0x0E and clear when its recorded channel is removed; this is an observed action signal, NOT proven successful client START/REPLACE/CLEAR or meter memory",
            "candidate_conflicts": cap["state_candidate_truth_conflicts"],
            "native_same_label_candidate_rows": cap["state_candidate_recovered_3019"],
            "promotion_allowed": False}


def event_quantity_audit(prior):
    """One additional focused replay to expose the flags missing in v50 rows.

    No worker pool, no rule changes. Exact wire identity joins omit amount,
    intervals and inferred skill labels. Source code hashes detect audit drift.
    """
    from tools.audit_direct_damage_by_profession import (
        V576DiagnosticReplaySource, DATA_DIR, CombatEvent, CombatAggregator,
        _scope_reason, _strict_classification, _analysis_signature,
    )
    from dataclasses import asdict
    signature_before = _analysis_signature()
    source = V576DiagnosticReplaySource(v59.CAPTURE, DATA_DIR, {})
    before = source._decoder.feed_events
    physical = defaultdict(list)
    for h in prior["capture_evidence"]["focus_hits"]:
        physical[(h["elapsed_ms"], h["record_offset"], h["target_actor_id"], h["result_group_id"], h["damage_object_id"] or 0)].append(h)
    native_physical = {h["sequence"] for h in prior["capture_evidence"]["skill_3019_hits"]}
    raw = []

    def event_row(e):
        key = (e.timestamp_ms, e.wire_record_offset, e.wire_target_actor_id,
               e.wire_result_group_id, e.wire_damage_object_id)
        matches = physical.get(key, ())
        return {**asdict(e), "scope_reason":_scope_reason(e),
                "classification":_strict_classification(e),
                "physical_matches":len(matches),
                "physical_sequence":matches[0]["sequence"] if len(matches)==1 else None,
                "wire_record_sha256":matches[0]["wire_record_sha256"] if len(matches)==1 else None,
                "native_v50_skill_id":matches[0]["native_skill_id"] if len(matches)==1 else None}

    def feed(packet):
        events = before(packet)
        for e in events:
            if isinstance(e, CombatEvent) and e.wire_source_actor_id == v59.FOCUS_ACTOR_ID:
                row = event_row(e)
                if str(e.skill_id) == "3019" or row["physical_sequence"] in native_physical:
                    raw.append(row)
        return events

    source._decoder.feed_events = feed
    try:
        source_events = source.poll()
    finally:
        source._decoder.feed_events = before
        source.close()
    aggregator = CombatAggregator()
    for e in source_events:
        aggregator.ingest(e)
    stages = {}
    for name, events in (("source_poll",source_events), ("aggregated",aggregator.snapshot(include_events=True).combat_events)):
        selected=[]
        for e in events:
            if isinstance(e, CombatEvent) and e.wire_source_actor_id == v59.FOCUS_ACTOR_ID:
                row=event_row(e)
                if str(e.skill_id)=="3019" or row["physical_sequence"] in native_physical:
                    selected.append(row)
        stages[name]=selected
    stages["raw_feed"]=raw
    def summarise(rows):
        return {"rows":len(rows),
                "by_skill_method_scope": {"|".join(k):v for k,v in Counter((str(r["skill_id"]),r["attribution_method"],r["scope_reason"]) for r in rows).items()},
                "physical_match_counts": dict(Counter(r["physical_matches"] for r in rows)),
                "native3019_match_rows": sum(r["physical_sequence"] in native_physical for r in rows)}
    summaries={k:summarise(v) for k,v in stages.items()}
    signature_after = _analysis_signature()
    if signature_before != signature_after:
        raise ValueError("production inputs changed during single-capture audit; do not compare mixed versions")
    report={"schema":"3019-event-quantity-audit-v60", "capture_sha256":v59._digest(v59.CAPTURE),
            "analysis_signature":signature_before,
            "baseline_analysis_signature_equal":signature_before==read(v59.FIXED_BASELINE)["analysis_signature"],
            "summary":summaries, "stages":stages}
    write("event_quantities.json",report)
    print(json.dumps(summaries,ensure_ascii=False),flush=True)


def parse_action_candidate(event):
    """Exact fields of the 31-byte client 0E body, with opaque extra bytes.

    Encoding tag 6 reads FOUR wire bytes (22F2D6); tag 9 reads nine.
    Only a request is emitted: no invented successful runtime transition.
    """
    import struct
    body = struct.pack("<IBB",event["skill_id"],event["action_variant"],event["branch_flag"])+bytes.fromhex(event["tail_hex"])
    assert len(body)==event["declared_length"] and len(body) in (31,35)
    return {k:event[k] for k in ("sequence","frame","record_index","elapsed_ms","wire_record_sha256")} | {
        "payload_hex":body.hex(), "body_bytes_consumed_by_handler":31,
        "skill_row_id_u32_p0":struct.unpack_from("<I",body)[0],
        "skill_property_298_u8_p4":body[4],"alternate_branch_u8_p5":body[5],
        "raw_3bytes_p6":body[6:9].hex(),
        "compressed_tag6_p9":body[9:13].hex(),"compressed_tag6_p13":body[13:17].hex(),
        "activation_r9_flag_u8_p17":body[17],
        "conditional_additional_row_id_u32_p18":struct.unpack_from("<I",body,18)[0],
        "compressed_tag9_p22":body[22:31].hex(),"unconsumed_trailer_hex":body[31:].hex(),
        "dispatch":"0x37C280(mode=2) -> virtual slot+0x10" if body[5] else "0x376F00 / optional slot+0x58 -> slot+0x80=0x37C460 -> slot+0x10=0x375940",
        "shadow_operation":"START_OR_REPLACE_REQUEST", "successful_start_or_replace":None,
        "current_action_pair":None,"meter_remembered_row_id":None,
        "reason":"runtime skill registry, valid handle, activation guards and action-name transition not observed in wire record",
        "deterministic_attribution_allowed":False,
    }


def final_report(prior, static, q, control, objects):
    events = read(OUT/"event_quantities.json")
    assert events["capture_sha256"] == v59._digest(v59.CAPTURE) == v59.CAPTURE_SHA256
    physical = {h["sequence"]:h for h in prior["capture_evidence"]["focus_hits"]}
    native_ids = {h["sequence"] for h in prior["capture_evidence"]["skill_3019_hits"]}
    raw = {r["physical_sequence"]:r for r in events["stages"]["raw_feed"] if r["physical_sequence"] in native_ids}
    final = {r["physical_sequence"]:r for r in events["stages"]["aggregated"] if r["physical_sequence"] in native_ids}
    assert set(raw)==set(final)==native_ids and len(raw)==1521
    assert all(r["has_real_hit_evidence"] is True and r["scope_reason"]=="included_positive_direct_hit" for r in list(raw.values())+list(final.values()))
    renamed = []
    for seq, row in final.items():
        if str(row["skill_id"]) != "3019":
            assert row["attribution_method"]=="cast_instance_match"
            renamed.append({"sequence":seq,"record_sha256":row["wire_record_sha256"],
                            "before_skill_id":raw[seq]["skill_id"],"before_method":raw[seq]["attribution_method"],
                            "after_skill_id":row["skill_id"],"after_method":row["attribution_method"],
                            "classification":row["classification"],"has_real_hit_evidence":True})
    gaps=[r for r in final.values() if str(r["skill_id"])=="3019" and r["classification"]=="nondeterministic"]
    assert len(renamed)==376 and len(gaps)==325
    actual=Counter()
    expected=Counter()
    for r in final.values():
        if str(r["skill_id"])=="3019":
            actual[(r["classification"],"hits")]+=1
            actual[(r["classification"],"damage")]+=r["amount"]
    for r in q["sample_fixed35_details"]:
        expected[(r["classification"],"hits")]+=r["hits"]
        expected[(r["classification"],"damage")]+=r["damage"]
    assert actual==expected,(actual,expected)
    by_obj={s["damage_object_id"]:s for s in objects["objects"]}
    buckets=Counter()
    seeded=[]
    gap_rows=[]
    for r in gaps:
        h=physical[r["physical_sequence"]]
        obj=by_obj[h["damage_object_id"]]
        independent=obj["independent_skill_ids"]
        before_seeds=[x for x in obj["evidence"] if x["native_skill_id"]==3019 and x["native_deterministic"] and x["sequence"]<h["sequence"]]
        group="same_id_independent_3019" if 3019 in independent else "same_id_other_independent_skill" if independent else "same_id_no_independent_label"
        buckets[group]+=1
        entry={"physical":h,"final_method":r["attribution_method"],"final_classification":r["classification"],
               "has_real_hit_evidence":r["has_real_hit_evidence"],"same_id_group":group,
               "same_id_independent_skill_ids":independent,"prior_independent_3019_rows":len(before_seeds),
               "promotion_allowed":False}
        gap_rows.append(entry)
        if group=="same_id_independent_3019":
            seeded.append(entry|{"all_same_id_evidence":obj["evidence"],"generation_identity_proved":False})
    requests=[parse_action_candidate(r) for r in control["focus_control_events"] if r["operation"]=="ACTION" and r["skill_id"]==3019]
    assert len(requests)==93
    assert all(r["skill_property_298_u8_p4"]==21 and r["alternate_branch_u8_p5"]==0 and r["activation_r9_flag_u8_p17"]==0 and r["conditional_additional_row_id_u32_p18"]==0 for r in requests)
    assert sum(bool(r["unconsumed_trailer_hex"]) for r in requests)==84
    b=read(v59.FIXED_BASELINE)["analysis_signature"]
    a=events["analysis_signature"]
    changed=[k for k in sorted(set(a["files"])|set(b["files"])) if a["files"].get(k)!=b["files"].get(k)]
    q.update({"reconciliation":"1521 -> all 1521 physical Hits survive aggregation; 820 stay deterministic 3019, 325 stay nondeterministic 3019, 376 are relabelled to other skill IDs by cast_instance_match",
              "removed_native_physical_rows":0,"relabelled_native_physical_rows":376,
              "relabelled_before_methods":dict(Counter(r["before_method"] for r in renamed)),
              "relabelled_after_skills":dict(Counter(r["after_skill_id"] for r in renamed)),
              "relabelled_rows_remain_nondeterministic":all(r["classification"]=="nondeterministic" for r in renamed),
              "sample_fixed35_counts_and_damage_reproduced":True,
              "baseline_analysis_signature_equal":a==b,
              "baseline_analysis_signature_sha256":b["sha256"],"current_analysis_signature_sha256":a["sha256"],
              "baseline_signature_changed_files":changed,
              "difference_attribution_limit":"Exact 376-row reconciliation proved for this single-capture replay; baseline runtime signature differs, although 3019 Hit and damage totals exactly reproduce the saved checkpoint. Do not extrapolate to other captures."})
    write("quantities.json",q)
    write("parser_candidates.json",{"schema":"3019-action-request-parser-v60","requests":requests,
                                    "row_id_is_not_damage_object_id":True,"committed_client_states":0})
    write("quantity_relabels_376.json",renamed)
    write("remaining_325.json",{"counts":dict(buckets),"rows":gap_rows})
    write("same_id_candidates_13.json",seeded)
    verdict={"static_current_action_writer_closed":True,"static_meter_writer_closed":True,
             "complete_network_START_REPLACE_CLEAR_reconstruction_closed":False,
             "remote_projectile_skill_inheritance_proved_by_this_audit":False,
             "deterministic_promotions":0,"remaining_fixed35_3019_hits":842,
             "candidate_use":"parser/evidence only; failed to qualify for production attribution"}
    contracts={
        "0x0E": "payload+0 u32 RowId, +4 u8 compared to Skill+0x298, +5 branch flag. Normal path 0x376F00 checks state skill registry; missing/different entry uses 0x376E10 and slot+0x58=0x374750. On success slot+0x80=0x37C460 finds a valid skill pair via 0x376AA0, passes runtime activation checks, then calls slot+0x10=0x375940. Alternate +5!=0 uses 0x37C280 to create/find a pair and invokes the same setter at 0x37C416.",
        "START_REPLACE": "Remote Actor+0x6A0 vtable 0xF24788, RTTI CDnPlayerActor locator 0x101FF70. Setter 0x375940 may return before writing if state+0x120 is absent and +0x128 is false. A valid existing pair is ended (unless action+0x38A suppresses the end call), cleared at 0x375A92/+0x375A9A; input handle/pointer pair is copied to state+0x10/+0x18 at 0x375AC7/+0x375ACC. START versus REPLACE depends on actual old pair, not previous observed RowId.",
        "0x12": "Separate handler 0xA59220; slot+0x58 updates skill state, then Actor slot+0x2E8=0xA4D4D0 handles an action-name-based route at 0xA59502. It is not equivalent to a direct 0x0E activation.",
        "0x66_67_68": "0x66 body u32 channel/u32 skill/u8 flag; Actor slot+0x410=0xA4A950 resolves a skill pair and passes it to manager 0x38D3F0. 0x68 repeats channel/skill pairs. 0x67 body u32 channel -> Actor slot+0x418=0xA63650 -> 0x38EB30. It looks up the channel's valid skill pair, gets RowId at 0x38ECEE, calls 0x379090 via Actor+0x6A0 at 0x38ECFA, then invalidates manager node+0x28/+0x30 at 0x38ED37/+0x38ED3F. No records of these three types were present anywhere in the uniquely framed sample.",
        "CLEAR_constraints": "0x379090 is not an unconditional current-skill clear: observed current-pair writes at 0x379738/+0x379740 require the selected skill subtype+0x238==5 branch, matching stored pair+0x40/+0x48 and requested RowId, action+0x38A!=0, and equality with the current pair. Separate update function 0x377F70 clears at 0x378076/+0x37807E after 0xB967C0 and termination, or at 0x3788DE/+0x3788E8 when a queued pair from state+0x130/+0x138 equals current. Captured channel IDs alone cannot replay these runtime branches.",
        "meter_write": "0x5286C0 runs an actual action-name transition (known direct callers 0x8E5530 and 0xA567CD). It checks the incoming name differs from current actor action name, validates current action pair at Actor+0x6B0/+0x6B8, tests action-name-set compatibility through 0xB97910, then requires (action+0x5A8!=0 or action+0x98==1) and action+0x238==0 plus meter UI instance. 0x528C3B gets RowId from that current action+0x20/slot+0x10; 0x528C4B calls 0x6A6C60 with Actor handle pair and a copy of action+0x5F0 string set. This captures the action present at that transition, not the last incoming network RowId.",
        "meter_vector": "0x6A6C60 validates actor handle, keys the vector at meter+0x658/+0x660 by full 16-byte handle/pointer pair, stride 0x28: pair+0/+8, RowId+0x10, saved action-name set+0x18. It appends or updates the existing RowId at 0x6A6E47. Reader 0x6A5717 uses the same full actor pair if earlier result/object key is 0 or -1. No damage_object_id index exists in this vector.",
        "meter_clear": "0x6A7450 compares each valid actor's current action name with its saved name set using 0x6A7130 (set intersection, then string containment fallback). False goes through exact actor-pair lookup and erase, shrinking vector end by 0x28 at 0x6A7940. Invalid actor handles are skipped at 0x6A7500/0x6A7513, not erased by that branch. Constructor zeros the vector; destructor 0x6A52B1 calls 0x6A5160 to release it. No direct network 0x67->meter erase was established.",
        "scope_boundary": "Recorder is black queen and focus actor 1314843433 is remote, per parent audit. Parent's 0x378E83 root Projectile current-skill copy is local-player-only; this audit neither assumes that copy for teammates nor analyzes ChargeVolt child creation. Successful remote current-action updates and remote Projectile key inheritance are distinct propositions.",
    }
    report={"schema":"3019-action-meter-shadow-v60","verdict":verdict,"static_contracts":contracts,
            "quantities":q,"request_count":len(requests),"remaining_325_identity_buckets":dict(buckets),
            "same_id_13_with_prior_seed":sum(r["prior_independent_3019_rows"]>0 for r in seeded),
            "rejected_action_candidate_counts":control["physical_counts"],
            "scope":"one 0821 capture, actor 1314843433, skill 3019; other labels only as contradiction/reconciliation evidence",
            "production_changed":False,"captures_replayed_by_own_processes_concurrently":1,
            "input_hashes":{str(p):v59._digest(p) for p in (v59.CLIENT,v59.CAPTURE,v59.NATIVE_ROWS,PRIOR,CHECKPOINT,ACCESS,OUT/"event_quantities.json")},
            "static_evidence_file":"static_evidence.json","runtime_client_executed":False}
    report["physical_row_accounting"]={
        "focus_actor_physical_rows":len(physical),
        "focus_actor_uniquely_joined_native_rows":sum(h["native_match_count"]==1 for h in physical.values()),
        "physical_rows_absent_from_native_sidecar":sum(h["native_match_count"]==0 for h in physical.values()),
        "native_rows_without_physical_match":prior["capture_evidence"]["counts"]["focus_actor_native_rows_unmatched"],
    }
    report["conflict_interpretation"]="525 independent-label conflicts reject using action state as a blanket damage attribution rule. They do not disprove that the real current action could be 3019 while an older projectile deals damage; the client's result/object-key priority must still be respected."
    write("REPORT.json",report)
    text_lines=[
        "# 雷神 3019：动作控制 → meter 影子审计 v60", "",
        "结论：当前动作对 writer 与 meter 记忆表 writer 的静态路径已闭合；网络 START/REPLACE/CLEAR 的等价重建尚未闭合。新增确定性 Hit = 0，fixed35 的 842 缺口保持不变。", "",
        "## 数量口径（本轮最重要的修正）", "",
        "1521 是原生 sidecar 的 3019 物理记录行：820 `damage_object_projectile_link` + 674 `continuation_window` + 27 `learned_cast_profile`。逐行核对证实这些行全部有真实 Hit 标志，聚合后全部保留。",
        "聚合器把其中 349 条 continuation_window 和全部 27 条 learned_cast_profile 改为其他技能的 `cast_instance_match`，合计 376 行，仍属非确定性。这里是标签变化，不是伤害/Hit 丢失，也不是按真实 Hit 标志过滤了 376 行。",
        "因此 0821 的最终 3019 = 1521 − 376 = 1145 = 820 确定 + 325 非确定；全部 fixed35 雷神/3019 = 2703 确定 + 842 非确定，其中其他抓包缺口为 842 − 325 = 517。", "",
        "325 明细：未知模板 299、模板63为11、4011为3、4018为6、4024为6。对象模板是既有 checkpoint 的诊断标签，不独立证明技能。",
        "本次单份重放的 3019 Hit 数与伤害总额完全重现 checkpoint；当前生产输入签名与 v57 不同（5 个文件，见 quantities.json），故不声称使用了完全相同的生产版本。原始行前后变化按时间戳、记录偏移、源/目标、结果组和对象 ID 的精确字段连接；每条匹配唯一并保留原包 SHA256，不使用伤害值或时间阈值。", "",
        "## 解析候选与实包否证", "",
        "捕获到该角色 1498 条 0x0E、31 条 0x12；全样本唯一完整记录中均未出现 0x66/67/68。3019 的 0x0E 有 93 条：9 条正文31字节、84条正文35字节。+0=3019、+4=21（和 action+0x298 比较的值）、+5=0、+17=0、+18 u32=0。",
        "0x22F270 的编码 tag6 消耗4字节，tag9消耗9字节。因此 handler 正文消耗31字节；35字节形状的末4字节原样保留为未解释尾部。parser_candidates.json 提供93条精确字段、帧号、序号与包记录哈希，输出仅为 START_OR_REPLACE_REQUEST；不伪造动作句柄、成功状态或 meter 值。",
        "v59 旧候选仅在0x0E时留住 RowId：共选中1387条物理行，独立标签1012条中匹配487、冲突525；覆盖325缺口中的316条也不足以提升确定性。首个冲突：frame4037 / sequence5217 / object100115，已观察到3019请求，但独立对象链为3425。见 control_shadow.json。", "",
        "这525条冲突否定的是直接拿候选动作状态给全部伤害命名；它们不否定客户端当时可能正在3019动作中。旧投射物仍可造成其他技能伤害，meter 原本就优先读 result/object key。物理扫描共17163条该角色记录，其中16636条唯一对应 native sidecar、527条不在 sidecar；sidecar 未匹配数为0。上述1387与525采用物理行口径，未冒充最终Hit统计。", "",
        "## 同 damage_object_id 关系", "",
        "最终325缺口：13行同ID有独立3019标签（10行在种子之后、3行在种子之前）；10行同ID有其他独立技能标签；302行无独立同ID标签。13行见 same_id_candidates_13.json，10条反例及其余行见 remaining_325.json。",
        "全原始标签中有17行同ID见过独立3019，严格收紧至最终325后只剩13。数字ID不能独立证明同一对象代次，尚缺出生/销毁/重用及实际初始或弹跳语义；本轮不把种子向后传播或把未来种子倒灌为真值。", "",
        "## 已核验的处理器和清除规则", "",
    ]
    for key,value in contracts.items():
        text_lines.extend([f"- `{key}`：{value}", ""])
    text_lines.extend(["## 复现", "", "从项目根目录按顺序运行（单进程）：", "", "```powershell",
        "& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B -X utf8 tools/trace_chain_lightning_meter_v59.py",
        "& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B -X utf8 tools/trace_chain_lightning_action_meter_v60.py --events-only",
        "& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B -X utf8 tools/trace_chain_lightning_action_meter_v60.py",
        "```", "", "只读取该抓包、原生 sidecar、已有 checkpoint 和固定哈希客户端。静态依据保存为 static_evidence.json 与逐函数 TXT；数据阶段证据为 event_quantities.json。未修改生产代码、原始抓包或 v59 输出；未运行整35份。"])
    (OUT/"REPORT.md").write_text("\n".join(text_lines)+"\n",encoding="utf-8")
    write("reproduction_manifest.json",{"tools":{str(p):v59._digest(p) for p in (Path(__file__),ROOT/"tools/trace_chain_lightning_meter_v59.py")},
        "outputs":{p.name:v59._digest(p) for p in sorted(OUT.iterdir()) if p.is_file() and p.name!="reproduction_manifest.json"},
        "scope":"only two owned tools and this v60 output directory; no production writes"})
    print(json.dumps({"report":str(OUT/"REPORT.md"),"verdict":verdict,"buckets":dict(buckets)},ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--events-only", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.events_only:
        event_quantity_audit(read(PRIOR))
        return
    s = static_audit()
    print(json.dumps({"static_ranges": len(s["ranges"]), "xrefs": {k:[x["rva"] for x in v] for k,v in s["direct_call_xrefs"].items()}, "meter_functions":s["meter_vector_context_functions"]}), flush=True)
    if args.static_only:
        return
    prior = read(PRIOR)
    assert prior["inputs"]["native_rows_sha256"] == v59._digest(v59.NATIVE_ROWS)
    q = quantities(prior)
    c = control_shadow(prior)
    o = object_identity_audit(prior["capture_evidence"]["focus_hits"])
    write("quantities.json", q)
    write("control_shadow.json", c)
    write("same_object_identity.json", o)
    print(json.dumps({"quantities": {k:v for k,v in q.items() if k in ("native_raw_rows", "sample_fixed35_included_hits", "fixed35_all_inquisitor_3019", "raw_to_sample_net_difference", "other_captures_remaining_hits")}, "control_counts": c["focus_control_counts"], "object_counts": o["counts"]}, ensure_ascii=False))
    final_report(prior,s,q,c,o)


if __name__ == "__main__":
    main()
