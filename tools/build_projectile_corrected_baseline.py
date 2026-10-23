"""Plan/verify the v24 baseline with only reviewed projectile identity fixes.

This tool does not write files. --emit-file emits exact UTF-8 file content as
JSON for Codex apply_patch; --emit-patch emits an apply_patch batch. Apply the
plan to its independent destination, then run --verify. No capture replay,
workers, scoring or shared runtime edits are performed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FUNCTION = "decode_v576_projectile_damage"
TRANSIENT = "_TRANSIENT_DAMAGE_TARGET_LAYOUTS"
OLD_LAYOUT = "s2c-udp-projectile-direct37-v576"
BASELINE = ROOT / "outputs/damage_attribution_baseline_v25_20260907"
REVIEWED = ROOT / "outputs/damage_attribution_candidate_v25_entity_preview_20260907/protocol_v576.py"
DESTINATION = ROOT / "outputs/damage_attribution_baseline_v25_projectile_corrected_20260908"
REVIEWED_SHA256 = "0d31ee83a9985dd87f3149c073b8d8a52e89c798cebf0d72959cecde8d81c69c"
BASELINE_HASHES = {
    "actor_state.py": "5cb41927349083eacf39085759fd0a30de664dc984f50586415f203044538a8b",
    "core_decoder.py": "7112756e91d1608ab94f530ed4399fa59d6dd4a015a594f6d2acf571903867fc",
    "damage_attribution.py": "38ef45cb825e84ad7ed23cb7b269ec6e01aaef3d32adb74e8130ec90b20014ca",
    "damage_object_origins.py": "42fb755017b7b8f1b1a9ca9bd146eaa1038e46314ecf7f3b0ec7779cc1b2bd64",
    "generated_attack_instance_specs_v576.py": "8eec0d884ddea8a022c2ab4ee2390be9e8d6808cce4c95b50d57eb4a6f61d65c",
    "models.py": "8a836e32421c5072ee0c431c4496cf8fba593f0e22e6f8b7cc5a5bc5b3682099",
    "protocol_v576.py": "db5d1023bd77eeffefb58014feb2a802b22b2a8612990e48bfc764ecf080defd",
    "replay_normalization.py": "48b6df5d7e9860a546d955e92b144b66dae0fe311505a087bb68542ff45157bb",
    "sources.py": "4aa5d685cd02e6c2d7a8785b00f6d63a835c2e32cf541682d3ab42ff29094456",
    "status_decoder.py": "b2154edf049d6fc52d17835021113ed9241dd579ee346e6df5ff165e1249a4e4",
    "udp_direction.py": "57e919c3d188ecc7ec31c477501b57bbf0b3efaa70c350431a5c33f53fc94d12",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def function_parts(data):
    # Byte slicing preserves all text outside the one top-level function.
    tree = ast.parse(data.decode("utf8"))
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == FUNCTION]
    require(len(matches) == 1 and not matches[0].decorator_list,
            "Expected one undecorated projectile function")
    node = matches[0]
    lines = data.splitlines(keepends=True)
    return (b"".join(lines[:node.lineno - 1]),
            b"".join(lines[node.lineno - 1:node.end_lineno]),
            b"".join(lines[node.end_lineno:]))


def transient_parts(data):
    tree = ast.parse(data.decode("utf8"))
    matches = [node for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == TRANSIENT
                       for target in node.targets)]
    require(len(matches) == 1, "Expected one transient-layout assignment")
    call = matches[0].value
    require(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "frozenset" and not call.keywords
            and len(call.args) == 1 and isinstance(call.args[0], ast.Set)
            and ast.literal_eval(call.args[0]) == {OLD_LAYOUT},
            "Refusing to erase any unreviewed transient family")
    lines = data.splitlines(keepends=True)
    start = sum(map(len, lines[:call.lineno - 1])) + call.col_offset
    end = sum(map(len, lines[:call.end_lineno - 1])) + call.end_col_offset
    return data[:start], data[start:end], data[end:]


def make_plan(baseline, reviewed, destination):
    baseline, reviewed, destination = map(Path.resolve, (baseline, reviewed, destination))
    require(destination != baseline and not destination.is_relative_to(baseline)
            and not baseline.is_relative_to(destination), "Destination overlaps baseline")
    require(destination.is_relative_to(ROOT / "outputs"), "Destination must be under outputs")
    require(not reviewed.is_relative_to(destination), "Destination contains reviewed input")
    require({path.name for path in baseline.glob("*.py")} == set(BASELINE_HASHES),
            "Baseline module inventory changed")
    original = {name: (baseline / name).read_bytes() for name in BASELINE_HASHES}
    for name, data in original.items():
        require(sha(data) == BASELINE_HASHES[name], f"Unreviewed baseline bytes: {name}")
    reviewed_bytes = reviewed.read_bytes()
    require(sha(reviewed_bytes) == REVIEWED_SHA256, "Reviewed protocol hash changed")
    files = dict(original)
    prefix, old_function, suffix = function_parts(original["protocol_v576.py"])
    reviewed_function = function_parts(reviewed_bytes)[1]
    files["protocol_v576.py"] = prefix + reviewed_function + suffix
    source_prefix, old_value, source_suffix = transient_parts(original["sources.py"])
    files["sources.py"] = source_prefix + b"frozenset()" + source_suffix
    require(function_parts(files["protocol_v576.py"]) == (prefix, reviewed_function, suffix),
            "Protocol change escaped the reviewed function")
    for name, data in files.items():
        compile(data, str(destination / name), "exec")
    changed = sorted(name for name in files if files[name] != original[name])
    require(changed == ["protocol_v576.py", "sources.py"], "Unexpected changed module")
    # The byte checks above also preserve every registration class and parent
    # decoder: no Maxwell/shared-parent or entity-owner code is imported here.
    manifest = {
        "schema_version": 1,
        "created_for": "coverage denominator comparison; v24 with reviewed projectile identity correction only",
        "baseline_directory": str(baseline), "output_directory": str(destination),
        "reviewed_protocol": str(reviewed), "reviewed_protocol_sha256": REVIEWED_SHA256,
        "reviewed_function": FUNCTION, "reviewed_function_sha256": sha(reviewed_function),
        "baseline_function_sha256": sha(old_function),
        "changes": {
            "protocol_v576.py": "Replace only decode_v576_projectile_damage with the reviewed function, preserving every other byte",
            "sources.py": "Replace only the singleton transient frozenset expression with frozenset()",
        },
        "changed_modules": changed,
        "unchanged_modules": sorted(set(files) - set(changed)),
        "baseline_sha256": dict(BASELINE_HASHES),
        "output_sha256": {name: sha(data) for name, data in sorted(files.items())},
        "source_value_before_sha256": sha(old_value),
        "maxwell_parent_chain_changes_included": False,
        "entity_owner_changes_included": False,
        "full_corpus_replayed": False,
        "files_written_by_tool": False,
        "materialization": "Use --emit-file/--emit-patch with apply_patch, then --verify",
    }
    files["MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    return files, manifest


def verify(destination, files, manifest):
    actual_names = {path.name for path in destination.iterdir() if path.is_file()}
    require(actual_names == set(files), "Output inventory has missing or unexpected files")
    for name, expected in files.items():
        require((destination / name).read_bytes() == expected, f"Output bytes differ: {name}")
    sys.path.insert(0, str(ROOT))
    from tools.audit_damage_attribution_paired import BaselineFinder, snapshot_modules
    sys.meta_path.insert(0, BaselineFinder(destination, snapshot_modules(destination)))
    from dragon_nest_builder.live_dps import protocol_v576 as protocol, sources
    from dragon_nest_builder.live_dps.damage_object_origins import DamageObjectOriginLedger
    paths = {}
    for label, fn, name in (
        ("protocol", protocol.decode_v576_projectile_damage, "protocol_v576.py"),
        ("core", sources._V576PacketEventDecoder.feed_events, "core_decoder.py"),
        ("actor_state", sources._V576PacketEventDecoder._normalize_transient_damage_target, "actor_state.py"),
        ("status", sources._V576PacketEventDecoder._supplemental_embedded_direct_damage_events, "status_decoder.py"),
        ("origins", DamageObjectOriginLedger.observe_registration, "damage_object_origins.py"),
    ):
        actual_path = Path(fn.__code__.co_filename).resolve()
        require(actual_path == destination / name, f"Non-baseline import: {label}")
        paths[label] = str(actual_path)
    require(sources._TRANSIENT_DAMAGE_TARGET_LAYOUTS == frozenset(), "Old transient still active")
    fixtures = json.loads((ROOT / "tests/data/direct_result_fixed_field_anchors.json").read_text(encoding="utf8"))
    checked = 0
    for row in fixtures["anchors"]:
        if row["kind"] != "short":
            continue
        raw = bytes.fromhex(row["payload_hex"])
        primary = protocol.decode_v576_projectile_damage(raw, "server_to_client", fixtures["known_damage_skill_ids"])
        embedded = protocol.decode_v576_embedded_direct_damage_events(raw, "server_to_client", fixtures["known_damage_skill_ids"])
        identity = lambda hit: (hit.source_actor_id, hit.target_actor_id, hit.damage,
                                hit.critical, hit.damage_result_group_id, hit.damage_object_id, hit.record_offset)
        require(primary is not None and len(embedded) == 1 and identity(primary) == identity(embedded[0]),
                f"Wire identity mismatch: {row['frame']}")
        require(primary.skill_id == row["expected_skill"] and primary.skill_id_exact,
                f"Skill contract changed: {row['frame']}")
        checked += 1
    for name, expected in files.items():
        require((destination / name).read_bytes() == expected, f"Output changed during verification: {name}")
    return {"verified": True, "directory": str(destination), "changed_modules": manifest["changed_modules"],
            "unchanged_module_count": len(manifest["unchanged_modules"]), "real_short_anchors_checked": checked,
            "loaded_paths": paths, "full_corpus_replayed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--reviewed-protocol", type=Path, default=REVIEWED)
    parser.add_argument("--output", type=Path, default=DESTINATION)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--emit-file", help="Return one planned file as JSON, including exact newline bytes")
    action.add_argument("--emit-patch", action="store_true", help="Emit apply_patch additions, without writing")
    action.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    destination = args.output.resolve()
    files, manifest = make_plan(args.baseline, args.reviewed_protocol, destination)
    if args.verify:
        print(json.dumps(verify(destination, files, manifest), ensure_ascii=False, indent=2))
    elif args.emit_file:
        require(args.emit_file in files, "Unknown planned file")
        print(json.dumps({"path": str(destination / args.emit_file), "sha256": sha(files[args.emit_file]),
                          "content": files[args.emit_file].decode("utf8")}, ensure_ascii=False))
    elif args.emit_patch:
        require(not destination.exists(), "Refusing to overwrite an existing directory")
        print("*** Begin Patch")
        for name, data in files.items():
            print("*** Add File: " + (destination / name).as_posix())
            content = data.decode("utf8")
            require(content.endswith("\n"), f"No final newline: {name}")
            for line in content[:-1].split("\n"):
                print("+" + line)
        print("*** End Patch")
    else:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf8", newline="\n")
    main()
