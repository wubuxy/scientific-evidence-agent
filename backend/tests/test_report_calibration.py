import importlib.util
from pathlib import Path


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
