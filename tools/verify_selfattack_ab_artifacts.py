"""Verify completed per-capture A/B artifacts without replaying captures.

Checks the exclusive completion contract, streams JSONL hashes/counts, and
keeps interrupted and failed audits distinct. Does not infer coverage or fix
unexplained changes. Runtime is never imported.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def verify_one(complete_path):
    complete_path = Path(complete_path)
    complete = json.loads(complete_path.read_text(encoding='utf8'))
    identity = complete['capture_sha256']
    if (len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity)
            or complete_path.name != identity + '.complete.json'
            or complete.get('audit_complete') is not True):
        raise ValueError('Invalid completion identity or incomplete audit')
    if (complete.get('jsonl') != identity + '.jsonl'
            or complete.get('summary') != identity + '.summary.json'):
        raise ValueError('Sidecar names must match input hash')
    directory = complete_path.parent
    summary_path = directory / complete['summary']
    if file_hash(summary_path) != complete['summary_sha256']:
        raise ValueError('Summary hash mismatch')
    summary = json.loads(summary_path.read_text(encoding='utf8'))
    hasher = hashlib.sha256()
    count = size = 0
    first = last = None
    reasons = Counter()
    changed_messages = 0
    with (directory / complete['jsonl']).open('rb') as stream:
        for line in stream:
            hasher.update(line)
            count += 1
            size += len(line)
            row = json.loads(line)
            if count == 1:
                first = row
            elif row.get('record_type') == 'header':
                raise ValueError('Repeated header')
            if last is not None and last.get('record_type') == 'summary':
                raise ValueError('Records after terminal summary')
            if row.get('record_type') == 'message_delta':
                changed_messages += 1
                for change in row.get('unknown_change', []):
                    reasons[change.get('reason', '<missing>')] += 1
            last = row
    if (hasher.hexdigest() != complete['jsonl_sha256']
            or count != complete['jsonl_records'] or size != complete['jsonl_bytes']):
        raise ValueError('JSONL hash, bytes or line count mismatch')
    if (not first or first.get('record_type') != 'header'
            or first.get('capture_sha256') != identity
            or last != summary or summary.get('record_type') != 'summary'
            or summary.get('capture_sha256') != identity
            or summary.get('audit_complete') is not True):
        raise ValueError('Missing/mismatched header or terminal summary')
    if summary.get('mode'):
        raise ValueError('Fixture artifact is not a full-capture audit')
    counts = Counter(summary['counts'])
    before, after = summary['totals']['before'], summary['totals']['after']
    balanced = (
        before['rows'] - counts['proven_duplicate_removed'] + counts['newly_emitted'] == after['rows']
        and before['damage'] - summary['proven_duplicate_damage_removed']
            + summary['newly_emitted_damage'] == after['damage'])
    passed = balanced and counts['unknown_change'] == 0 and counts['newly_emitted'] == 0
    if (balanced != summary['accounting_balanced']
            or passed != summary['strict_existing_hit_conservation_passed']
            or passed != complete['audit_passed']
            or sum(reasons.values()) != counts['unknown_change']):
        raise ValueError('Contradictory conservation summary')
    return {
        'capture': summary['capture'], 'capture_sha256': identity,
        'artifacts_verified': True, 'audit_passed': passed,
        'counts': dict(counts), 'totals': summary['totals'],
        'duplicate_damage_removed': summary['proven_duplicate_damage_removed'],
        'unknown_reasons': dict(reasons), 'changed_message_records': changed_messages,
        'jsonl_bytes': size, 'runtime': first['runtime'],
    }


def verify_directory(directory):
    directory = Path(directory)
    rows = [verify_one(p) for p in sorted(directory.glob('*.complete.json'))]
    complete_ids = {r['capture_sha256'] for r in rows}
    partial = sorted(p.name for p in directory.glob('*.jsonl') if p.stem not in complete_ids)
    runtimes = {json.dumps(r.pop('runtime'), sort_keys=True) for r in rows}
    if len(runtimes) > 1:
        raise ValueError('Audits used different runtime/data/tool metadata')
    return {'completed_captures': len(rows), 'partial_jsonl': partial,
            'passed_captures': sum(r['audit_passed'] for r in rows), 'captures': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_directory(args.directory), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
