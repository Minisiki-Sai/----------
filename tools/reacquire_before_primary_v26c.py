"""One normal BEFORE-only replay to fill missing primary audit observations.

Never changes event values, never reruns AFTER, never changes the original
audit. Certification requires the ENTIRE old before stream to match, plus
exact focused rows and current primary/binding observations. Default: no run.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import audit_selfattack_ab_v26c as audit
from tools.verify_selfattack_ab_artifacts import verify_one

REVIEW_PATH = ROOT / 'outputs/v26c_incoming_projection_offline_20260908/audit_incoming_projection.py'
spec = importlib.util.spec_from_file_location('before_projection_review', REVIEW_PATH)
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)


class FocusStore:
    def __init__(self, selected):
        self.expected = {audit.packet_key(row['message']): row for row, _ in selected}
        self.observed = {}
        self.summary = audit.Summary()

    def __setitem__(self, key, value):
        message, trace = value
        result = {**{category: [] for category in audit.CATEGORIES},
                  'message': message, 'before': trace, 'after': trace, 'unchanged': True}
        self.summary.add(result)
        if key not in self.expected:
            return
        if key in self.observed:
            raise ValueError('Repeated focused message')
        original = self.expected[key]
        if Counter(map(audit.canonical, original['before']['rows'])) != Counter(map(audit.canonical, trace['rows'])):
            raise ValueError('Focused BEFORE events changed; cannot join the old AFTER')
        self.observed[key] = trace


def observed_probe_type(focus_hashes):
    base = audit.Probe

    class ObservedBeforeProbe(base):
        def __init__(self, decoder, enabled, event_type):
            if enabled:
                raise ValueError('BEFORE-only observation')
            super().__init__(decoder, enabled, event_type)
            original_noop = decoder._repair_proven_entity_target

            def observe(packet, decoded):
                wire_hash = audit.digest(packet.payload)
                if wire_hash in focus_hashes and getattr(decoded, 'kind', '') == 'damage_hit':
                    binding = decoder.entity_owner_evidence.bindings.get(decoded.source_actor_id)
                    self.calls.append({
                        'hook': 'observed_before_primary', 'decoded': audit.decoded_fields(decoded),
                        'alternate_target_actor_id': getattr(decoded, 'alternate_target_actor_id', None),
                        'wire_sha256': wire_hash,
                        'packet_serial': decoder._damage_object_packet_serial,
                        'binding': asdict(binding) if binding else None,
                    })
                result = original_noop(packet, decoded)
                if result is not decoded:
                    raise ValueError('Before noop changed the decoded object')
                return result

            decoder._repair_proven_entity_target = observe

    return ObservedBeforeProbe


def certify_primary(candidate, calls):
    if not candidate.get('structural_candidate'):
        return False
    repair = candidate['repair_call']
    matching = [call for call in calls if (
        call.get('hook') == 'observed_before_primary'
        and call['wire_sha256'] == repair['wire_sha256']
        and call['decoded'] == repair['before']
        and audit.canonical(call['binding']) == audit.canonical(repair['binding'])
        and audit.canonical(call['binding']) == audit.canonical(candidate['self_attack_call']['binding']))]
    return len(matching) == 1 and len(calls) == 1


def run_one(runtime, path, output_dir):
    collector_hash = audit.file_digest(Path(__file__))
    reviewer_hash = audit.file_digest(REVIEW_PATH)
    complete = path.with_suffix('.complete.json')
    verify_one(complete)
    selected, header, original_summary, marker = reviewer.read_complete(path)
    if header['runtime'] != runtime[3]:
        raise ValueError('Original runtime/data/tool metadata differ; no cross-version join')
    capture = Path(header['capture'])
    capture_hash = header['capture_sha256']
    if audit.file_digest(capture) != capture_hash:
        raise ValueError('Capture changed since original A/B')
    target = output_dir / (capture_hash + '.json')
    if target.exists():
        raise FileExistsError(target)
    store = FocusStore(selected)
    focus_hashes = {row['message']['wire_sha256'] for row, _ in selected}
    previous_probe = audit.Probe
    audit.Probe = observed_probe_type(focus_hashes)
    try:
        metrics = audit._capture_pass(runtime, capture, capture_hash, False, store, audit.Summary(), lambda _: None)
    finally:
        audit.Probe = previous_probe
    references = metrics.pop('references')
    metrics['gc_collected'] = gc.collect()
    metrics['released'] = {name: reference() is None for name, reference in references.items()}
    if not all(metrics['released'].values()):
        raise ValueError('Before lifetime was retained')
    audit.verify_runtime(runtime[3])
    measured = store.summary.result()
    if (measured['totals']['before'] != original_summary['totals']['before']
            or measured['seven_field_stream_sha256']['before'] != original_summary['seven_field_stream_sha256']['before']
            or measured['messages'] != original_summary['messages']
            or set(store.observed) != set(store.expected)
            or metrics['messages_fed'] != original_summary['memory_diagnostics']['pass_lifetimes'][0]['messages_fed']):
        raise ValueError('Whole BEFORE stream or focused inventory differs')
    if (audit.file_digest(capture) != capture_hash or audit.file_digest(path) != marker['jsonl_sha256']
            or collector_hash != audit.file_digest(Path(__file__)) or reviewer_hash != audit.file_digest(REVIEW_PATH)):
        raise ValueError('Original input changed during reacquisition')
    specs = reviewer.load_specs()
    items = []
    for row, reference in selected:
        candidate = reviewer.review(row, specs)
        trace = store.observed[audit.packet_key(row['message'])]
        certified = certify_primary(candidate, trace['calls'])
        items.append({'original_reference': reference, 'original_message_delta': row,
                      'structural_review': candidate, 'observed_before': trace,
                      'certified': certified})
    unresolved = [item['structural_review']['message_index'] for item in items if not item['certified']]
    certified = [item['structural_review'] for item in items if item['certified']]
    counts = Counter(item['category'] for item in certified)
    duplicates = counts['candidate_spurious_incoming_duplicate']
    conversions = counts['candidate_incoming_projection_retyped_as_outgoing']
    removed = sum(item['candidate_duplicate_damage'] for item in certified)
    original_counts = Counter(original_summary['counts'])
    resolved_unknown = sum(item['original_unknown'] for item in certified)
    resolved_new = sum(item['original_newly'] for item in certified)
    before, after = original_summary['totals']['before'], original_summary['totals']['after']
    balanced = before['rows'] - duplicates == after['rows'] and before['damage'] - removed == after['damage']
    passed = (not unresolved and balanced and resolved_unknown == original_counts['unknown_change']
              and resolved_new == original_counts['newly_emitted'])
    result = {'schema': 'observed-before-primary-supplement.1', 'capture': str(capture),
        'capture_sha256': capture_hash, 'runtime': runtime[3],
        'original_jsonl': str(path), 'original_jsonl_sha256': marker['jsonl_sha256'],
        'collector_sha256': collector_hash, 'reviewer_sha256': reviewer_hash,
        'before_stream_matches_original': True, 'whole_before': measured, 'lifetime': metrics,
        'focused_messages': len(items), 'certified_messages': len(certified),
        'spurious_incoming_duplicates_removed': duplicates, 'duplicate_damage_removed': removed,
        'incoming_reclassified_as_outgoing': conversions,
        'original_unknown_resolved': resolved_unknown, 'original_newly_resolved': resolved_new,
        'unresolved_message_indexes': unresolved, 'strict_supplement_passed': passed,
        'original_audit_unchanged': True, 'evidence': items}
    output_dir.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf8') as stream:
        json.dump(result, stream, ensure_ascii=False, separators=(',', ':'))
    with target.with_suffix('.complete.json').open('x', encoding='utf8') as stream:
        json.dump({'capture_sha256': capture_hash, 'report_sha256': audit.file_digest(target),
                   'report_bytes': target.stat().st_size, 'complete': True, 'passed': passed}, stream)
    print(audit.canonical({k: v for k, v in result.items() if k not in (
        'runtime', 'evidence', 'whole_before', 'lifetime')}), flush=True)
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--allow-before-replay', action='store_true')
    args = parser.parse_args()
    if not args.allow_before_replay:
        parser.error('Explicit --allow-before-replay required')
    runtime = audit.load_runtime()
    paths = sorted(args.audit_dir.glob('*.jsonl'))
    if not paths:
        raise ValueError('No complete A/B inputs')
    passed = True
    for path in paths:
        passed &= run_one(runtime, path, args.output_dir)
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())
