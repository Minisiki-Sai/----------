"""Reproducible synthetic-fixture regression run; never starts the game."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_client_probe_tests_v39 import RecordedResult


if __name__ == '__main__':
    paths = [ROOT/'tools/emulate_client_entry_v39.py', ROOT/'tools/emulate_client_sections_v40.py',
        *sorted((ROOT/'tests').glob('test_*v39.py')), ROOT/'tests/test_client_sections_v40.py']
    before = {str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in paths}
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.discover(str(ROOT/'tests'), pattern='test_*v39.py'),
        loader.discover(str(ROOT/'tests'), pattern='test_client_sections_v40.py')])
    result = unittest.TextTestRunner(verbosity=1, resultclass=RecordedResult).run(suite)
    changed = [str(p.relative_to(ROOT)) for p in paths if sha256(p.read_bytes()).hexdigest()!=before[str(p.relative_to(ROOT))]]
    node = ET.Element('testsuite', name='client-protection-v40', tests=str(result.testsRun),
        failures=str(len(result.failures)), errors=str(len(result.errors)), skipped=str(len(result.skipped)))
    node.extend(result.cases)
    out = ROOT/'outputs/client_protection_v40_20260908'
    out.mkdir(exist_ok=True)
    ET.ElementTree(node).write(out/'tests.xml', encoding='utf-8', xml_declaration=True)
    (out/'test_run.json').write_text(json.dumps(dict(runner='unittest', tests=result.testsRun,
        successful=result.wasSuccessful() and not changed, source_hashes=before, changed_during_tests=changed,
        fixtures='Synthetic PE images and mocked DLL bytes; no game execution or host API forwarding.'),
        ensure_ascii=False,indent=2),'utf-8')
    sys.exit(not result.wasSuccessful() or bool(changed))
