"""Read only the five requested database records; never update cloud scores."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NODE = Path("C:/Users/gcg59/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")
CLI = Path("C:/Users/gcg59/AppData/Local/pnpm/store/v11/links/@cloudbase/cli/3.8.5/1debc631389eb93a3f8a18251ef3aaa3493ef90f403579f14b4b812281a1d884/node_modules/@cloudbase/cli/dist/standalone/cli.js")
records = json.loads((ROOT / "work/sunday_nine_tail_cloud_20261011.json").read_text(encoding="utf-8"))
ids = [r["historyId"] for r in records]
assert all(len(value.split(":")[0]) == 64 and set(value.split(":")[0]) <= set("0123456789abcdef") for value in ids)
quoted = ",".join("'" + value + "'" for value in ids)
sql = f"select row_fingerprint,x.score_detail, (select row_to_json(f) from public.team_score_capture_files f where f.capture_hash=s.capture_hash) as capture_file from public.team_scores s left join public.team_score_extended_details x on x.team_score_id=s.id where row_fingerprint in ({quoted})"
process = subprocess.run([str(NODE), str(CLI), "db", "execute", "-e", "dragonnest-d0gy32zvzd63b6c52", "--sql", sql, "--json"],
                         capture_output=True, encoding="utf-8", timeout=60)
if process.returncode:
    raise RuntimeError("Read-only database query failed; no cloud state changed")
payload = json.loads(process.stdout)["data"]
rows = [json.loads(row) for row in payload.get("Rows") or []]
decoded = [{name: (json.loads(value) if isinstance(value, str) and value.startswith(("{", "[")) else value)
            for name, value in zip(payload["Columns"], row)} for row in rows]
output = ROOT / "work/sunday_full_score_inputs_20261011.json"
output.write_text(json.dumps(decoded, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"records": len(decoded), "raw_captures_available": sum(bool(row["capture_file"]) for row in decoded),
                  "detail_keys": [list((row["score_detail"] or {}).keys()) for row in decoded], "saved": str(output)}, ensure_ascii=False))
