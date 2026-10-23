"""Package saved v20 observations. No capture access, decoder import, or replay."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "outputs/audit_remaining_registration_3021_context_20260907"
OUT = ROOT / "outputs/audit_remaining_registration_source_v20_20260907"
OWNER = 1337770664


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    fixtures = load(RAW / "fixtures_final.json")
    full = load(OUT / "trace.json")
    cases = []
    selected_records = {}
    for child, parent, epoch in ((102046, 101918, 1801), (102941, 102809, 1904)):
        proof = next(p for p in fixtures["final_parent_evidence"] if p["child_obj"] == child)
        hit_time = proof["physical"]["capture_seconds"]
        casts = [c for c in fixtures["expected_casts"]
                 if hit_time - 8 < c["capture_seconds"] < hit_time]
        assert {c["skill_id"] for c in casts} == {2353, 2207}
        ids = {proof[k] for k in ("parent_fixture", "child_fixture", "hit_fixture")}
        ids.update(c["fixture"] for c in casts)
        for key in ids:
            record = fixtures["records"][key]
            assert hashlib.sha256(bytes.fromhex(record["payload_hex"])).hexdigest() == record["payload_sha256"]
            assert len(bytes.fromhex(record["payload_hex"])) == record["length"]
            selected_records[key] = record
        registrations = [r for r in full["trace"] if r["kind"] == "ledger.observe_registration"
                         and r["kwargs"].get("source_actor_id") == OWNER
                         and r["kwargs"].get("damage_object_id") in (parent, child)
                         and abs(r["packet"]["capture_seconds"] - hit_time) < 8]
        child_registration = next(r for r in registrations if r["kwargs"]["damage_object_id"] == child)
        origin = child_registration["result"]
        assert origin["parent_lineage_exact"] is True
        assert origin["parent_damage_object_id"] == parent
        assert origin["action_epoch_id"] == epoch
        output = [dict(packet=r["packet"], event=e) for r in full["trace"]
                  if r["kind"] == "source.output_events" for e in r["events"]
                  if e.get("wire_damage_object_id") == child and e.get("source_id") == "0x4FBCC2A8"]
        assert len(output) == 1
        event = output[0]["event"]
        assert event["skill_id"] == "2353" and event["amount"] == proof["damage"]
        assert event["raw_attribution_method"] == "packet_skill_context:2207"
        cases.append(dict(child_object_id=child, parent_object_id=parent,
                          raw_parent_evidence=proof, real_client_actions=casts,
                          full_source_registrations=registrations, full_source_output=output,
                          runtime_action_epoch=epoch,
                          caveat="Raw audit epoch numbers are owner-filtered; runtime epoch numbers are global. Neither is a wire field. This packet subset does not reproduce prior full-capture generations by itself."))
    compact = dict(capture=fixtures["capture"], capture_sha256=fixtures["capture_sha256"],
                   inputs_sha256={"fixtures_final.json": digest(RAW / "fixtures_final.json"),
                                  "trace.json": digest(OUT / "trace.json")},
                   runtime=full["runtime"], runtime_sha256=full["before_hashes"],
                   runtime_unchanged=full["runtime_unchanged"], cases=cases,
                   records=selected_records,
                   hook_scope={"trace_rows": len(full["trace"]),
                               "kinds": dict(Counter(r["kind"] for r in full["trace"])),
                               "limitations": ["attribute_direct37 result hook did not match positional payload-derived object IDs; zero recorded calls is not evidence of no calls.",
                                               "Output filtering also retained one different owner's object102809; excluded from these cases.",
                                               "No watched invalidate_origin calls occurred in the two trace windows; not a whole-capture claim."]})
    target = OUT / "two_real_action_fixtures.json"
    if target.exists():
        raise SystemExit("Refusing to overwrite handoff fixtures")
    target.write_text(json.dumps(compact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(cases), "records": len(selected_records),
                      "sha256": digest(target), "validated": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
