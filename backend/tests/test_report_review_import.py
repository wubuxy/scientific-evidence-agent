"""Only independent, bound human labels can enter report-review summaries."""
import json
import zipfile

import pytest

from tests.test_architecture_study import ROOT, module


@pytest.fixture
def packets(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    packager=module('package_architecture_review')
    packager.package(ROOT/'docs/results/memory-ablation-dev001-v1',tmp_path/'review')
    files=[]
    for slot in ('annotator-a','annotator-b'):
        with zipfile.ZipFile(tmp_path/'review'/(slot+'.zip')) as archive:
            path=tmp_path/(slot+'.json'); path.write_bytes(archive.read('labels.json')); files.append(path)
    return tmp_path/'review',files,module('score_architecture_review')


def test_blank_packets_stay_missing_and_do_not_create_agreement_or_accuracy(packets):
    directory,files,scorer=packets
    result=scorer.score(directory,*files)
    assert result['planned_attempts']==4 and result['delivered_reports']==3
    assert result['completed_items_by_annotator']==[0,0]
    assert result['missing_items_by_annotator']==[18,18]
    assert result['pooled_agreement']==dict(paired_items=0,agreement=None,cohen_kappa=None)
    assert result['semantic_accuracy'] is None and result['adjudicated_human_score'] is None
    assert len(result['adjudication_queue'])==18


def test_disagreement_goes_to_adjudication_without_invented_consensus(packets):
    directory,files,scorer=packets
    for index,path in enumerate(files):
        labels=scorer.load(path); labels['annotator']=f'reviewer-{index}'
        for report in labels['reports']:
            for item in report['items']:
                item.update(score=index,rationale='Synthetic test labels, not real human scores')
        path.write_text(json.dumps(labels),encoding='utf-8')
    result=scorer.score(directory,*files)
    assert result['completed_items_by_annotator']==[18,18]
    assert result['pooled_agreement']['agreement']==0
    assert result['pooled_agreement']['cohen_kappa']==0
    assert len(result['adjudication_queue'])==18 and result['adjudicated_human_score'] is None


@pytest.mark.parametrize('fault',['boolean','no_reason','foreign_span','duplicate','same_reviewer','mapping'])
def test_review_import_rejects_invalid_or_unbound_labels(packets,fault):
    directory,files,scorer=packets
    for index,path in enumerate(files):
        labels=scorer.load(path); labels['annotator']='same' if fault=='same_reviewer' else f'reviewer-{index}'
        labels['reports'][0]['items'][0].update(score=1,rationale='Synthetic test')
        if index==0:
            item=labels['reports'][0]['items'][0]
            if fault=='boolean': item['score']=True
            elif fault=='no_reason': item['rationale']=' '
            elif fault=='foreign_span': item['evidence_spans']=[dict(evidence_id='foreign',start_line=1,end_line=2)]
            elif fault=='duplicate': labels['reports'].append(labels['reports'][0])
        path.write_text(json.dumps(labels),encoding='utf-8')
    if fault=='mapping':
        path=directory/'coordinator-mapping.json'; path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError): scorer.score(directory,*files)


def test_binary_kappa_known_cases(monkeypatch):
    scorer=module('score_architecture_review')
    assert scorer.agreement([(0,0),(1,1)])['cohen_kappa']==1
    assert scorer.agreement([(0,0),(0,0),(0,1),(1,1)])['cohen_kappa']==.5
    assert scorer.agreement([(1,1),(1,1)])['cohen_kappa'] is None
