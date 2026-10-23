"""Inspect saved decoded fragments, without fabricating wire or root evidence.

No capture reads, runtime imports, replay, or candidate edits. This deliberately
cannot approve a protocol patch unless complete raw root/parent/child/Hit evidence
is available. A stored XOR phase is not an independently rechecked unique phase.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/damage_attribution/branch0_cached_structure_20260907"
SERVER = ROOT / "outputs/audit_remaining_registration_server_20260907_v2"
STATIC = ROOT / "outputs/audit_remaining_registration_3021_context_20260907/act_dnt_evidence.json"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def hashes():
    paths = list((ROOT / "outputs/damage_attribution_candidate_v21_20260907").glob("*.py"))
    paths += list((ROOT / "outputs/damage_attribution_candidate_v22_20260907").glob("*.py"))
    paths += [ROOT / "dragon_nest_builder/live_dps/protocol_v576.py"]
    paths += list((ROOT / "dragon_nest_builder/live_dps").glob("*udp_direction*.py"))
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def fragment(r):
    b = bytes.fromhex(r["context_hex"])
    m = r["m"] - r["context_start"]
    def u(at):
        assert 0 <= at <= len(b) - 4
        return int.from_bytes(b[at:at + 4], "little")
    assert m >= 4 and m + 101 <= len(b)
    fields = dict(owner=u(m-4), object_id=u(m+8), template=u(m+12),
                  repeated_owner=u(m+44), parent_id=u(m+49),
                  branch_low16=u(m+53) & 0xffff, sequence_high16=u(m+53) >> 16,
                  suffix_m69=u(m+69))
    assert (fields["owner"], fields["object_id"], fields["template"]) == (r["owner"], r["obj"], 2023)
    guards = dict(marker_0962=b[m:m+3] == b"\x09\x62\0",
                  marker_0966=b[m:m+3] == b"\x09\x66\0",
                  five_zero=b[m+3:m+8] == bytes(5),
                  owner_repeat=fields["owner"] == fields["repeated_owner"] != 0,
                  zero_m48=b[m+48] == 0,
                  parent_range=0 < fields["parent_id"] <= 0xffffff,
                  parent_not_self=fields["parent_id"] != fields["object_id"],
                  branch0=fields["branch_low16"] == 0,
                  eight_ff=b[m+57:m+65] == b"\xff" * 8,
                  bounded_suffix=0 < fields["suffix_m69"] < 0xffff)
    fields["fragment_guards"] = guards
    fields["candidate_fields_valid"] = (guards["marker_0962"] or guards["marker_0966"]) and all(
        v for k,v in guards.items() if k not in ("marker_0962", "marker_0966"))
    return fields


def compact(r):
    return {k:r[k] for k in ("p", "m", "t", "owner", "obj", "template", "marker", "phase",
                             "length", "context_start", "context_hex", "signature")}


def main():
    before = hashes()
    static = read(STATIC)
    roots = static["root_actions"]
    assert [s["template"] for s in roots["1352"]["signal15"]] == [2022]
    assert [s["template"] for s in roots["1353"]["signal15"]] == [2025]
    pa = static["projectile_acts"]["2022"]
    child_edges = [s for s in pa["signals"] if s["type"] == 15 and s["u32"][0] == 2023]
    assert child_edges
    # Check the underlying resources have not drifted since the static audit.
    checked_static = {}
    for key in ("1352", "1353"):
        p = Path(static["resource_root"]) / roots[key]["file"]
        assert sha(p) == roots[key]["file_sha256"]
        checked_static[str(p)] = sha(p)
    for key in ("2022", "2023", "2025"):
        act = static["projectile_acts"][key]
        assert sha(Path(act["act_file"])) == act["act_sha256"]
        checked_static[act["act_file"]] = act["act_sha256"]
    reports, conflicts, requests = [], [], []
    for name in ("0825经典圣徒", "0808星期天黑典翻车"):
        path = SERVER / (name + ".json")
        d = read(path)
        rows = sorted(d["strict_instances"], key=lambda r:(r["p"], r["m"]))
        packet_rows = defaultdict(list)
        for r in rows: packet_rows[r["p"]].append(r)
        latest = {}
        count = Counter()
        shapes, negatives = Counter(), []
        for r in rows:
            if r["template"] == 2023:
                f = fragment(r)
                parent = latest.get((f["owner"], f["parent_id"]))
                typed = bool(parent and parent["template"] == 2022)
                age = r["t"] - parent["t"] if parent else None
                count["server2023_records"] += 1
                count["field_guards_pass"] += f["candidate_fields_valid"]
                count["field_guards_full_parent_typed_age_0_8"] += bool(
                    f["candidate_fields_valid"] and typed and 0 <= age <= 8)
                count["0966_records"] += r["marker"] == "09 66 00"
                count["0966_field_guards_pass"] += bool(r["marker"] == "09 66 00" and f["candidate_fields_valid"])
                count["full_original_wire_available"] += bool(r.get("wire_hex") or r.get("payload_hex"))
                shapes[f'{r["marker"]}/L{r["length"]}/M{r["m"]}'] += 1
                if (not f["candidate_fields_valid"] or not typed) and len(negatives) < 5:
                    negatives.append(dict(record=compact(r), fields=f,
                                          parent_template=parent["template"] if parent else None,
                                          rejection="fragment guard failure" if not f["candidate_fields_valid"] else "no exact typed parent in saved server table"))
                if r["fixed_slot_anchors"]:
                    # These fixed values are CONFLICT CONTEXT, never independent
                    # skill truth, and are not used to choose a parent.
                    neighbors = packet_rows[r["p"]]
                    expected_offsets = (18,123) if r["length"] == 224 else ((18,) if r["length"] == 119 else ())
                    recorded_boundaries = bool(expected_offsets and all(any(
                        n["m"] == m and n["marker"] == "09 62 00" and n["length"] == r["length"]
                        for n in neighbors) for m in expected_offsets))
                    item = dict(capture=name, capture_sha256=d["capture_sha256"],
                                child=compact(r), fields=f, parent=compact(parent) if parent else None,
                                typed_parent_match=typed, parent_age_seconds=age,
                                cached_hit_observations=r["anchor_examples"],
                                raw_fixed_context_not_truth=r["fixed_slot_anchors"],
                                declared_frame_boundaries_covered=recorded_boundaries,
                                missing=["complete original root 24 04 wire", "complete original parent/child/Hit wire",
                                         "13-phase independent uniqueness", "all client/server current-generation lifecycle between anchors"],
                                qualified_root_parent_child_hit_chain=False)
                    conflicts.append(item)
                    requests.append(dict(capture=name, child_logical_packet=r["p"], child_marker=r["m"],
                                         parent_logical_packet=parent["p"] if parent else None,
                                         child_time=r["t"], parent_time=parent["t"] if parent else None,
                                         hit_logical_packets=[h["p"] for h in r["anchor_examples"]],
                                         owner=r["owner"], parent_id=f["parent_id"], child_id=r["obj"]))
            latest[(r["owner"],r["obj"])] = r
        reports.append(dict(capture=name, source_report=str(path.relative_to(ROOT)), sha256=sha(path),
                            counts=dict(count), shapes=dict(shapes), real_fragment_counterexamples=negatives))
    assert len(conflicts) == 14
    groups = defaultdict(list)
    for c in conflicts: groups[(c["capture"],c["fields"]["owner"],c["fields"]["parent_id"])].append(c)
    result = dict(scope="saved decoded fragments only; no capture reads or production imports",
                  reports=reports, static_source_sha256=sha(STATIC), checked_static=checked_static,
                  static_roots={k:roots[k] for k in ("1352","1353")},
                  static_2022_rowid_to_actname=pa["weapon_row"]["_ActName"],
                  static_2023_rowid_to_actname=static["projectile_acts"]["2023"]["weapon_row"]["_ActName"],
                  static_typed2022_to2023=child_edges,
                  conflict_records=conflicts, cached_conflict_count=14,
                  cached_distinct_parent_keys=len(groups),
                  cached_conflict_damage_sum=sum(h["damage"] for c in conflicts for h in c["cached_hit_observations"]),
                  verified_root_parent_child_hit_chains=0, protocol_patch_generated=False,
                  reason="Stored decoded fragments and a stored phase cannot substitute for original wire, current generations and a real root action.",
                  anchor_requests=requests, protected_before=before, protected_after=hashes())
    result["protected_unchanged"] = result["protected_before"] == result["protected_after"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cached_evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("cached_conflict_count", "cached_distinct_parent_keys", "cached_conflict_damage_sum",
        "verified_root_parent_child_hit_chains", "protocol_patch_generated", "protected_unchanged")}, ensure_ascii=False))
    for key, cs in groups.items():
        print(key, "parent p", cs[0]["parent"]["p"], "child", [c["child"]["p"] for c in cs],
              "Hit", [h["p"] for c in cs for h in c["cached_hit_observations"]])


if __name__ == "__main__":
    main()
