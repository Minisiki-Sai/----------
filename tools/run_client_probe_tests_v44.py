"""Joint offline analysis regressions; no game launch or production mutation."""
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import run_client_probe_tests_v41 as runner

if __name__ == "__main__":
    runner.OUT = ROOT / "outputs/client_protection_v44_20260908"
    runner.PATTERNS = (*runner.PATTERNS,"test_client_*v42.py","test_client_*v43.py","test_client_*v44.py")
    runner.PROBES = (*runner.PROBES,"tools/emulate_client_file_v42.py",
        "tools/continue_client_v42.py","tools/client_file_checksum_v42.py",
        "tools/client_virtual_protection_v43.py","tools/extract_client_segments_v44.py",
        "tools/run_client_probe_tests_v44.py","tools/review_client_stream_descriptors_v43.py",
        "tools/review_client_crypto_tables_v44.py")
    status = runner.main()
    # The reused implementation labels its own module path. Preserve that
    # provenance, but record the actual wrapper command and added fixtures too.
    report_path = runner.OUT / "test_run.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["implementation_runner_command"] = report["command"]
    report["command"] = [sys.executable,"-B",str(Path(__file__).resolve())]
    report["additional_v44_fixtures"] = (
        "Hash-pinned original client read as bytes, prior independent descriptor "
        "evidence, six raw LZMA streams, synthetic malformed/truncated streams, "
        "temporary .bin artifacts and public FIPS finite-field/table examples. "
        "No client execution, checkpoint replacement or production changes."
    )
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n","utf-8")
    xml_path = runner.OUT / "tests.xml"
    tree = ET.parse(xml_path)
    tree.getroot().set("name","client-protection-v44")
    tree.write(xml_path,encoding="utf-8",xml_declaration=True)
    sys.exit(status)
