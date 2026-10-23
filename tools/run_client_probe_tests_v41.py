"""Run the v39/v40/v41 synthetic regressions and record their exact source scope.

Run with Python 3.12 -B. Only this runner's two reports are written; the tests'
existing in-memory fixtures and offline guards are used without modification.
No probe CLI, real checkpoint, game executable or live process is opened.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import importlib.abc
import importlib.machinery
import json
from pathlib import Path
import sys
import time
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
OUT = ROOT / "outputs/client_protection_v41_20260908"
PATTERNS = ("test_*v39.py", "test_*v40.py", "test_*v41.py")
PROBES = (
    "tools/emulate_client_entry_v39.py",
    "tools/probe_pak_wrapper_v39.py",
    "tools/emulate_client_sections_v40.py",
    "tools/client_checksum_model_v41.py",
    "tools/client_guest_snapshot_v41.py",
    "tools/run_client_probe_tests_v39.py",
)


def source_key(filename):
    """Lexical only: this also runs while the tests block filesystem access."""
    try:
        relative = Path(filename).relative_to(ROOT)
    except (TypeError, ValueError):
        return None
    if relative.suffix == ".py" and relative.parts[0] in ("tools", "tests"):
        return relative.as_posix()
    return None


class SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        # -B prevents pyc writes, but does not prevent stale pyc reads. Compile
        # local sources directly so the audit hook sees the actual input bytes.
        return self.source_to_code(self.get_data(self.path), self.path)


class SourceRecorder(importlib.abc.MetaPathFinder):
    def __init__(self):
        self.active = True
        self.hashes = defaultdict(set)

    def audit(self, event, args):
        if self.active and event == "compile":
            source, filename = args
            key = source_key(filename)
            if key and isinstance(source, bytes):
                self.hashes[key].add(sha256(source).hexdigest())

    def find_spec(self, fullname, path=None, target=None):
        if not (fullname == "tools" or fullname.startswith("tools.")
                or fullname.startswith("test_")):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if (spec and isinstance(spec.loader, importlib.machinery.SourceFileLoader)
                and source_key(spec.origin)):
            spec.loader = SourceOnlyLoader(fullname, spec.origin)
            return spec
        return None


def hashes_on_disk(keys):
    result = {}
    for key in sorted(keys):
        try:
            result[key] = sha256((ROOT / key).read_bytes()).hexdigest()
        except OSError:
            result[key] = None
    return result


def matching_files():
    return {pattern: sorted(p.relative_to(ROOT).as_posix()
                            for p in TESTS.glob(pattern)) for pattern in PATTERNS}


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def result_xml(result, elapsed):
    """Keep RecordedResult timings; use unittest's authoritative outcome lists.

    In particular, the v39 recorder labels every failed subtest as 'failure',
    even when unittest classified it as an error. Rebuild outcome children so
    the XML agrees with the real failure/error/skip counts without editing v39.
    """
    node = ET.Element("testsuite", name="client-protection-v41",
                      tests=str(result.testsRun), failures=str(len(result.failures)),
                      errors=str(len(result.errors)), skipped=str(len(result.skipped)),
                      expected_failures=str(len(result.expectedFailures)),
                      unexpected_successes=str(len(result.unexpectedSuccesses)),
                      time=f"{elapsed:.6f}")
    cases = {}
    for recorded in result.cases:
        case = ET.SubElement(node, "testcase", dict(recorded.attrib))
        cases[case.get("name")] = case
    for tag, outcomes in (("failure", result.failures), ("error", result.errors),
                          ("skipped", result.skipped),
                          ("skipped", result.expectedFailures),
                          ("failure", [(t, "Unexpected success")
                                       for t in result.unexpectedSuccesses])):
        for test, detail in outcomes:
            parent_id = getattr(test, "test_case", test).id()
            if parent_id not in cases:
                cases[parent_id] = ET.SubElement(node, "testcase", name=parent_id,
                                                 time="0", fixture="true")
            ET.SubElement(cases[parent_id], tag, message=test.id()).text = detail
    return node


def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    started_at = datetime.now(timezone.utc).isoformat()
    matched = matching_files()
    test_files = sorted({key for files in matched.values() for key in files})
    runner_key = Path(__file__).relative_to(ROOT).as_posix()
    before = hashes_on_disk({runner_key, *PROBES, *test_files})
    recorder = SourceRecorder()
    sys.addaudithook(recorder.audit)
    sys.meta_path.insert(0, recorder)
    try:
        from tools.run_client_probe_tests_v39 import RecordedResult

        def recorded_result(*args, **kwargs):
            result = RecordedResult(*args, **kwargs)
            # A setUpModule/setUpClass error or skip can precede startTest.
            # The XML is rebuilt from outcome lists, including these events.
            result.current = ET.Element("testcase")
            return result

        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        selected = {}
        for pattern in PATTERNS:
            group = loader.discover(str(TESTS), pattern=pattern)
            selected[pattern] = list(test_ids(group))
            suite.addTests(group)
        planned = [name for names in selected.values() for name in names]
        print("v41 combined regression scope:", flush=True)
        for pattern, names in selected.items():
            print(f"  {pattern}: {len(names)} tests, {len(matched[pattern])} files", flush=True)
        started = time.monotonic()
        result = unittest.TextTestRunner(verbosity=1, resultclass=recorded_result).run(suite)
        elapsed = time.monotonic() - started
    finally:
        sys.meta_path.remove(recorder)
        recorder.active = False

    # These hashes include explicit compile(SOURCE, ...) calls in the tests,
    # so they identify the bytes actually used by their frozen Python subjects.
    variants = {key: sorted(values) for key, values in sorted(recorder.hashes.items())}
    source_hashes = {key: values[0] for key, values in variants.items() if len(values) == 1}
    source_hashes[runner_key] = before[runner_key]
    after = hashes_on_disk(set(before) | set(variants))
    changed = sorted(key for key in after if
                     (key in before and before[key] != after[key]) or
                     (key in variants and (len(variants[key]) != 1 or
                      after[key] not in variants[key] or
                      (key in before and before[key] not in variants[key]))))
    matched_after = matching_files()
    duplicates = sorted(name for name, count in Counter(planned).items() if count > 1)
    missing_sources = sorted(key for key in test_files if key not in variants)
    empty_patterns = [pattern for pattern, names in selected.items() if not names]
    complete = (bool(planned) and result.testsRun == len(planned) and
                not loader.errors and not duplicates and not missing_sources and not empty_patterns)
    sources_stable = not changed and matched == matched_after
    successful = result.wasSuccessful() and complete and sources_stable
    node = result_xml(result, elapsed)
    case_reports = []
    for case in node:
        tags = {child.tag for child in case}
        status = ("error" if "error" in tags else "failure" if "failure" in tags
                  else "skipped" if "skipped" in tags else "passed")
        case_reports.append(dict(id=case.get("name"), status=status,
                                 seconds=float(case.get("time", "0")),
                                 details=[dict(kind=c.tag, id=c.get("message"), text=c.text)
                                          for c in case]))
    scope = []
    for key in test_files:
        module = Path(key).stem
        cases = [case for case in case_reports if case["id"].startswith(module + ".")]
        scope.append(dict(path=key, module=module,
                          selected=sum(name.startswith(module + ".") for name in planned),
                          recorded=len(cases), outcomes=dict(Counter(c["status"] for c in cases))))
    details = lambda entries: [dict(id=test.id(), detail=detail) for test, detail in entries]
    report = dict(
        runner="unittest", result_class="tools.run_client_probe_tests_v39.RecordedResult",
        command=[sys.executable, "-B", str(Path(__file__).resolve())],
        python_version=sys.version, started_at_utc=started_at,
        finished_at_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=elapsed,
        tests=result.testsRun, selected_tests=len(planned),
        passed=sum(c["status"] == "passed" for c in case_reports),
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        expected_failures=len(result.expectedFailures),
        unexpected_successes=len(result.unexpectedSuccesses),
        successful=successful, tests_successful=result.wasSuccessful(),
        collection_complete=complete, sources_stable=sources_stable,
        patterns=list(PATTERNS), matched_files=matched, matched_files_after=matched_after,
        scope=scope, test_ids=planned, cases=case_reports,
        failure_details=details(result.failures), error_details=details(result.errors),
        skip_details=details(result.skipped), expected_failure_details=details(result.expectedFailures),
        unexpected_success_details=[test.id() for test in result.unexpectedSuccesses],
        discovery_errors=loader.errors, duplicate_test_ids=duplicates,
        empty_patterns=empty_patterns, missing_compiled_test_sources=missing_sources,
        source_hashes=source_hashes, compiled_source_hashes=variants,
        source_hashes_before=before, source_hashes_after=after, changed_during_tests=changed,
        source_hash_method=("SHA-256 of actual local compile input bytes; local imports bypass pyc. "
                            "Runner self-hash is read at startup and checked again after tests. "
                            "Multiple compiled versions are listed and invalidate source stability."),
        fixtures=("Synthetic PE/DLL bytes, guest heap/section models, in-memory checkpoint ZIPs, "
                  "checksum differential fixtures, and v39 PAK directory/codec/wrapper probes. "
                  "Existing client-test offline guards remain active. Unicorn/Capstone are "
                  "test dependencies; no game or Windows-system DLL code is loaded or called, "
                  "no live process is read, and no real checkpoint/progress file is opened."),
        scope_note=("Includes every matching test file, including test_pak_wrapper_v39.py and "
                    "tools/probe_pak_wrapper_v39.py. Count is discovered, never fixed at 133. "
                    "Only the listed sources/tests are evidenced; this is not a client boot result."),
    )
    OUT.mkdir(parents=True, exist_ok=True)
    ET.indent(node)
    ET.ElementTree(node).write(OUT / "tests.xml", encoding="utf-8", xml_declaration=True)
    (OUT / "test_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(dict(tests=report["tests"], passed=report["passed"], failures=report["failures"],
                          errors=report["errors"], skipped=report["skipped"], successful=successful,
                          sources_stable=sources_stable, output=str(OUT)), ensure_ascii=True), flush=True)
    return 0 if successful else 1


if __name__ == "__main__":
    sys.exit(main())
