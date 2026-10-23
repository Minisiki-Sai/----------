"""Run the isolated v39 diagnostic tests with standard unittest and save XML."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import time
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/client_protection_v39_20260908'


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def startTest(self, test):
        self.current = ET.Element('testcase', name=test.id())
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.current.set('time', str(time.monotonic()-self.started))
        self.cases.append(self.current)
        super().stopTest(test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        ET.SubElement(self.current, 'failure').text = self._exc_info_to_string(err, test)

    def addError(self, test, err):
        super().addError(test, err)
        ET.SubElement(self.current, 'error').text = self._exc_info_to_string(err, test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        ET.SubElement(self.current, 'skipped').text = reason

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            ET.SubElement(self.current, 'failure', message=str(subtest)).text = self._exc_info_to_string(err, test)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'), pattern='test_*v39.py')
    result = unittest.TextTestRunner(verbosity=1, resultclass=RecordedResult).run(suite)
    node = ET.Element('testsuite', name='client-protection-v39', tests=str(result.testsRun),
        failures=str(len(result.failures)), errors=str(len(result.errors)), skipped=str(len(result.skipped)))
    node.extend(result.cases)
    OUT.mkdir(exist_ok=True)
    ET.ElementTree(node).write(OUT/'tests.xml', encoding='utf-8', xml_declaration=True)
    sources = [*sorted((ROOT/'tests').glob('test_*v39.py')), ROOT/'tools/emulate_client_entry_v39.py']
    (OUT/'test_run.json').write_text(json.dumps(dict(runner='unittest', tests=result.testsRun,
        successful=result.wasSuccessful(), source_hashes={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in sources},
        initial_pytest_attempt='70 passed, one default-rerun guard failure; also printed native access-violation diagnostics but continued to normal test completion. The guard failure was fixed. No claim is made here that native diagnostic logging has been explained.'), ensure_ascii=False, indent=2), 'utf-8')
    sys.exit(not result.wasSuccessful())
