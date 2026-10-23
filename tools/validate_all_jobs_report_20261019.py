"""Check the final local report, its evidence links and arithmetic coverage."""
import argparse,collections,json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote,urlsplit

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'outputs/verification/all_jobs_published_ab_20261019/detailed_attribution'
class Links(HTMLParser):
    def __init__(self):super().__init__();self.ids=[];self.hrefs=[];self.text=[]
    def handle_data(self,data):self.text.append(data)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if 'id' in a:self.ids.append(a['id'])
        if tag=='a' and 'href' in a:self.hrefs.append(a['href'])

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--allow-partial',action='store_true');args=parser.parse_args()
    d=json.loads((DEST/'attribution.json').read_text(encoding='utf-8'))
    v=d['validation']
    complete=not d['pending'] and not v['missing_jobs'] and v['stable_jobs_covered']==30 and v['case_pairs']==12
    if not args.allow_partial:assert complete
    assert len(d['scope']['target_jobs'])==30
    assert v['max_total_error']<1e-8 and v['max_output_ratio_error']<1e-8 and v['max_support_ratio_error']<1e-8
    assert v['max_timeline_error']<.01 and not v['cast_pairing_duplicate_rows']
    assert not v['exported_reference_mismatch_casts']
    assert all(all(module['functions'].values()) for module in d['code_provenance'])
    files=list(DEST.glob('*.html'));broken=[];duplicates=[];links=0;parsed={};bad_numbers=[]
    def parse(path):
        key=path.resolve()
        if key not in parsed:
            h=Links();h.feed(path.read_text(encoding='utf-8'));parsed[key]=h
        return parsed[key]
    for path in files:
        h=parse(path)
        if path.name.startswith('player_') and any(t.strip() in ('None','nan','NaN','Infinity') for t in h.text):
            bad_numbers.append(path.name)
        duplicates += [(path.name,key) for key,n in collections.Counter(h.ids).items() if n>1]
        for href in h.hrefs:
            links+=1;u=urlsplit(href)
            if u.scheme:continue
            if not u.path:
                if u.fragment and unquote(u.fragment) not in h.ids:broken.append((path.name,href))
            else:
                target=path.parent/unquote(u.path)
                if not target.exists():broken.append((path.name,href))
                elif u.fragment and target.suffix=='.html' and unquote(u.fragment) not in parse(target).ids:
                    broken.append((path.name,href))
    assert not broken,broken
    assert not duplicates,duplicates
    assert not bad_numbers,bad_numbers
    result={'report_files':len(files),'checked_links':links,'broken_links':broken,'duplicate_anchors':duplicates,
            'complete':complete,'validated_jobs':v['stable_jobs_covered'],'validated_case_pairs':v['case_pairs'],'validated_player_pairs':v['player_pairs'],
            'skills':v['skill_rows'],'support_effects':v['support_effect_rows'],
            'unpaired_entities':len(d['unpaired']),'unresolved_reference_groups':len(d['unresolved_reference_groups']),
            'published_function_checks':sum(len(m['functions']) for m in d['code_provenance']),
            'forward_verified_reference_casts':v['exported_reference_verified_casts'],
            'important_note':'Arithmetic reconciliation and source provenance are verified; unresolved game-mechanic references are not marked proven.'}
    (DEST/'report_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
