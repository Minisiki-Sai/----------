"""Four real production fixtures; other cases are explicitly synthetic mutations."""
from copy import deepcopy
import json

import pytest

from tools import audit_selfattack_ab_v26c as audit

MUTATIONS = json.loads((audit.ROOT / "tests/data/summon_attack_v26_boundary_mutations.json").read_text(encoding="utf8"))


@pytest.fixture(scope="module")
def report():
    runtime = audit.load_runtime()
    value = audit.fixture_run(runtime)
    assert value["passed"] and value["captures_replayed"] == 0
    yield value
    audit.verify_runtime(runtime[3])


def test_rule_data_inventory_and_content_are_both_tracked(tmp_path):
    folder = tmp_path / "modular"
    folder.mkdir()
    first = folder / "a.json"
    first.write_text('{"value":1}', encoding="utf8")
    baseline = audit.rule_data_hashes(tmp_path)
    first.write_text('{"value":2}', encoding="utf8")
    assert audit.rule_data_hashes(tmp_path) != baseline
    first.write_text('{"value":1}', encoding="utf8")
    assert audit.rule_data_hashes(tmp_path) == baseline
    (folder / "new.json").write_text('{}', encoding="utf8")
    assert audit.rule_data_hashes(tmp_path) != baseline


def test_rule_data_ignores_non_rule_output(tmp_path):
    folder = tmp_path / "modular"
    folder.mkdir()
    (folder / "notes.txt").write_text('not a rule input', encoding="utf8")
    assert audit.rule_data_hashes(tmp_path) == {}


def first_change(report, case=0):
    return deepcopy(report["results"][case]["changes"][0])


def classify(change):
    return audit.classify_message(change["message"], change["before"], change["after"])


def summarize(change):
    summary = audit.Summary()
    summary.add(change)
    return summary.result()


@pytest.mark.parametrize("index,duplicates,amount", [(0, 1, 8908), (1, 1, 286880), (2, 0, 0), (3, 1, 97920)])
def test_real_fixtures_use_frozen_runtime_and_exact_record_keys(report, index, duplicates, amount):
    case = report["results"][index]
    summary = case["summary"]
    assert summary["counts"]["existing_hit_skill_or_owner_upgraded"] == 1
    assert summary["counts"]["proven_duplicate_removed"] == duplicates
    assert summary["counts"]["target_corrected"] == duplicates
    assert summary["proven_duplicate_damage_removed"] == amount
    assert summary["counts"]["newly_emitted"] == summary["counts"]["unknown_change"] == 0
    change = case["changes"][0]
    assert change["before"]["calls"] == []
    records = audit.wire_records(bytes.fromhex(change["message"]["wire_hex"]), "server_to_client")
    assert len(records) == 1
    expected = [(56, 48, 8908, False, 18682583, 100047, 30),
                (57, 48, 286880, False, 18682611, 100049, 30),
                (128, 59, 185344, False, 18683060, 101984, 18),
                (66, 48, 97920, True, 18682620, 4294967295, 102)][index]
    assert tuple(records[0][k] for k in ("source", "target", "amount", "critical", "group", "object", "marker")) == expected
    assert records[0]["record_end"] == records[0]["marker"] + 118
    assert records[0]["typed_boundaries"][0]["start"] == 14
    assert records[0]["typed_boundaries"][-1]["end"] == len(bytes.fromhex(change["message"]["wire_hex"]))
    proof = change["existing_hit_skill_or_owner_upgraded"][0]["proofs"][0]
    assert proof["binding"]["record"]["owner_id"] == proof["result"][0]
    assert proof["binding"]["birth_fingerprint"]
    assert proof["binding"]["record"]["message_fingerprint"]
    assert proof["static_spec"]["proof_id"]
    assert proof["static_spec"]["monster_skill_id"] == records[0]["self_attack_slot_m102"]
    assert proof["wire_sha256"] == change["message"]["wire_sha256"]


@pytest.mark.parametrize("mutation", ["amount", "time", "critical", "group", "object", "entity", "owner",
                                      "target", "wire_hash", "raw_skill", "healing", "source_generation", "missing_self_proof",
                                      "missing_repair_proof", "wrong_proof_wire", "wrong_attack_slot"])
def test_synthetic_unproved_changes_never_pass_as_upgrade_or_duplicate(report, mutation):
    change = first_change(report)
    hit = change["after"]["rows"][0]
    if mutation == "amount": hit["amount"] += 1
    elif mutation == "time": hit["timestamp_ms"] += 1
    elif mutation == "critical": hit["critical"] = not hit["critical"]
    elif mutation == "group": hit["wire_result_group_id"] += 1
    elif mutation == "object": hit["wire_damage_object_id"] += 1
    elif mutation == "entity": hit["wire_entity_id"] += 1
    elif mutation == "owner": hit["source_id"] = "0x4D000099"
    elif mutation == "target": hit["target_id"] = "0x00009999"
    elif mutation == "wire_hash": change["message"]["wire_sha256"] = "0" * 64
    elif mutation == "raw_skill": hit["raw_skill_id"] = "12345"
    elif mutation == "healing": hit["healing"] = True
    elif mutation == "source_generation": hit["source_id"] += "@999"
    elif mutation == "missing_self_proof":
        change["after"]["calls"] = [c for c in change["after"]["calls"] if c["hook"] != "self_attack"]
    elif mutation == "missing_repair_proof":
        change["after"]["calls"] = [c for c in change["after"]["calls"] if c["hook"] != "target_repair"]
    elif mutation == "wrong_proof_wire":
        for call in change["after"]["calls"]: call["wire_sha256"] = "f" * 64
    else:
        for call in change["after"]["calls"]:
            if call["hook"] == "self_attack": call["static_spec"]["monster_skill_id"] += 1
    result = classify(change)
    assert result["unknown_change"]
    assert not summarize(result)["strict_existing_hit_conservation_passed"]


def test_synthetic_no_survivor_is_unknown_not_duplicate(report):
    change = first_change(report)
    change["after"]["rows"] = []
    result = classify(change)
    assert result["unknown_change"] and not result["proven_duplicate_removed"]


def test_synthetic_new_emission_is_separate_and_fails_existing_hit_conservation(report):
    change = first_change(report, 2)
    change["before"]["rows"] = []
    result = classify(change)
    assert len(result["newly_emitted"]) == 1 and not result["unknown_change"]
    summary = summarize(result)
    assert summary["accounting_balanced"]
    assert not summary["strict_existing_hit_conservation_passed"]


def test_synthetic_equal_amount_foreign_group_cannot_cancel_missing_hit(report):
    change = first_change(report, 2)
    change["after"]["rows"][0]["wire_result_group_id"] += 9
    result = classify(change)
    assert sum(e["amount"] for e in change["before"]["rows"]) == sum(e["amount"] for e in change["after"]["rows"])
    assert result["unknown_change"]
    assert not summarize(result)["strict_existing_hit_conservation_passed"]


def test_synthetic_two_raw_equal_records_cannot_collapse_to_one(report):
    change = first_change(report)
    raw = bytes.fromhex(change["message"]["wire_hex"])
    first = audit.wire_records(raw, "server_to_client")[0]
    key = bytes.fromhex("c8bfcda4943f2642fd8a723474")
    phase, marker = first["phase"], first["marker"]
    body = bytearray(raw)
    for i in range(9, len(body)): body[i] ^= key[(i - phase) % 13]
    body.extend(body[marker-4:])
    body[7:9] = (len(body) - 3).to_bytes(2, "little")
    for i in range(9, len(body)): body[i] ^= key[(i - phase) % 13]
    raw = bytes(body)
    change["message"].update(wire_hex=raw.hex(), wire_sha256=audit.digest(raw))
    for call in change["after"]["calls"]: call["wire_sha256"] = audit.digest(raw)
    assert len(audit.wire_records(raw, "server_to_client")) == 2
    result = classify(change)
    assert result["unknown_change"] and not result["proven_duplicate_removed"]


def test_synthetic_three_old_rows_are_not_declared_two_duplicates(report):
    change = first_change(report)
    change["before"]["rows"].append(deepcopy(change["before"]["rows"][0]))
    result = classify(change)
    assert result["unknown_change"] and not result["proven_duplicate_removed"]


@pytest.mark.parametrize("row", MUTATIONS["synthetic_boundaries"], ids=lambda r: r["mutation"])
def test_shared_raw_boundary_fixtures_cannot_supply_proof(report, row):
    change = first_change(report)
    raw = bytes.fromhex(row["wire"])
    change["message"].update(wire_hex=raw.hex(), wire_sha256=audit.digest(raw))
    # Even plausible hook calls cannot certify a non-top-level/malformed raw
    # marker. Deliberately give them the mutated fullwire fingerprint.
    for call in change["after"]["calls"]:
        call["wire_sha256"] = audit.digest(raw)
    assert audit.wire_records(raw, "server_to_client") == []
    result = classify(change)
    assert result["unknown_change"]
    assert all(not result[k] for k in audit.CATEGORIES if k != "unknown_change")
    assert not summarize(result)["strict_existing_hit_conservation_passed"]


@pytest.mark.parametrize("row", MUTATIONS["synthetic_multi_hit"], ids=lambda r: r["scenario"])
def test_shared_two_equal_hit_fixtures_preserve_markers_and_refuse_loss(report, row):
    change = first_change(report)
    raw = bytes.fromhex(row["wire"])
    records = audit.wire_records(raw, "server_to_client")
    fields = ("source", "target", "amount", "critical", "group", "object", "marker")
    expected_fields = ("source_actor_id", "target_actor_id", "damage", "critical",
                       "damage_result_group_id", "damage_object_id", "record_offset")
    assert [tuple(r[k] for k in fields) for r in records] == [
        tuple(r[k] for k in expected_fields) for r in row["decoded_rows"]]
    assert len(records) == 2 and records[0]["marker"] != records[1]["marker"]
    assert records[0]["amount"] == records[1]["amount"]
    assert len({r["phase"] for r in records}) == 1
    # Build an intentionally invalid 2->1 result: both real physical records
    # existed, only one survives. Equal amounts may NEVER erase the other.
    template = next(e for e in change["before"]["rows"] if audit.actor(e["target_id"]) == 48)
    old = []
    for record in records:
        event = deepcopy(template)
        event.update(target_id=f'0x{record["target"]:08X}', amount=record["amount"],
                     critical=record["critical"], wire_result_group_id=record["group"],
                     wire_damage_object_id=record["object"])
        old.append(event)
    first = records[0]
    final = change["after"]["rows"][0]
    final.update(target_id=f'0x{first["target"]:08X}', amount=first["amount"],
                 critical=first["critical"], wire_result_group_id=first["group"],
                 wire_damage_object_id=first["object"])
    proof = deepcopy(next(c for c in change["after"]["calls"] if c["hook"] == "self_attack"))
    for src, dst in zip(fields, expected_fields):
        proof["decoded"][dst] = first[src]
    proof["wire_sha256"] = audit.digest(raw)
    change["before"]["rows"] = old
    change["after"]["calls"] = [proof]
    change["message"].update(wire_hex=raw.hex(), wire_sha256=audit.digest(raw))
    result = classify(change)
    assert result["unknown_change"] and not result["proven_duplicate_removed"]
    assert not summarize(result)["strict_existing_hit_conservation_passed"]


@pytest.mark.parametrize("mutation", ["truncated", "trailing_fragment", "source_reserved", "attack_reserved", "nested_result"])
def test_independent_reader_rejects_partial_or_ambiguous_slots(report, mutation):
    change = first_change(report)
    raw = bytes.fromhex(change["message"]["wire_hex"])
    record = audit.wire_records(raw, "server_to_client")[0]
    key, phase, marker = bytes.fromhex("c8bfcda4943f2642fd8a723474"), record["phase"], record["marker"]
    body = bytearray(raw)
    for i in range(9, len(body)): body[i] ^= key[(i - phase) % 13]
    if mutation == "truncated": del body[-1:]
    elif mutation == "trailing_fragment": body.extend(b'\0\0')
    elif mutation == "source_reserved": body[marker + 12] = 1
    elif mutation == "attack_reserved": body[marker + 106] = 1
    else:
        at = marker + 40
        body[at-4:at] = (48).to_bytes(4, "little")
        body[at:at+3] = bytes.fromhex("087300")
        body[at+8:at+12] = (56).to_bytes(4, "little")
        body[at+19:at+27] = (8908).to_bytes(8, "little")
    body[7:9] = (len(body) - 3).to_bytes(2, "little")
    for i in range(9, len(body)): body[i] ^= key[(i - phase) % 13]
    assert audit.wire_records(bytes(body), "server_to_client") == []


def test_two_valid_phase_candidates_fail_closed(report, monkeypatch):
    raw = bytes.fromhex(first_change(report)["message"]["wire_hex"])
    records = audit.wire_records(raw, "server_to_client")
    # Synthetic ambiguity at the phase-validation boundary, no production
    # reader injection: the public aggregator must reject multiple candidates.
    monkeypatch.setattr(audit, "_typed_records_for_phase", lambda wire, phase: records if phase in (1, 8) else [])
    assert audit.wire_records(raw, "server_to_client") == []


def test_proof_critical_bit_is_part_of_exact_record_identity(report):
    change = first_change(report)
    record = audit.wire_records(bytes.fromhex(change["message"]["wire_hex"]), "server_to_client")[0]
    for call in change["after"]["calls"]:
        if call["hook"] == "self_attack": call["decoded"]["critical"] = not record["critical"]
        else: call["after"]["critical"] = not record["critical"]
    assert audit.proof_for(record, change["after"]) == ([], [])
    assert classify(change)["unknown_change"]


def test_synthetic_target_only_repair_not_mislabeled_skill_upgrade(report):
    change = first_change(report)
    repair = next(c for c in change["after"]["calls"] if c["hook"] == "target_repair")
    old = next(e for e in change["before"]["rows"] if audit.actor(e["target_id"]) == repair["before"]["target_actor_id"])
    new = deepcopy(old)
    new["target_id"] = f'0x{repair["after"]["target_actor_id"]:08X}'
    change["before"]["rows"], change["after"]["rows"] = [old], [new]
    change["after"]["calls"] = [repair]
    result = classify(change)
    assert len(result["target_corrected"]) == 1
    assert not result["existing_hit_skill_or_owner_upgraded"] and not result["unknown_change"]
    assert summarize(result)["strict_existing_hit_conservation_passed"]


def test_synthetic_realflag_transition_is_explicit(report):
    change = first_change(report, 2)
    change["before"]["rows"][0]["has_real_hit_evidence"] = False
    result = classify(change)
    assert result["realflag_changed"][0]["before"] is False
    assert result["realflag_changed"][0]["after"] is True
    assert result["raw_realflag_totals"] == {"before": 0, "after": 1}


def test_duplicate_timestamp_and_wire_still_keep_message_sequence(report):
    message = first_change(report)["message"]
    other = dict(message, message_index=message["message_index"] + 1)
    assert audit.packet_key(message) != audit.packet_key(other)


def test_synthetic_transport_harness_two_sequential_passes_without_capture_replay(report, monkeypatch):
    """Exercise capture-mode plumbing using ONLY three fixture datagrams.

    The existing fixture JSON is the hash input; no .dpscap is opened. This
    replaces transport, NOT owner or attribution logic, equally for both passes.
    """
    import sys
    import weakref
    sources = sys.modules["dragon_nest_builder.live_dps.sources"]
    protocol = sys.modules["dragon_nest_builder.live_dps.protocol_v576"]
    event_type = sys.modules["dragon_nest_builder.live_dps.models"].CombatEvent
    case = json.loads(audit.FIXTURES.read_text(encoding="utf8"))["cases"][0]
    lifetimes = []
    references = []

    class ThreeFixtureDatagrams:
        active = 0

        def __init__(self, *args):
            assert self.active == 0
            assert all(reference() is None for reference in references)
            type(self).active += 1
            lifetimes.append("open")
            self._decoder = sources._V576PacketEventDecoder(audit.ROOT / "dragon_nest_builder/data")
            self._decoder.monster_entities[case["hit"]["target_id"]] = next(t for t in self._decoder.monster_templates.values() if t.boss)
            references.extend((weakref.ref(self), weakref.ref(self._decoder)))

        def poll(self):
            birth, owner = case["ownership"]["birth"], case["ownership"]["owner_record"]
            events = []
            for index, (t, wire) in enumerate(((birth["t"], birth["wire"]), (owner["time"], owner["wire"]),
                                              (owner["time"] + 1, case["hit"]["wire"]))):
                if index == 2:
                    self._decoder.summon_owners[case["hit"]["entity_id"]] = (0x50123456, case["expected_skill"])
                raw = bytes.fromhex(wire)
                packet = protocol.PacketRecord(t, "UDP", "118.25.23.98", "10.1.39.221", 15161, 57027,
                                               raw, len(raw), len(raw), direction_hint="server_to_client")
                events.extend(self._decoder.feed_events(packet))
            return events

        def close(self):
            lifetimes.append("close")
            type(self).active -= 1

    monkeypatch.setattr(sources, "V576DiagnosticReplaySource", ThreeFixtureDatagrams)
    output = []
    runtime = (sources, protocol, event_type, report["runtime"])
    result = audit.capture_run(runtime, audit.FIXTURES, output.append)
    assert lifetimes == ["open", "close", "open", "close"]
    assert result["strict_existing_hit_conservation_passed"]
    assert result["counts"]["proven_duplicate_removed"] == 1
    assert result["proven_duplicate_damage_removed"] == 8908
    assert [row["record_type"] for row in output] == ["header", "message_delta", "summary"]
    assert output[-1]["audit_complete"]
    assert all(reference() is None for reference in references)
    memory = result["memory_diagnostics"]
    assert memory["before_traces"]["peak_messages"] == 1
    assert memory["before_traces"]["peak_rows"] == 2
    assert memory["normal_replay_event_list_still_materialized"]
    assert [row["side"] for row in memory["pass_lifetimes"]] == ["before", "after"]
    assert all(all(row["released_after_close_and_gc"].values()) for row in memory["pass_lifetimes"])


def output_rows(report, *, passed=True, identity=None):
    identity = identity or report["fixture_sha256"]
    return [{"record_type": "header", "capture_sha256": identity, "input_kind": "test_fixture_only"},
            {"record_type": "message_delta", **first_change(report)},
            {"record_type": "summary", "capture_sha256": identity, "audit_complete": True,
             "strict_existing_hit_conservation_passed": passed}]


def test_persistent_output_exact_wire_checksums_and_deferred_completion(report, tmp_path):
    rows = output_rows(report)
    with audit.PersistentAudit(tmp_path) as output:
        for row in rows: output.emit(row)
        assert output.paths["jsonl"].exists()
        assert not output.paths["summary"].exists() and not output.paths["complete"].exists()
        paths = output.finish()
        with pytest.raises(RuntimeError): output.finish()
        with pytest.raises(RuntimeError): output.emit(rows[1])
    raw = audit.Path(paths["jsonl"]).read_bytes()
    assert raw == b"".join((audit.canonical(row) + "\n").encode("utf8") for row in rows)
    saved = [json.loads(line) for line in raw.decode("utf8").splitlines()]
    assert saved[1]["message"]["wire_hex"] == rows[1]["message"]["wire_hex"]
    marker = json.loads(audit.Path(paths["complete"]).read_text(encoding="utf8"))
    assert marker["audit_complete"] and marker["audit_passed"]
    assert marker["jsonl_sha256"] == audit.digest(raw)
    assert marker["jsonl_bytes"] == len(raw) and marker["jsonl_records"] == 3
    assert marker["summary_sha256"] == audit.file_digest(paths["summary"])
    assert audit.Path(paths["jsonl"]).stem == report["fixture_sha256"]


@pytest.mark.parametrize("suffix", [".jsonl", ".summary.json", ".complete.json"])
def test_any_existing_output_refuses_without_overwrite(report, tmp_path, suffix):
    existing = tmp_path / (report["fixture_sha256"] + suffix)
    with existing.open("xb") as stream: stream.write(b"existing-do-not-touch")
    with pytest.raises(FileExistsError), audit.PersistentAudit(tmp_path) as output:
        output.emit(output_rows(report)[0])
    assert existing.read_bytes() == b"existing-do-not-touch"
    assert list(tmp_path.iterdir()) == [existing]


def test_interrupted_output_is_partial_never_complete_and_never_reused(report, tmp_path):
    with pytest.raises(InterruptedError), audit.PersistentAudit(tmp_path) as output:
        for row in output_rows(report)[:2]: output.emit(row)
        raise InterruptedError("synthetic interruption; no capture replay")
    assert [p.suffix for p in tmp_path.iterdir()] == [".jsonl"]
    with pytest.raises(FileExistsError), audit.PersistentAudit(tmp_path) as retry:
        retry.emit(output_rows(report)[0])


@pytest.mark.parametrize("stage", ["jsonl_fsync", "summary_write", "complete_write"])
def test_persistence_failure_never_produces_completion(report, tmp_path, monkeypatch, stage):
    with audit.PersistentAudit(tmp_path) as output:
        for row in output_rows(report): output.emit(row)
        def fail(*args): raise OSError("synthetic disk failure")
        if stage == "jsonl_fsync": monkeypatch.setattr(output, "_sync", fail)
        else:
            original = output._exclusive_json
            def fail_sidecar(path, row):
                if path == output.paths["summary" if stage == "summary_write" else "complete"]:
                    fail()
                return original(path, row)
            monkeypatch.setattr(output, "_exclusive_json", fail_sidecar)
        with pytest.raises(OSError): output.finish()
    assert not output.paths["complete"].exists()


def test_completed_failed_audit_is_not_reported_as_pass(report, tmp_path):
    with audit.PersistentAudit(tmp_path) as output:
        for row in output_rows(report, passed=False): output.emit(row)
        paths = output.finish()
    marker = json.loads(audit.Path(paths["complete"]).read_text(encoding="utf8"))
    assert marker["audit_complete"] is True and marker["audit_passed"] is False


def test_two_different_hashes_get_independent_exclusive_artifacts(report, tmp_path):
    for identity in (report["fixture_sha256"], "e" * 64):
        with audit.PersistentAudit(tmp_path) as output:
            for row in output_rows(report, identity=identity): output.emit(row)
            output.finish()
    assert len(list(tmp_path.iterdir())) == 6


def test_fixture_cli_persists_raw_but_stdout_is_compact(report, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(audit.sys, "argv", ["audit", "--fixtures", "--output-dir", str(tmp_path)])
    monkeypatch.setattr(audit, "load_runtime", lambda snapshot: None)
    monkeypatch.setattr(audit, "fixture_run", lambda runtime: report)
    assert audit.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["evidence_persisted"] and output["captures_replayed"] == 0
    assert "wire_hex" not in audit.canonical(output) and "birth_wire_hex" not in audit.canonical(output)
    rows = [json.loads(line) for line in audit.Path(output["output_paths"]["jsonl"]).read_text(encoding="utf8").splitlines()]
    assert rows[1]["results"][0]["changes"][0]["message"]["wire_hex"] == first_change(report)["message"]["wire_hex"]


@pytest.mark.parametrize("args", [[], ["--capture", "do-not-read.dpscap"]])
def test_cli_default_and_missing_opt_in_never_read_capture_or_create_output(tmp_path, monkeypatch, args):
    monkeypatch.setattr(audit.sys, "argv", ["audit", *args, "--output-dir", str(tmp_path / "absent")])
    def forbidden(*args): raise AssertionError("Runtime/capture must not be read")
    monkeypatch.setattr(audit, "load_runtime", forbidden)
    monkeypatch.setattr(audit, "file_digest", forbidden)
    with pytest.raises(SystemExit) as failure: audit.main()
    assert failure.value.code == 2 and not list(tmp_path.iterdir())


def test_hashing_is_streamed_not_read_bytes(report, monkeypatch):
    def forbidden(*args): raise AssertionError("Whole-file allocation forbidden")
    monkeypatch.setattr(audit.Path, "read_bytes", forbidden)
    assert audit.file_digest(audit.FIXTURES) == report["fixture_sha256"]


def test_memory_estimator_is_bounded_and_not_an_rss_claim(report):
    change = first_change(report)
    traces = {("synthetic", i): (change["message"], change["before"]) for i in range(1000)}
    memory = audit.trace_memory_estimate(traces)
    assert memory["peak_messages"] == 1000 and memory["peak_rows"] == 2000
    assert memory["sample_entries"] <= 128 and memory["estimated_python_container_bytes"] > 0
    assert memory["is_rss_or_total_process_peak"] is False


def test_measurement_matches_prior_accepted_v26c_four_fixture_audit(report):
    old = json.loads((audit.ROOT / "outputs/v26c_selfattack_ab_tool_20260908/fixture_validation.json").read_text(encoding="utf8"))
    for before, after in zip(old["results"], report["results"], strict=True):
        # Historical JSON materialization uses 0 where Python prints 0.0;
        # compare all JSON values, not incidental numeric spelling/tuples.
        assert before["changes"] == json.loads(audit.canonical(after["changes"]))
        assert before["summary"] == after["summary"]
