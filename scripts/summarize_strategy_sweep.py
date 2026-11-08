"""Descriptive six-arm analysis. Uses all planned attempts; no inferred accuracy."""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from strategy_sweep import audit, paths


def aggregate(rows):
    known=sum(r['known_tokens'] for r in rows)
    attempted=[r for r in rows if r['status']!='missing']
    complete_cost=all(r['returned_tokens'] is not None for r in rows)
    fields=[r.get('field_presence_rate') or 0 for r in rows]
    lines=sum(r.get('total_read_lines',0) for r in rows)
    unique=sum(r.get('unique_read_lines',0) for r in rows)
    return dict(planned=len(rows),executed=len(attempted),contract_passes=sum(r['contract_pass'] for r in rows),
        fully_populated_reports=sum(r['contract_pass'] and r.get('field_presence_rate')==1 for r in rows),
        mean_field_presence=sum(fields)/len(rows),known_tokens=known,
        mean_returned_tokens=round(known/len(rows),2) if complete_cost else None,
        total_read_lines=lines,unique_read_lines_sum=unique,
        repeated_line_fraction=round(1-unique/lines,4) if lines else None,
        rejected_drafts=sum(r.get('rejected_drafts',0) for r in rows),
        invalid_read_calls=sum(r.get('invalid_read_calls',0) for r in rows),
        final_phase_calls=sum(r.get('final_phase_calls',0) for r in rows),
        length_stops=sum(r.get('length_stops',0) for r in rows),
        semantic_accuracy=None,human_rubric_score=None)


def describe(summary):
    groups=defaultdict(list); by_task=defaultdict(list)
    for row in summary['results']:
        groups[row['arm']].append(row); by_task[row['task'],row['arm']].append(row)
    baseline={r['task']+str(r['repeat']):r for r in groups['baseline']}
    contrasts={}
    for arm,rows in groups.items():
        if arm=='baseline': continue
        paired=[]
        for row in rows:
            base=baseline[row['task']+str(row['repeat'])]
            paired.append(dict(task=row['task'],repeat=row['repeat'],
                contract_delta=int(row['contract_pass'])-int(base['contract_pass']),
                token_delta=row['returned_tokens']-base['returned_tokens']
                    if row['returned_tokens'] is not None and base['returned_tokens'] is not None else None))
        contrasts[arm]=paired
    return dict(study=summary['study'],by_arm={key:aggregate(value) for key,value in groups.items()},
        by_task=[dict(task=key[0],arm=key[1],**aggregate(value)) for key,value in by_task.items()],
        matched_descriptive_deltas=contrasts,semantic_accuracy=None,human_rubric_score=None,
        limitations=['Two tasks share the same two previously observed papers; repeats are not new independent tasks.',
            'These are separately generated trajectories, not branches sharing an upstream trajectory.',
            'Matched task/repeat deltas are descriptive; no significance test or confidence interval is justified here.',
            'Failed and missing attempts count against delivery; missing or unknown costs do not become zero means.',
            'Field presence, line depth and reduced overlap do not imply correctness or semantic support.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=paths()[1])
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    result=describe(audit(args.root))
    with args.out.open('x',encoding='utf-8') as stream: json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result['by_arm']))
