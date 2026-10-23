"""Verify hash-pinned current-client projectile ACT metadata writer evidence."""
import argparse, hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--image", type=Path, default=ROOT / "outputs/client_unpack_with_udp_key/dragonnest_reborn.memory-image.bin")
args = parser.parse_args()
evidence = json.loads((ROOT / "outputs/verification/native_projectile_metadata_v475/evidence.json").read_text(encoding="utf8"))
raw = args.image.read_bytes()
assert hashlib.sha256(raw).hexdigest() == evidence["image_sha256"]
for item in evidence["native_instruction_quotes"] + evidence["tables"]:
    rva = int(item["rva"], 16)
    expected = bytes.fromhex(item["bytes"])
    assert raw[rva:rva + len(expected)] == expected, item
print(json.dumps({"image_sha256": evidence["image_sha256"], "verified_instructions": len(evidence["native_instruction_quotes"]), "verified_switch_tables": len(evidence["tables"])}, indent=2))
