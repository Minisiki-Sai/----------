"""Compare saved native examples with frozen admitted-event witnesses.

This is SAMPLE review, not new coverage. No replay, mutation, score or upload.
Only an exact unique physical-key match is reported; no nearest-time matching.
"""
from collections import Counter,defaultdict
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/native_wire_v47_20260909'
BASE=ROOT/'outputs/magma_runtime_v37_20260908/after_results'


def event_key(row):
    # Columns pinned in the source result. group here is legacy raw metadata,
    # not evidence of a unique result group/skill, and is never used alone.
    return row[7],row[8],row[3],row[4],row[5],row[6],row[9]


def candidate_key(sample):
    hit=sample['hit'];record=bytes.fromhex(hit['record_hex']);fields=hit['fields']
    if fields['namespace']!='index':return None
    return (fields['source_id'],fields['target_id'],fields['damage'],fields['critical'],
        int.from_bytes(record[8:12],'little'),fields['reference'],hit['marker'])


def main():
    corpus=OUT/'corpus_summary.json';raw=corpus.read_bytes();summary=json.loads(raw)
    if summary['successful']!=35 or not summary['all_physical_fields_unchanged']:
        raise ValueError('complete uniform shadow corpus required first')
    rows=[];stats=Counter();methods=Counter();inputs={str(corpus):sha256(raw).hexdigest()}
    for member in summary['reports']:
        report_path=Path(member['report']);report_raw=report_path.read_bytes()
        if sha256(report_raw).hexdigest()!=member['report_sha256']:raise ValueError('shadow report changed')
        shadow=json.loads(report_raw)
        selected=[e for e in shadow['additional_prefix_candidates']['examples'] if e['existing_spec']]
        if not selected:continue
        baseline_path=BASE/(sha256(shadow['capture'].encode()).hexdigest()+'.json')
        baseline_raw=baseline_path.read_bytes();baseline=json.loads(baseline_raw)
        if baseline['capture_sha256']!=shadow['capture_sha256']:raise ValueError('capture differs')
        if baseline['physical_columns']!=['time','source','target','amount','critical','group','object','wire_source','wire_target','offset','real','skill','method']:
            raise ValueError('column schema changed')
        inputs[str(report_path)]=sha256(report_raw).hexdigest();inputs[str(baseline_path)]=sha256(baseline_raw).hexdigest()
        keys={candidate_key(e) for e in selected};index=defaultdict(list)
        for event in baseline['aggregated_physical_rows']:
            key=event_key(event)
            if key in keys:index[key].append(event)
        for sample in selected:
            key=candidate_key(sample);matches=index.get(key,[])
            status='no_exact_accepted_row' if not matches else ('unique_physical_key' if len(matches)==1 else 'ambiguous_physical_key')
            stats[status]+=1
            if len(matches)==1:methods[str(matches[0][-1])]+=1
            rows.append(dict(capture=Path(shadow['capture']).name,
                native_config=sample['registration']['object']['config_row'],
                root=sample['registration']['object']['root_context_known'],
                spec=sample['existing_spec'],status=status,physical_key=key,
                matched_accepted_rows=matches,registration=sample['registration'],hit=sample['hit']))
    result=dict(schema='native-candidate-existing-event-review-v48',inputs_sha256=inputs,
        counts=dict(stats),existing_methods=dict(methods),examples=rows,
        new_confirmed_hits=0,production_changed=False,
        limitations=['Capped saved examples, not full event/coverage audit.',
            'Existing accepted witnesses are historical v37, not a fresh current-runtime score replay.',
            'Unique full-key equality is a cross-check, not independent wire frame identity or native skill proof.',
            'No promotion of numeric/context/suffix matches; multiple physical matches remain ambiguous.'])
    (OUT/'candidate_existing_events.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(counts=dict(stats),methods=dict(methods),rows=len(rows)),ensure_ascii=False))


if __name__=='__main__':main()
