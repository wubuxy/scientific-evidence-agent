import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


def script(name):
    path=Path(__file__).resolve().parents[2]/"scripts"/(name+".py")
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_report_cohort_excludes_ids_and_duplicate_text_without_leaking_references():
    module=script("prepare_report_calibration")
    candidates=[];documents={}
    for i in range(14):
        sid=f"p{i}"
        documents[sid]=dict(id=sid,title=sid,text_sha256=f"h{i}")
        for j in range(3):
            candidates.append((dict(id=f"{sid}-q{j}",question=f"Which value {j}?",source_ids=[sid]),
                dict(targets=[dict(id="answer",source_id=sid,references=[dict(answer="secret-reference")])])))
    tasks,mapping,eligible=module.build(candidates,documents,{"p0"},{"h1"})
    assert eligible==12 and len(tasks)==8
    assert sorted(len(t["source_ids"]) for t,g in tasks)==[1]*4+[2]*4
    assert sum(len(t["targets"]) for t,g in tasks)==36
    for task,gold in tasks:
        assert not set(task["source_ids"]) & {"p0","p1"}
        assert "secret-reference" not in str(task)
        assert [t["id"] for t in task["targets"]]==[t["id"] for t in gold["targets"]]
        assert all(t["field"] in {"Q1","Q2","Q3"} for t in task["targets"])


def test_natural_annotation_sampling_covers_claims_fields_and_keeps_short_reports_short():
    module=script("select_report_annotations")
    def case(tid,unit):
        return dict(id=tid+unit,task_id=tid,repeat=0,method="report-grep",unit_id=unit)
    cases=[case("t",f"claim-{i}") for i in range(7)]
    cases += [case("t",f"row-0-attribute:Q{i}") for i in range(1,4)]
    cases += [case("short","claim-0")]
    tasks=[dict(id=t,targets=[dict(field=f"Q{i}") for i in range(1,4)]) for t in ("t","short")]
    selected=module.select(dict(cases=cases),tasks)
    assert len(selected["cases"])==7
    main=[c for c in selected["cases"] if c["task_id"]=="t"]
    assert sum(c["unit_id"].startswith("claim-") for c in main)==3
    assert sum("attribute:" in c["unit_id"] for c in main)==3
    ids=[c["id"] for c in selected["cases"]]
    for c in cases:c["candidate_verdict"]="supported" if c["id"] not in ids else "contradicted"
    assert [c["id"] for c in module.select(dict(cases=cases),tasks)["cases"]]==ids


def test_annotation_page_does_not_execute_paper_text_or_disclose_candidate_metadata(tmp_path):
    module=script("package_report_annotations")
    human=dict(cases=[dict(id="one",question="q",claim="</script><img src=x onerror=alert(1)>",
        method="hidden-method",candidate_verdict="hidden-verdict",evidence=[dict(id="e",source_id="p",start_line=2,
            end_line=2,quote="quote",model="hidden-model")])])
    (tmp_path/"human-cases.json").write_text(json.dumps(human))
    for slot in ("annotator-a","annotator-b"):
        (tmp_path/(slot+".json")).write_text(json.dumps(dict(annotation_source="human",annotator=slot,
            cases_sha256="frozen",labels=[dict(id="one",verdict=None,rationale=None,evidence_spans=[])])))
    module.package(tmp_path)
    for slot in ("annotator-a","annotator-b"):
        content=(tmp_path/(slot+".html")).read_text(encoding="utf-8")
        assert "</script><img" not in content and "hidden-method" not in content
        assert "hidden-verdict" not in content and "hidden-model" not in content
        assert "\\u003c/script\\u003e" in content
        with zipfile.ZipFile(tmp_path/(slot+".zip")) as z:
            assert set(z.namelist())=={"start.html","human-cases.json",slot+".json","instructions.md"}
            assert b"hidden-" not in z.read("human-cases.json")
        assert ("annotator-b" if slot=="annotator-a" else "annotator-a") not in content


@pytest.mark.asyncio
async def test_judge_checkpoint_preserves_unknown_usage_and_stops_further_calls(tmp_path):
    module=script("run_report_judges")
    case=lambda i:dict(id=str(i),question="q",claim="alpha",source_ids=["p"],citation_contract_valid=True,
        evidence=[dict(id="e",source_id="p",start_line=1,end_line=1,quote="alpha")])
    class Model:
        calls=0
        async def complete(self,messages,tools,limit):
            self.calls+=1
            return dict(tokens=None,message={})
    model=Model()
    result=await module.evaluate(dict(cases=[case(i) for i in range(3)]),model,tmp_path/"judge",20000,"fixture")
    assert model.calls==1 and result["tokens_used"] is None
    assert len(result["records"])==3 and all(r["status"]=="failed" for r in result["records"])
    assert len(list((tmp_path/"judge").glob("case-*.json")))==3
    assert result["records"][-1]["error"]=="prior_unknown_usage"


@pytest.mark.parametrize("bad", [None,"verdict","span","too_long","extra"])
def test_format_recovery_preserves_full_reason_and_rejects_other_errors(bad):
    module=script("recover_review_format")
    case=dict(evidence=[dict(id="e",source_id="p",start_line=1,end_line=1,quote="alpha")])
    payload=dict(verdict="supported",rationale="Evidence "*100,
                 evidence_spans=[dict(evidence_id="e",start_line=1,end_line=1)])
    if bad=="verdict":payload["verdict"]="probably"
    if bad=="span":payload["evidence_spans"][0]["end_line"]=2
    if bad=="too_long":payload["rationale"]="x"*4001
    if bad=="extra":payload["invented_field"]=True
    if bad:
        with pytest.raises(ValueError):module.recover(payload,case)
    else:
        result=module.recover(payload,case)
        assert result["rationale"]==payload["rationale"]
        assert result["verdict"]==payload["verdict"]
        assert result["evidence_spans"][0]["quote"]=="alpha"
