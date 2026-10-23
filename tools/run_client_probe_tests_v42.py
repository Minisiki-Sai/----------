"""Run all bounded v39-v42 diagnostic regressions and freeze source identities."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import run_client_probe_tests_v41 as runner

if __name__=='__main__':
    runner.OUT=ROOT/'outputs/client_protection_v42_20260908'
    runner.PATTERNS=(*runner.PATTERNS,'test_client_*v42.py','test_client_*v43.py')
    runner.PROBES=(*runner.PROBES,'tools/emulate_client_file_v42.py',
        'tools/continue_client_v42.py','tools/client_file_checksum_v42.py',
        'tools/run_client_probe_tests_v42.py','tools/client_virtual_protection_v43.py')
    sys.exit(runner.main())
