"""Architecture comparison must use the frozen condition and retain honest metrics."""
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('mode',['legacy','evidence'])
def test_frozen_memory_mode_reaches_real_run_request_without_model_calls(tmp_path,monkeypatch,mode):
    from research_backend.config import Settings
    from research_backend.runtime import Runtime
    runner = module('run_research_case')
    directory = tmp_path/'case'
    runner.prepare(directory,mode)
    monkeypatch.setattr(Settings,'from_env',lambda:Settings(llm_api_key='offline-test',
        llm_model='qwen3.5-27b',llm_extra_body={'enable_thinking':False}))

    async def capture(runtime,owner,wid,rid):
        run = runtime.store.get(owner,wid,'run',rid)
        assert run['memory_mode']==mode and run['retrieval_strategy']=='grep'
        assert run['allow_network'] is False and len(run['required_fields'])==8
        runtime.store.update(owner,wid,'run',rid,status='failed',error='Offline harness; no model invocation')

    monkeypatch.setattr(Runtime,'execute',capture)
    assert asyncio.run(runner.run_case(directory,tmp_path/'.env'))==1
    summary=json.loads((directory/'run/summary.json').read_text(encoding='utf-8'))
    assert summary['memory_mode']==mode and summary['model_calls']==0


def test_memory_preparation_changes_only_the_condition(tmp_path):
    runner=module('run_research_case')
    runner.prepare(tmp_path/'a','legacy'); runner.prepare(tmp_path/'b','evidence')
    a,b=[json.loads((tmp_path/n/'manifest.json').read_text(encoding='utf-8')) for n in ('a','b')]
    assert a.pop('memory_mode')=='legacy' and b.pop('memory_mode')=='evidence'
    assert a==b


@pytest.mark.parametrize('fault',['usage','human_score','missing','quote'])
def test_architecture_audit_rejects_corrupt_result(tmp_path,fault):
    auditor=module('audit_architecture_study')
    directory=tmp_path/'study'
    shutil.copytree(ROOT/'docs/results/grounding-gate-dev001-v1',directory)
    assert auditor.audit(directory)['contract_passes']==2
    if fault=='missing':
        (directory/'repeat-02/reads.json').unlink()
    elif fault=='quote':
        # Alter both exports: source-text verification must still reject the quote.
        for name in ('reads.json','tool-calls.json'):
            path=directory/'repeat-01'/name; value=auditor.load(path)
            read=value[0] if name=='reads.json' else next(c['data'] for c in value if c['name']=='sources_read')
            read['quote']='Fabricated source content'
            path.write_text(json.dumps(value),encoding='utf-8')
    else:
        path=directory/'summary.json'; value=auditor.load(path)
        value['known_tokens' if fault=='usage' else 'human_rubric_score']=1
        path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises((ValueError,FileNotFoundError)):
        auditor.audit(directory)


def test_report_packets_keep_conditions_private_and_human_labels_blank(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    packager=module('package_architecture_review')
    packager.package(ROOT/'docs/results/memory-ablation-dev001-v1',tmp_path/'review')
    manifest=json.loads((tmp_path/'review/packet-manifest.json').read_text(encoding='utf-8'))
    assert manifest['planned_attempts']==4 and manifest['delivered_reports']==3
    assert manifest['completed_human_labels']==0 and manifest['human_rubric_score'] is None
    for slot in ('annotator-a','annotator-b'):
        with zipfile.ZipFile(tmp_path/'review'/(slot+'.zip')) as packet:
            assert 'coordinator-mapping.json' not in packet.namelist()
            assert not any('trace' in n or 'summary' in n for n in packet.namelist())
            labels=json.loads(packet.read('labels.json'))
            assert labels['slot']==slot and labels['annotator'] is None
            assert len(labels['reports'])==3
            assert all(len(r['items'])==6 and all(i['score'] is None and i['rationale'] is None
                and not i['evidence_spans'] for i in r['items']) for r in labels['reports'])
            assert 'memory_mode' not in packet.read('reports.md').decode()
