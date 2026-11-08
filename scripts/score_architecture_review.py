"""Validate two returned report-review packets and summarize agreement.

Blank labels stay missing; disagreements produce an adjudication queue. Ratings
are not truth, and no semantic accuracy is inferred from reviewer agreement.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import zipfile


def require(condition,message):
    if not condition: raise ValueError(message)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(labels,template,evidence):
    require(labels.get('annotation_source')=='human','Expected declared human labels')
    require(labels.get('slot')==template['slot'],'Reviewer slot mismatch')
    expected={(r['id'],i['rubric_id']) for r in template['reports'] for i in r['items']}
    expected_reports={r['id'] for r in template['reports']}
    rows=labels.get('reports',[])
    require(len(rows)==len(expected_reports) and {r['id'] for r in rows}==expected_reports,'Missing, duplicate or foreign report')
    values={}
    for report in rows:
        spans={e['id']:(e['start_line'],e['end_line']) for e in evidence[report['id']]}
        for item in report['items']:
            key=(report['id'],item['rubric_id'])
            require(key in expected and key not in values,'Duplicate or foreign rubric')
            score=item.get('score')
            require(score is None or type(score) is int and score in (0,1),'Scores must be 0, 1, or null')
            if score is not None:
                require(isinstance(item.get('rationale'),str) and item['rationale'].strip(),'Scored items need a rationale')
            for span in item.get('evidence_spans',[]):
                require(span.get('evidence_id') in spans,'Foreign evidence ID')
                start,end=span.get('start_line'),span.get('end_line')
                low,high=spans[span['evidence_id']]
                require(type(start) is int and type(end) is int and low<=start<=end<=high,'Span outside canonical read')
            values[key]=item
    require(set(values)==expected,'Missing rubric items')
    if any(i['score'] is not None for i in values.values()):
        require(isinstance(labels.get('annotator'),str) and labels['annotator'].strip(),'Scored labels need an annotator')
    return values


def agreement(pairs):
    count=len(pairs)
    if not count: return dict(paired_items=0,agreement=None,cohen_kappa=None)
    observed=sum(a==b for a,b in pairs)/count
    a_counts,b_counts=Counter(a for a,b in pairs),Counter(b for a,b in pairs)
    expected=sum(a_counts[k]*b_counts[k] for k in (0,1))/count**2
    return dict(paired_items=count,agreement=observed,
        cohen_kappa=(observed-expected)/(1-expected) if expected<1 else None)


def score(packet_dir,a_path,b_path):
    manifest=load(packet_dir/'packet-manifest.json')
    mapping=load(packet_dir/'coordinator-mapping.json')
    require(sha(packet_dir/'coordinator-mapping.json')==manifest['coordinator_mapping_sha256'],'Coordinator mapping changed')
    submitted=[load(a_path),load(b_path)]; values=[]
    for index,slot in enumerate(('annotator-a','annotator-b')):
        name=slot+'.zip'
        require(sha(packet_dir/name)==manifest['packets'][name],'Original packet changed')
        with zipfile.ZipFile(packet_dir/name) as packet:
            template=json.loads(packet.read('labels.json'))
            evidence=json.loads(packet.read('evidence-index.json'))
        values.append(validate(submitted[index],template,evidence))
    require(set(values[0])==set(values[1]),'Reviewers evaluated different items')
    if any(i['score'] is not None for value in values for i in value.values()):
        first,second=[str(s.get('annotator') or '').strip().casefold() for s in submitted]
        require(not first or not second or first!=second,'Need two distinct independent annotator identifiers')
    case_by_id={r['id']:r for r in mapping['mapping']}
    require({key[0] for key in values[0]}==set(case_by_id),'Mapping/report identity mismatch')
    pairs=[]; by_rubric=defaultdict(list); by_condition=defaultdict(lambda:[[],[]]); queue=[]
    for key in sorted(values[0]):
        a,b=[value[key]['score'] for value in values]
        if a is not None and b is not None:
            pairs.append((a,b)); by_rubric[key[1]].append((a,b))
        if a is None or b is None or a!=b:
            queue.append(dict(report_id=key[0],rubric_id=key[1],scores=[a,b],
                reason='missing' if a is None or b is None else 'disagreement',adjudicated_score=None))
        condition=case_by_id[key[0]]['condition']
        for index,rating in enumerate((a,b)):
            if rating is not None: by_condition[condition][index].append(rating)
    completed=[sum(i['score'] is not None for i in value.values()) for value in values]
    return dict(study=manifest['study'],status='pending_human_labels' if not any(completed) else 'independent_labels_received',
        planned_attempts=mapping['planned_attempts'],delivered_reports=len(case_by_id),
        rubric_items_per_annotator=len(values[0]),completed_items_by_annotator=completed,
        missing_items_by_annotator=[len(values[i])-completed[i] for i in (0,1)],
        pooled_agreement=agreement(pairs),by_rubric={key:agreement(value) for key,value in by_rubric.items()},
        per_condition_reviewer_mean={key:[sum(v)/len(v) if v else None for v in value] for key,value in by_condition.items()},
        adjudication_queue=queue,adjudicated_human_score=None,semantic_accuracy=None,
        limitations=['Agreement uses paired report/rubric items, which are not independent samples.',
            'Reviewer identifiers and human provenance are declarations, not externally authenticated.',
            'Condition means include only supplied ratings; missing items and failed attempts are separately retained.',
            'No reviewer average or agreement statistic is treated as semantic ground truth.'],
        input_sha256=dict(packet_manifest=sha(packet_dir/'packet-manifest.json'),a=sha(a_path),b=sha(b_path)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet-dir',required=True,type=Path)
    parser.add_argument('--a',required=True,type=Path)
    parser.add_argument('--b',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    result=score(args.packet_dir,args.a,args.b)
    with args.out.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result[k] for k in ('status','completed_items_by_annotator','pooled_agreement','semantic_accuracy')}))
