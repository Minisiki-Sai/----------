"""Read saved attribution evidence only; never import a decoder or replay a capture.

Baseline fixes the accepted v37 35-capture denominator. Comparison accepts saved
manifests, this baseline, or single audit_self_target_pair_v28 reports. Neither
mode scores, captures, changes attribution, or asserts ground-truth accuracy.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/projectile_evidence_v45_20260909"
PYTHON = r"C:\Users\gcg59\AppData\Local\Python\pythoncore-3.12-64\python.exe"
MANIFEST = "outputs/magma_runtime_v37_20260908/current36_manifest.json"
ACCEPTANCE = "outputs/magma_runtime_v37_20260908/acceptance.json"
FIXED = "outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json"
FROZEN = "outputs/magma_runtime_v37_20260908/after/frozen_manifest.json"
INVARIANTS = ("raw_source_invariants", "aggregated_invariants")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
WIRE_KEYS = ("wire", "wire_hex", "payload_hex")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return sha256(data).hexdigest()


def read_captures(report):
    if "capture_manifest" in report:
        rows = report["capture_manifest"]
    elif "report" in report:
        return read_captures(report["report"])
    elif "capture_path" in report:
        rows = [report]
    else:
        raise ValueError("missing capture_manifest / single capture report")
    require(isinstance(rows, list) and bool(rows), "empty capture set")
    by_path = {}
    for row in rows:
        path = row.get("capture_path")
        require(isinstance(path, str) and path, "missing capture_path")
        require(path not in by_path, "duplicate capture_path: " + path)
        by_path[path] = row
    return by_path


def valid_invariants(value):
    return (isinstance(value, dict)
            and all(type(value.get(k)) is int and value[k] >= 0
                    for k in ("rows", "damage", "real_hits"))
            and all(isinstance(value.get(k), str) and HASH_RE.fullmatch(value[k])
                    for k in ("sha256", "hit_evidence_sha256")))


def job_counts(values):
    require(isinstance(values, dict) and "total_direct_hits" in values
            and "total_direct_damage" in values, "incomplete profession summary")
    keys = ("total_direct_hits", "total_direct_damage", "deterministic_hits",
            "nondeterministic_hits", "unattributed_hits", "unattributed_damage")
    result = {k: values.get(k, 0) for k in keys}  # Counter omits genuine zeros.
    require(all(type(v) is int and v >= 0 for v in result.values()), "invalid counters")
    require(result["total_direct_hits"] == sum(result[k] for k in
            ("deterministic_hits", "nondeterministic_hits", "unattributed_hits")),
            "Hit partition does not conserve")
    return result


def coverage(values):
    exact, inferred = values["deterministic_hits"], values["nondeterministic_hits"]
    named = exact + inferred
    return dict(named_hits=named, confirmed_hits=exact, inferred_hits=inferred,
                unknown_hits=values["unattributed_hits"],
                coverage_percent=100 * exact / named if named else None,
                needed_for_95=max(0, (95 * named + 99) // 100 - exact),
                target_met=bool(named) and 100 * exact >= 95 * named)


def summarize(manifest, fixed_paths):
    all_captures = read_captures(manifest)
    require(bool(fixed_paths) and set(fixed_paths) <= all_captures.keys(), "fixed captures missing")
    selected = [all_captures[p] for p in sorted(fixed_paths)]
    jobs, groups = defaultdict(Counter), {}
    compact = []
    for capture in selected:
        require(HASH_RE.fullmatch(capture.get("capture_sha256", "")), "missing capture SHA")
        require(all(valid_invariants(capture.get(k)) for k in INVARIANTS), "missing physical certificate")
        detail_counts = defaultdict(Counter)
        for detail in capture["details"]:
            cls = detail["classification"]
            require(cls in ("deterministic", "nondeterministic", "unattributed"), "unknown classification")
            count = detail["hits"]
            require(type(count) is int and count >= 0, "invalid detail hits")
            detail_counts[detail["job"]][cls + "_hits"] += count
            if cls != "nondeterministic":
                continue
            key = (detail["job"], str(detail["skill_id"]))
            group = groups.setdefault(key, dict(job=key[0], skill_id=key[1], names=set(),
                inferred_hits=0, methods=Counter(), templates=Counter(), captures=Counter()))
            group["names"].add(detail["skill_name"])
            group["inferred_hits"] += count
            group["methods"][detail["method"]] += count
            group["templates"][detail["projectile_template_id"] or "<no template>"] += count
            group["captures"][capture["capture_path"]] += count
        for job, values in capture["profession_summary"].items():
            counts = job_counts(values)
            for cls in ("deterministic", "nondeterministic", "unattributed"):
                require(detail_counts[job][cls + "_hits"] == counts[cls + "_hits"],
                        "detail/summary mismatch: " + job)
            jobs[job].update(counts)
        require(set(detail_counts) <= capture["profession_summary"].keys(), "extra detail job")
        compact.append({k: capture[k] for k in ("capture_path", "capture_sha256", "decoded_events",
            "profession_summary", "raw_source_invariants", "aggregated_invariants",
            "frozen_signature", "runtime_py_hashes")})
    below = {job for job, value in jobs.items() if not coverage(value)["target_met"]}
    ranked = []
    for key, group in groups.items():
        if key[0] not in below:
            continue
        ranked.append(dict(job=key[0], skill_id=key[1], names=sorted(group["names"]),
            inferred_hits=group["inferred_hits"], methods=dict(group["methods"].most_common()),
            templates=dict(group["templates"].most_common()),
            captures=[dict(capture_path=p, hits=n) for p, n in group["captures"].most_common()],
            label_status="existing inferred display label; not ground truth or promotable count"))
    ranked.sort(key=lambda r: (-r["inferred_hits"], r["job"], r["skill_id"]))
    totals = Counter()
    for values in jobs.values():
        totals.update(values)
    return dict(capture_manifest=compact, totals=coverage(totals),
        excluded_supplemental_captures=sorted(all_captures.keys() - set(fixed_paths)),
        all_jobs=[dict(job=job, **coverage(value)) for job, value in sorted(jobs.items())],
        below95_jobs=[dict(job=job, **coverage(jobs[job])) for job in sorted(below)],
        ranked_skills=ranked)


def verify_accepted(baseline, accepted, frozen):
    require(accepted.get("physical_conserved") is True, "v37 not physically accepted")
    require(len(baseline["capture_manifest"]) == accepted["fixed_coverage_captures"], "fixed count differs")
    actual = {x["job"]: x for x in baseline["all_jobs"]}
    require(len(actual) == len(accepted["jobs"]), "job set differs from acceptance")
    for row in accepted["jobs"]:
        value = actual[row["job"]]
        for key, source in (("confirmed_hits", "deterministic_hits"),
                            ("inferred_hits", "named_inferred_hits"), ("unknown_hits", "unknown_hits")):
            require(value[key] == row["after"][source], "accepted count differs: " + row["job"])
        require(value["target_met"] == row["named_target_met"], "accepted target differs")
    expected = {k: v for k, v in frozen.items() if k.startswith("dragon_nest_builder/") and k.endswith(".py")}
    require(bool(expected), "empty frozen runtime")
    for capture in baseline["capture_manifest"]:
        require(capture["runtime_py_hashes"] == expected, "capture runtime differs from accepted freeze")


def compare_reports(before, after):
    """Fail closed on missing certificates, Hit eligibility changes, or scope drift."""
    left, right = read_captures(before), read_captures(after)
    checks, gains = [], []
    for path in sorted(left.keys() | right.keys()):
        a, b = left.get(path), right.get(path)
        failures = []
        if a is None or b is None:
            checks.append(dict(capture_path=path, conserved=False, failures=["capture_set_changed"]))
            continue
        if not HASH_RE.fullmatch(a.get("capture_sha256", "")) or a.get("capture_sha256") != b.get("capture_sha256"):
            failures.append("capture_bytes_changed_or_missing")
        if type(a.get("decoded_events")) is not int or a.get("decoded_events") != b.get("decoded_events"):
            failures.append("decoded_events_changed_or_missing")
        for key in INVARIANTS:
            if not valid_invariants(a.get(key)) or not valid_invariants(b.get(key)) or a[key] != b[key]:
                failures.append(key + "_changed_or_incomplete")
        for key in ("raw_physical_rows", "aggregated_physical_rows"):
            if key in a or key in b:
                expected_columns = ["time", "source", "target", "amount", "critical", "group", "object",
                                    "wire_source", "wire_target", "offset", "real"]
                if (key not in a or key not in b or a.get("physical_columns") != b.get("physical_columns")
                        or not isinstance(a.get("physical_columns"), list)
                        or a["physical_columns"][:11] != expected_columns):
                    failures.append(key + "_missing_or_columns_changed")
                elif any(len(r) < 11 for r in a[key] + b[key]) or [r[:11] for r in a[key]] != [r[:11] for r in b[key]]:
                    failures.append(key + "_physical_or_real_hit_changed")
        old_jobs, new_jobs = a.get("profession_summary", {}), b.get("profession_summary", {})
        if not old_jobs or old_jobs.keys() != new_jobs.keys():
            failures.append("job_set_changed_or_missing")
        for job in sorted(old_jobs.keys() & new_jobs.keys()):
            try:
                av, bv = job_counts(old_jobs[job]), job_counts(new_jobs[job])
            except ValueError as exc:
                failures.append(job + ": " + str(exc))
                continue
            for key in ("total_direct_hits", "total_direct_damage", "unattributed_hits", "unattributed_damage"):
                if av[key] != bv[key]:
                    failures.append(job + ": " + key + " changed")
            delta = bv["deterministic_hits"] - av["deterministic_hits"]
            if delta != av["nondeterministic_hits"] - bv["nondeterministic_hits"]:
                failures.append(job + ": named denominator changed")
            if delta < 0:
                failures.append(job + ": deterministic regression")
            if delta:
                gains.append(dict(capture_path=path, job=job, confirmed_delta=delta))
        ha, hb = a.get("runtime_py_hashes"), b.get("runtime_py_hashes")
        if not ha or not hb or not a.get("frozen_signature") or not b.get("frozen_signature"):
            failures.append("runtime_provenance_missing")
        changed = [k for k in sorted((ha or {}).keys() | (hb or {}).keys()) if (ha or {}).get(k) != (hb or {}).get(k)]
        checks.append(dict(capture_path=path, conserved=not failures, failures=failures,
            runtime_changes=[dict(path=k, before=(ha or {}).get(k), after=(hb or {}).get(k)) for k in changed]))
    return dict(schema="projectile-gap-comparison-v45", same_capture_set=left.keys() == right.keys(),
        comparison_passed=all(x["conserved"] for x in checks), capture_checks=checks, gains=gains,
        release_approval=False, limitation="Conservation is necessary, not proof of any new attribution rule. No replay was run.")


class Inputs:
    def __init__(self, root=ROOT):
        self.root, self.hashes = root, {}

    def read(self, name):
        path = Path(name)
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        raw = path.read_bytes()
        value = digest(raw)
        require(str(path) not in self.hashes or self.hashes[str(path)] == value, "input changed while reading")
        self.hashes[str(path)] = value
        return raw

    def json(self, name):
        return json.loads(self.read(name).decode("utf-8"))

    def verify(self):
        require(all(digest(Path(p).read_bytes()) == h for p, h in self.hashes.items()), "input changed during run")


def pointer_escape(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def wire_inventory(value, pointer="", inherited=None):
    """JSON locators + byte digests; no decoder import, decryption or capture IO."""
    inherited = inherited or {}
    if isinstance(value, dict):
        context = dict(inherited)
        for key in ("capture", "capture_path", "frame", "file_offset", "message", "phase",
                    "marker", "record_offset", "offset", "time", "t", "direction", "template",
                    "action", "signal", "entity", "object", "parent", "owner"):
            if key in value and not isinstance(value[key], (dict, list)):
                context[key] = value[key]
        for key in WIRE_KEYS:
            if isinstance(value.get(key), str):
                raw = bytes.fromhex(value[key])
                expected = value.get("wire_sha256") or value.get("payload_sha256")
                require(expected is None or expected == digest(raw), "saved wire SHA mismatch at " + pointer)
                yield dict(json_pointer=pointer + "/" + key, wire_sha256=digest(raw),
                           wire_bytes=len(raw), header9_hex=raw[:9].hex(), context=context)
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                yield from wire_inventory(item, pointer + "/" + pointer_escape(key), context)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from wire_inventory(item, pointer + "/" + str(index), inherited)


DIAGNOSTICS = (
    "outputs/named_gap_wire_v36_20260908/0813硬核火舞_origins.json",
    "outputs/named_gap_wire_v36_20260908/0821黑典女王_origins.json",
    "outputs/named_gap_wire_v36_20260908/20260806YEP黑典时空.json",
    "outputs/named_hit_chain_followup_20260908/diagnostic_0812.json",
    "outputs/named_hit_chain_followup_20260908/diagnostic_0821.json",
    "outputs/compact_rejection_v33_20260908/0821硬核04.json",
)
FIXTURES = (
    "tests/data/compact_entity_selectors_raw.json",
    "tests/data/shared_arrow_framed_raw.json",
    "tests/data/damage_object_server_parent_registrations.json",
    "tests/data/direct_result_fixed_field_anchors.json",
)
RECORDS = (
    "outputs/gap_object_records_v36_20260908/0813硬核火舞_黑暗萨满.json",
    "outputs/gap_object_records_v36_20260908/0821黑典女王_雷神.json",
    "outputs/gap_object_records_v36_20260908/20260806YEP黑典时空_影_group.json",
    "outputs/compact_rejection_v33_20260908/duck_default_object_occurrences.json",
)
SOURCE_FILES = (
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/udp_damage_records.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/compact_entity_object_evidence.py",
    "dragon_nest_builder/live_dps/summon_self_attack_evidence_v26.py",
    "dragon_nest_builder/live_dps/autonomous_attack_families.py",
    "tools/audit_direct_damage_by_profession.py",
    "tools/audit_self_target_pair_v28.py",
    "tools/freeze_udp_audit_runtime_v27.py",
    "tools/probe_gap_object_records_v36.py",
    "tools/probe_named_gap_wire_v36.py",
    "tools/probe_tcp_object_sidecar_v37.py",
    "tests/test_compact_entity_selectors.py",
    "tests/test_shared_arrow_framed_batches.py",
    "tests/test_damage_object_server_parent_registrations.py",
    "tests/test_damage_object_wrapped_registrations.py",
    "tests/test_gap_probe_v36.py",
    "tests/test_projectile_primary_wire_identity_v25.py",
    "tests/test_projectile_gap_baseline_v45.py",
)


def collect_evidence(inputs, baseline, frozen):
    jobs = {r["job"] for r in baseline["below95_jobs"]}
    samples, diagnostics, fixtures, records = [], [], [], []
    for name in DIAGNOSTICS:
        data = inputs.json(name)
        capture = data["capture"]
        for index, row in enumerate(data["rows"]):
            if row["job"] not in jobs:
                continue
            reason = row.get("reason") or row.get("origin_state") or row.get("wire_state")
            item = dict(source=name, json_pointer=f"/rows/{index}", capture=capture, job=row["job"],
                reason=reason, historical_preaggregation_count=row.get("count", row.get("hits")),
                scope="Historical row grouping; NOT v37 final skill counts and NOT an upgrade prediction.",
                layout=row.get("layout"), slots=row.get("slots"), object_kind=row.get("object_kind"))
            diagnostics.append(item)
            for eindex, example in enumerate(row["examples"]):
                event = example["event"]
                wire = bytes.fromhex(example["wire"])
                samples.append(dict(job=row["job"], skill_id=str(event["skill_id"]), source=name,
                    json_pointer=f"/rows/{index}/examples/{eindex}", capture=capture,
                    reason=reason, layout=event["wire_damage_layout"],
                    marker=event["wire_record_offset"], time=example["time"],
                    wire_sha256=digest(wire), wire_bytes=len(wire),
                    wire_source=event["wire_source_actor_id"], wire_target=event["wire_target_actor_id"],
                    object=event["wire_damage_object_id"], group=event["wire_result_group_id"],
                    positional=example.get("positional"), state=example.get("state"),
                    origin=example.get("origin"),
                    classification="historical diagnostic label, not ground truth"))
    for name in FIXTURES:
        data = inputs.json(name)
        wires = list(wire_inventory(data))
        fixtures.append(dict(source=name, raw_wire_count=len(wires),
                             distinct_wire_count=len({r["wire_sha256"] for r in wires}), wires=wires))
    for name in RECORDS:
        data = inputs.json(name)
        matches = []
        for index, row in enumerate(data["matches"]):
            raw = bytes.fromhex(row["wire"])
            require(digest(raw) == row["wire_sha256"], "record wire hash differs")
            matches.append(dict(json_pointer=f"/matches/{index}",
                **{k: row.get(k) for k in ("frame", "file_offset", "message", "time", "phase", "records",
                    "decoded_client", "decoded_server", "positions", "direction", "wire_sha256")},
                wire_bytes=len(raw)))
        unknown = list(wire_inventory(data.get("unparsed", []), "/unparsed"))
        records.append(dict(source=name, capture=data["capture"], matches=matches, unknown_frames=unknown,
            limitation="Object-byte matches are not valid registration or skill evidence; no capture reopened."))
    tcp_name = "outputs/tcp_object_sidecar_v37_20260908/0813黑萨_tcp_object_scan.json"
    review_name = "outputs/tcp_object_sidecar_v37_20260908/cipher_review.json"
    tcp, review = inputs.json(tcp_name), inputs.json(review_name)
    messages = {r["message"]: r for r in tcp["messages"]}
    reviewed = set()
    for row in review["observations"]:
        require(row["wire_sha256"] == messages[row["message"]]["wire_sha256"], "TCP review input differs")
        require(row["valid_length"] is True, "TCP review length failed")
        reviewed.add(row["message"])
    opaque = []
    for index, message in enumerate(tcp["messages"]):
        raw = bytes.fromhex(message["wire_hex"])
        require(digest(raw) == message["wire_sha256"], "TCP wire SHA mismatch")
        if not message["decoded"] and message["message"] not in reviewed:
            opaque.append(dict(json_pointer=f"/messages/{index}/wire_hex",
                **{k: message.get(k) for k in ("message", "length", "wire_sha256", "physical_locations", "opaque_reason")}))
    sources = []
    for name in SOURCE_FILES:
        raw = inputs.read(name)
        tree = ast.parse(raw.decode("utf-8-sig"))
        definitions = [dict(name=n.name, line=n.lineno) for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and (n.name.startswith("test_") or n.name in (
                           "decode_v576_damage_object_registration", "decode_v576_server_damage_object_registrations",
                           "decode_selector_records", "inspect_udp_damage_records", "read_self_attack_slot", "main"))]
        sources.append(dict(path=name, sha256=digest(raw), accepted_frozen_sha256=frozen.get(name),
                            differs_from_v37=frozen.get(name) != digest(raw), definitions=definitions))
    by_skill = defaultdict(list)
    for sample in samples:
        by_skill[sample["job"], sample["skill_id"]].append(sample)
    for skill in baseline["ranked_skills"]:
        skill["historical_samples"] = by_skill[skill["job"], skill["skill_id"]]
        skill["evidence_reason"] = reason_for(skill)
    return dict(diagnostic_groups=diagnostics, fixture_catalog=fixtures, raw_record_inventory=records,
        current_source_inventory=sources,
        unknown_registration_availability=dict(
            registration_proven=False,
            udp_unparsed_saved_frames=sum(len(r["unknown_frames"]) for r in records),
            tcp_original_opaque=tcp["counts"]["undecoded_TCP"],
            tcp_subsequently_reviewed=len(reviewed), tcp_still_unexamined=len(opaque),
            tcp_source=tcp_name, later_review=review_name, remaining_tcp_messages=opaque,
            registration_candidates_in_saved_tcp_scan=tcp["candidate_count"],
            limitation="Raw unknown frames exist. They are NOT demonstrated missing-object registrations; 362 reviewed name messages must not be recounted as opaque."))


def reason_for(skill):
    templates, methods = skill["templates"], skill["methods"]
    result = []
    if any(k.startswith("实体模板:") for k in templates):
        result.append("Entity ownership is insufficient: need its own attack selector plus current birth/owner/object generation; empty self slot cannot inherit creator.")
    if "<对象存在但模板未解析>" in templates:
        result.append("Object present but template unresolved in accepted detail; valid same-owner current registration/lineage is missing or unproved, not necessarily a decoder bug.")
    if "damage_object_projectile_link_provisional" in methods:
        result.append("Provisional shared-template lineage needs full parent ID and independent action root; an old label or nearest cast cannot certify it.")
    if "<no template>" in templates:
        result.append("No template in accepted detail; inspect raw family/fixed slot before assuming projectile identity, especially direct41.")
    return result or ["Existing heuristic label still lacks an independent exact packet/object skill proof."]


SHAPES = [
    dict(name="direct37", source="dragon_nest_builder/live_dps/udp_damage_records.py",
         shape="opcode 08, declared body 0x73 (115); m=opcode; target u32 m-4, group m+4, source m+8, object m+13, damage u64 m+19, critical byte m+35, raw skill slots m+102/m+114",
         guard="Exact framed record, same physical source/target/amount/object; FFFFFFFF/zero slots and context alone cannot certify a skill."),
    dict(name="direct41", source="dragon_nest_builder/live_dps/udp_damage_records.py",
         shape="opcode 08, declared body 0x77 (119); target m-4, group m+4, source m+8, opaque bytes m+13..m+20, damage u64 m+23, critical m+39, raw skill slot m+106",
         guard="Object is None, not direct37 object slot; result group is NOT a skill foreign key."),
    dict(name="shared-arrow server09", source="dragon_nest_builder/live_dps/protocol_v576.py",
         shape="09/62 or 09/66 (body 98/102); actor/owner m-4 repeats m+44; object m+8, template m+12; reviewed 2023 parent m+49, branch m+53, suffix m+69; reserved FF m+57..m+64",
         guard="Complete envelope and exact EOF, checked companions/reserved fields; full same-owner parent ID/current generation/independent root required. Not a generic 09 layout."),
    dict(name="compact entity selector", source="dragon_nest_builder/live_dps/compact_entity_object_evidence.py",
         shape="short entity actor (<65536), 09/29 or 09/32 (body 41/50); object m+3, ACT ordinal m+34, signal ordinal m+38, reserved m+42=0",
         guard="Existing only: (32005,50,50,5)->4352/5054,3s; (33608,41,80,2)->6319/11090,8s. Need birth+owner+selector+hit; retransmission must not renew expiry. New selector needs independent static mapping."),
]


def command(*args):
    return "& '" + PYTHON + "' -B " + " ".join("'" + str(a).replace("'", "''") + "'" for a in args)


def reproduction_commands():
    return [
        dict(purpose="Rebuild saved baseline only; no capture reads", run_now_safe=True,
             command=command("tools/report_projectile_gap_baseline_v45.py", "baseline")),
        dict(purpose="Fast existing 6-chain compact-selector positives/negatives; no full capture replay", run_now_safe=True,
             command=command("-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/test_compact_entity_selectors.py")),
        dict(purpose="Existing raw parent fixtures; test selection avoids capture-wide auditing", run_now_safe=True,
             command=command("-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/test_damage_object_server_parent_registrations.py")),
        dict(purpose="Optional bounded raw UDP slice only; NOT executed. Reopens saved profile targets from recording start to first six anchors+2s; do not repeat without need.", run_now_safe=False,
             command=command("tools/probe_gap_object_records_v36.py", DIAGNOSTICS[1], "雷神")),
        dict(purpose="Future authorized one-capture frozen BEFORE control; placeholders must be replaced. This is a full capture replay, not a small slice; NOT executed by this task.", run_now_safe=False,
             command=command("tools/audit_self_target_pair_v28.py", "<FROZEN_BEFORE_ROOT>", "<ONE_CAPTURE_PATH>", "<BEFORE_REPORT.json>")),
        dict(purpose="Future authorized same-capture frozen AFTER control; separately frozen runtime, identical resources/scope; NOT executed.", run_now_safe=False,
             command=command("tools/audit_self_target_pair_v28.py", "<FROZEN_AFTER_ROOT>", "<ONE_CAPTURE_PATH>", "<AFTER_REPORT.json>")),
        dict(purpose="Compare saved single-capture controls, manifests, or two v45 baselines; fails closed on missing conservation evidence", run_now_safe=True,
             command=command("tools/report_projectile_gap_baseline_v45.py", "compare", "<BEFORE_REPORT.json>", "<AFTER_REPORT.json>")),
    ]


def markdown(report):
    if report["schema"] == "projectile-gap-comparison-v45":
        lines = ["# 冻结归因报告比较", "", f"比较通过：{report['comparison_passed']}；不是新规则正确性或发布批准。", ""]
        for row in report["capture_checks"]:
            lines.append(f"- {row['capture_path']}: {row['failures'] or '物理记录、真实Hit、分母及未知数量守恒'}")
        lines += ["", "增量：" + json.dumps(report["gains"], ensure_ascii=False)]
        return "\n".join(lines) + "\n"
    lines = ["# v45 只读技能缺口基线（2026-09-09）", "",
        "基线固定为已验收 v37 的35份抓包、30职业；11职业未达95%。第36份补充抓包不进入分母。这里是证据覆盖，不是准确率。",
        "本次只读现成JSON与源码：0抓包重放、0评分、0新取证切片、0客户端/DLL执行、0生产修改。", "",
        f"已命名总体：{report['totals']['confirmed_hits']}/{report['totals']['named_hits']} = {report['totals']['coverage_percent']:.5f}%；19/30职业达标。", "",
        "## 全30职业基线（到95%还需>0的11职业为本次缺口范围）", "",
        "|职业|确认/已命名Hit|覆盖率|推断Hit|未命名Hit|到95%还需|", "|---|---:|---:|---:|---:|---:|"]
    for row in report["all_jobs"]:
        lines.append(f"|{row['job']}|{row['confirmed_hits']}/{row['named_hits']}|{row['coverage_percent']:.4f}%|{row['inferred_hits']}|{row['unknown_hits']}|{row['needed_for_95']}|")
    lines += ["", "## 优先技能（推断标签，不是可升级承诺）", "",
              "|职业|skill ID / 旧名称|剩余推断Hit|主要method|主要模板|", "|---|---|---:|---|---|"]
    for row in report["ranked_skills"][:25]:
        methods = "; ".join(f"{k}:{n}" for k, n in list(row["methods"].items())[:2])
        templates = "; ".join(f"`{k}`:{n}" for k, n in list(row["templates"].items())[:2])
        lines.append(f"|{row['job']}|{row['skill_id']} {'/'.join(row['names'])}|{row['inferred_hits']}|{methods}|{templates}|")
    lines += ["", "## 每职业首要技能与可复核样本", ""]
    for job in report["below95_jobs"]:
        for row in [r for r in report["ranked_skills"] if r["job"] == job["job"]][:3]:
            lines += [f"### {row['job']} / {row['skill_id']} / {row['inferred_hits']} Hit", "",
                      " ".join(row["evidence_reason"]), ""]
            for cap in row["captures"][:2]:
                lines.append(f"- 抓包：`{cap['capture_path']}`，该标签缺口 {cap['hits']} Hit。")
            for sample in row["historical_samples"][:2]:
                lines.append(f"- 历史样本 `{sample['source']}#{sample['json_pointer']}`：{sample['reason']}；{sample['layout']}；marker={sample['marker']}，object={sample['object']}；wire SHA256 `{sample['wire_sha256']}`。")
            if not row["historical_samples"]:
                lines.append("- 当前选定旧诊断中没有该skill的保存示例；抓包路径可用不等于完整因果链夹具已存在。")
            lines.append("")
    lines += ["## 原始形状与边界", ""]
    for row in report["record_shapes"]:
        lines += [f"- {row['name']}: {row['shape']}", f"  - {row['guard']} 源：`{row['source']}`。"]
    unknown = report["unknown_registration_availability"]
    lines += ["", "## 未知帧是否存在", "",
        f"保存的UDP未行走帧：{unknown['udp_unparsed_saved_frames']}。TCP原始扫描不透明 {unknown['tcp_original_opaque']} 条，后续独立复核 {unknown['tcp_subsequently_reviewed']} 条，仍未检查 {unknown['tcp_still_unexamined']} 条。",
        "这些只是未知原始帧，不是已证明的缺注册。原TCP扫描同主人命中前注册候选为0；后续362条是名称/短ID形状，不能当作技能/完整对象注册。JSON保存剩余消息的精确索引、字节哈希和物理文件位置。", ""]
    for record in report["raw_record_inventory"]:
        lines.append(f"- `{record['source']}`：{len(record['matches'])} 个保存匹配、{len(record['unknown_frames'])} 个未行走帧。")
        for row in record["unknown_frames"][:2]:
            lines.append(f"  - `{row['json_pointer']}`，{row['wire_bytes']} bytes，SHA `{row['wire_sha256']}`；{row['context']}。")
    lines += ["", "## 便宜的规则验证是否可做", "",
        "可做证据完整性/负例和冻结守恒比较，但当前证据不足以批准任何新确定性规则。最便宜的正控是现有compact-selector 6条完整出生→主人→注册→Hit夹具；已有32005/50/50/5只证明4352，不证明所有4216或默认鸭攻击。",
        "0821硬核04的旧诊断已有137条通过与111条registration_missing；它们是汇总前机械大师计数，不能加到重炮手1129或预测整体增益。默认鸭对象101687有14条原始出现库存，但不能从出现次数伪造创建注册。",
        "雷神3407/3402的收益上限大，但direct37空固定槽和缺有效登记没有变成新证明；主线程取得独立selector/对象语义后，先用原始正控与错主人/错代/过期/重放/未知selector/技能冲突负例测试，再用冻结同抓包报告比较。",
        "比较器要求capture SHA、解码事件数、raw/aggregated记录指纹与real-hit指纹、逐职业总Hit/总伤害/未知量/命名分母均不变；若报告带物理列表还逐条比较前11列。源码差异明列，不自动批准。", "",
        "## 现有夹具", ""]
    for fixture in report["fixture_catalog"]:
        lines.append(f"- `{fixture['source']}`：{fixture['raw_wire_count']} 条wire字段，{fixture['distinct_wire_count']} 个不同字节串。精确JSON索引及SHA在gaps.json。")
    lines += ["", "## 复现命令（未自动执行抓包工具）", ""]
    for item in report["reproduction"]:
        lines += [item["purpose"], "", "```powershell", item["command"], "```", ""]
    lines += ["## 来源冻结", "", f"脚本 SHA256 `{report['script_sha256']}`。所有读取输入在生成前后哈希一致。",
        "gaps.json包含完整35份物理证书、所有剩余技能/方法/模板/抓包计数、旧诊断位置、当前源码与已验收冻结源码差异。未将缺失证据当作零或成功。", ""]
    for source in report["current_source_inventory"]:
        lines.append(f"- `{source['path']}` SHA `{source['sha256']}`；相对v37冻结变化：{source['differs_from_v37']}。")
    return "\n".join(lines) + "\n"


def output_path(value):
    path = Path(value).resolve()
    require(path.parent == OUT.resolve() and path.name.startswith("gaps") and path.suffix == ".json",
            "output must be owned outputs/projectile_evidence_v45_20260909/gaps*.json")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    build = modes.add_parser("baseline")
    build.add_argument("--manifest", type=Path, default=ROOT / MANIFEST)
    build.add_argument("--acceptance", type=Path, default=ROOT / ACCEPTANCE)
    build.add_argument("--fixed-set", type=Path, default=ROOT / FIXED)
    build.add_argument("--frozen", type=Path, default=ROOT / FROZEN)
    build.add_argument("--output", type=Path, default=OUT / "gaps.json")
    compare = modes.add_parser("compare")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    compare.add_argument("--output", type=Path, default=OUT / "gaps_comparison.json")
    args = parser.parse_args(argv)
    target = output_path(args.output)
    inputs = Inputs()
    source_sha = digest(inputs.read(__file__))
    if args.mode == "baseline":
        manifest, accepted = inputs.json(args.manifest), inputs.json(args.acceptance)
        fixed = set(read_captures(inputs.json(args.fixed_set)))
        frozen = inputs.json(args.frozen)
        report = summarize(manifest, fixed)
        verify_accepted(report, accepted, frozen)
        require(len(fixed) == 35 and len(report["below95_jobs"]) == 11, "unexpected v37 baseline scope")
        report.update(collect_evidence(inputs, report, frozen))
        # Stat only, never read original capture bytes or replay them.
        for capture in report["capture_manifest"]:
            p = Path(capture["capture_path"])
            capture["capture_file_available"] = p.is_file()
            capture["capture_hash_verification"] = "accepted manifest only; original not rehashed this run"
        report.update(schema="projectile-gap-baseline-v45", script_sha256=source_sha,
            record_shapes=SHAPES, reproduction=reproduction_commands(),
            actions=dict(capture_replays=0, new_forensic_slices=0, score_runs=0, production_writes=0),
            new_deterministic_rule_approved=False)
    else:
        report = compare_reports(inputs.json(args.before), inputs.json(args.after))
        report["script_sha256"] = source_sha
    inputs.verify()
    report["input_sha256"] = inputs.hashes
    report["inputs_unchanged"] = True
    require(str(target) not in inputs.hashes and str(target.with_suffix(".md")) not in inputs.hashes,
            "refusing to overwrite an input")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps(dict(output=str(target), schema=report["schema"],
        below95_jobs=len(report.get("below95_jobs", [])), ranked_skills=len(report.get("ranked_skills", [])),
        comparison_passed=report.get("comparison_passed"), inputs_unchanged=True), ensure_ascii=False))
    return 2 if report.get("comparison_passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
