"""Audit frozen Agent development cases and measure read overlap, without a model.

Mechanical delivery, content support and human research quality remain separate.
Original files are hashed byte-for-byte; publication must disable Git EOL conversion.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend/app'))
from research_backend.schemas import SourceCreate
from research_backend.benchmarks.reports import inspect_prediction

ARTIFACTS = ('run.json','trace.json','tool-calls.json','reads.json','report-drafts.json',
             'sources.json','summary.json','report.md')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inspect_case(folder, task, manifest):
    run, trace, reads, calls, sources = [load(folder/n) for n in
        ('run.json','trace.json','reads.json','tool-calls.json','sources.json')]
    documents = {}
    for source in sources:
        specification = next((s for s in task['sources'] if s['uri'] == source['uri']), None)
        require(specification is not None, 'Unexpected source')
        name = specification['id']
        paper = ROOT/'frontend/public/demo/papers'/name
        for suffix in ('.txt','.blocks.json'):
            require(sha(paper.with_suffix(suffix)) == manifest['input_sha256']['inputs/'+name+suffix], 'Source file changed')
        blocks = SourceCreate(title=source['title'], blocks=load(paper.with_suffix('.blocks.json'))).model_dump()['blocks']
        digest = hashlib.sha256(json.dumps(blocks,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        require(digest == source['source_hash'], 'Source version mismatch')
        documents[source['id']] = dict(title=source['title'], text=paper.with_suffix('.txt').read_text(encoding='utf-8'))
    require(len(documents) == len(task['sources']) == len(sources), 'Missing or duplicate source')
    require(reads == [c['data'] for c in calls if c['name']=='sources_read' and c['status']=='succeeded'], 'Read export mismatch')
    canonical, occupied, total_lines = {}, set(), 0
    for read in reads:
        source = next(s for s in sources if s['id']==read['source_id'])
        signature = f"{read['source_id']}:{source['source_hash']}:{read['start_line']}:{read['end_line']}"
        require(read['id'] == 'ev_'+hashlib.sha256(signature.encode()).hexdigest()[:32], 'Evidence identity mismatch')
        require(read['source_hash'] == source['source_hash'], 'Evidence version mismatch')
        if read['id'] in canonical:
            require(read == canonical[read['id']], 'Evidence was rewritten')
        canonical[read['id']] = read
        total_lines += read['end_line']-read['start_line']+1
        occupied.update((read['source_id'],line) for line in range(read['start_line'],read['end_line']+1))
    costs = [c['tokens_used'] for c in trace['calls']]
    known = sum(c or 0 for c in costs)
    total = None if any(c is None for c in costs) else known
    require(all(c is None or type(c) is int and c>=0 for c in costs), 'Invalid cost')
    require(all(c['tokens_used'] is None or c['prompt_tokens']+c['completion_tokens']==c['tokens_used'] for c in trace['calls']), 'Usage components mismatch')
    summary = load(folder/'summary.json')
    require(summary['tokens_used']==total and summary['known_tokens_used']==known, 'Summary usage mismatch')
    require(summary['model_calls']==len(costs) and summary['status']==run['status'], 'Run summary drift')
    require(total is None or total==run['tokens_used'], 'Run cost drift')
    require(summary['semantic_accuracy'] is None and summary['human_rubric_score'] is None, 'Human scores must remain pending')
    require(run['memory_mode']==manifest.get('memory_mode','legacy'), 'Memory mode drift')
    for key in ('max_tokens','max_steps','max_seconds','field_mode','max_field_repairs'):
        require(run[key]==manifest[key], 'Run configuration drift: '+key)
    require(run['retrieval_strategy']==manifest.get('retrieval_strategy','grep') and run['allow_network'] is False, 'Retrieval drift')
    require(sorted((f['source_id'],f['name'],f['criterion']) for f in run['required_fields']) ==
        sorted((s['id'],f['name'],f['criterion']) for s in sources for f in task['fields']), 'Requirement drift')
    prediction = dict(status=run['status'], report=run.get('report'), tokens_used=total,
        known_tokens_used=known, reads=list(canonical.values()))
    inspection = inspect_prediction(prediction, dict(source_ids=list(documents),
        targets=[dict(source_id=sid,field=f['name']) for sid in documents for f in task['fields']]),documents,manifest['max_tokens'])
    require('noncanonical_read' not in inspection['errors'], 'Noncanonical source quote')
    decisions = [e['data'] for e in trace['events'] if e['type']=='budget_decision']
    return dict(status=run['status'], memory_mode=run['memory_mode'],
        model_calls=len(costs), returned_tokens=total, known_tokens=known,
        input_tokens=sum(c['prompt_tokens'] or 0 for c in trace['calls']),
        output_tokens=sum(c['completion_tokens'] or 0 for c in trace['calls']),
        elapsed_seconds=summary['elapsed_seconds'],
        contract_pass=inspection['contract_pass'], mechanical_errors=inspection['errors'],
        read_calls=len(reads), search_calls=sum(c['name']=='sources_search' for c in calls),
        read_sources=len({r['source_id'] for r in reads}), required_sources=len(documents),
        unique_read_windows=len(canonical), total_read_lines=total_lines, unique_read_lines=len(occupied),
        repeated_line_fraction=round(1-len(occupied)/total_lines,4) if total_lines else None,
        unique_lines_after_80=sum(line>80 for _,line in occupied),
        field_presence_rate=inspection['field_presence_rate'], cited_source_coverage=inspection['cited_source_coverage'],
        proposal_count=inspection['proposal_count'], semantic_accuracy=None, human_rubric_score=None,
        rejected_drafts=sum(d['validation_status']=='rejected' for d in load(folder/'report-drafts.json')),
        stop_reason=run.get('field_state',{}).get('stop_reason'),
        phase_sequence=[d['phase'] for d in decisions],
        max_input_tokens=max((c['prompt_tokens'] or 0 for c in trace['calls']),default=0),
        location_note='Line depth and non-overlap are diagnostics, not evidence relevance or answer correctness.')


def summarize(directory):
    plan, task = load(directory/'plan.json'), load(directory/'task.json')
    rows = []
    for name, expected in plan['manifest_sha256'].items():
        folder = directory/name
        require(sha(folder/'manifest.json')==expected, 'Manifest changed')
        manifest = load(folder/'manifest.json')
        require(sha(directory/'task.json')==manifest['input_sha256']['inputs/task.json'], 'Task changed')
        require(sha(directory/'rubric.json')==manifest['rubric_sha256'], 'Rubric changed')
        row = inspect_case(folder,task,manifest)
        row.update(case=name, artifact_sha256={n:sha(folder/n) for n in ('manifest.json',)+ARTIFACTS})
        rows.append(row)
    require(all(i['score'] is None for i in load(directory/'rubric.json')['items']), 'Rubric has unreviewed scores')
    return dict(plan_sha256=sha(directory/'plan.json'), planned_attempts=len(plan['manifest_sha256']),
        completed_attempts=len(rows), contract_passes=sum(r['contract_pass'] for r in rows),
        known_tokens=sum(r['known_tokens'] for r in rows),
        returned_tokens=None if any(r['returned_tokens'] is None for r in rows) else sum(r['returned_tokens'] for r in rows),
        model_calls=sum(r['model_calls'] for r in rows), semantic_accuracy=None,human_rubric_score=None,
        results=rows)


def audit(directory):
    expected = load(directory/'summary.json')
    actual = summarize(directory)
    require(expected==actual, 'Frozen result summary mismatch')
    return dict(audit='passed',planned_attempts=actual['planned_attempts'],contract_passes=actual['contract_passes'],
        returned_tokens=actual['returned_tokens'],semantic_accuracy=None,new_model_calls=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root),ensure_ascii=False))
