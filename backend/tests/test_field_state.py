"""Task/trace invariants and complete production-loop scenarios, no paid model."""
import asyncio
from copy import deepcopy
import json
import os
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient
from research_backend.field_state import FieldLedger, value_for
from research_backend.api import create_app
from research_backend.config import Settings
from research_backend.schemas import Report, RunCreate
from research_backend.runtime import Runtime
from tests.test_research_backend import SECRET, headers, workspace, source


@pytest.fixture(autouse=True)
def record_runtime_outcomes(monkeypatch, request):
    """Expose stored outcomes to the fault-matrix report, including failures."""
    if os.environ.get("AGENT_FAULT_RECORD") != "1":
        return
    record_property = request.getfixturevalue("record_property")
    execute = Runtime.execute

    async def observed(runtime, owner, wid, rid):
        try:
            return await execute(runtime, owner, wid, rid)
        finally:
            run = runtime.store.get(owner, wid, "run", rid)
            state = run.get("field_state", {})
            record_property("runtime_outcome", json.dumps(dict(
                status=run["status"], stop_reason=state.get("stop_reason"),
                present=state.get("present"), unresolved=state.get("unresolved"),
                semantic_success=state.get("semantic_success"), review_status=state.get("review_status"),
                tokens_used=run["tokens_used"], tokens_estimated=run["tokens_estimated"],
                report_preserved=run.get("report") is not None,
                event_types=[e["type"] for e in runtime.store.events(owner, wid, rid)],
            )))
    monkeypatch.setattr(Runtime, "execute", observed)


def read(eid="e1", sid="s1", quote="Training uses dataset X."):
    return dict(id=eid, source_id=sid, source_hash="h1", start_line=1, end_line=1, quote=quote)


def report(sid="s1", eid="e1", **attrs):
    return Report.model_validate(dict(title="Fixture", claims=[], comparison_rows=[dict(
        source_id=sid, method="Method A", attributes=attrs, evidence_ids=[eid])], limitations=["fixture"])).model_dump()


def ledger():
    return FieldLedger([dict(source_id="s1", name="Data", criterion="Name training data"),
        dict(source_id="s1", name="Init", criterion="Name initialization")])


def judgment(case, **extra):
    return dict(field_id=case["field_id"], candidate_hash=case["candidate_hash"], support="supported",
        adequacy="complete", role="aligned", stage="aligned", rationale="Explicit in cited line",
        evidence_spans=[dict(evidence_id=case["evidence"][0]["id"], start_line=1, end_line=1)], **extra)


def test_requirements_cannot_disappear_and_reading_is_not_verification():
    l = ledger()
    with pytest.raises(ValueError, match="不能移除"):
        l.plan(dict(source_ids=["s1"], fields=["Data"]))
    l.read("s1")
    assert all(f["activity"] == "source_read" and f["coverage"] == "missing" for f in l.fields.values())
    assert l.snapshot()["semantic_success"] is None
    assert l.prompt()["fields"][0]["criterion"] == "Name training data"


def test_multiple_abstention_rows_do_not_become_a_present_answer():
    l = ledger()
    draft = report(Data="本次未核实")
    draft["comparison_rows"] += report(Data="未找到")["comparison_rows"]
    l.report(draft, {"e1": read()})
    assert l.fields["s1", "Data"]["coverage"] == "abstained"
    assert l.snapshot()["present"] == 0


def test_oversized_plan_is_rejected_before_mutating_requirements():
    l = FieldLedger()
    l.plan(dict(source_ids=["s1"], fields=["Data"]))
    before = l.snapshot()
    with pytest.raises(ValueError, match="96"):
        l.plan(dict(source_ids=["s1"] + [f"s{i}" for i in range(2, 10)],
            fields=["Data"] + [f"Field{i}" for i in range(11)]))
    assert l.snapshot() == before


@pytest.mark.parametrize("eid,sid", [("unknown", "s1"), ("e1", "s2")])
def test_field_candidate_requires_current_same_paper_evidence(eid, sid):
    with pytest.raises(ValueError, match="本次回读"):
        ledger().record(dict(source_id="s1", name="Data", answer="X", evidence_ids=[eid]), {"e1":read(sid=sid)})


def test_review_is_version_bound_and_never_semantic_success():
    l = ledger(); reads = {"e1":read()}
    l.report(report(Data="X"), reads)
    cases = l.review_cases(reads)
    payload = dict(reviews=[judgment(cases[0])])
    l.apply_reviews(payload, cases, "fixture")
    assert l.snapshot()["reviewed"] == 1 and l.snapshot()["semantic_success"] is None
    reads["e1"]["quote"] = "Training uses dataset Y."
    l.report(report(Data="X"), reads)
    assert l.snapshot()["reviewed"] == 0
    with pytest.raises(ValueError, match="旧答案"):
        l.apply_reviews(payload, cases, "fixture")
    assert l.snapshot()["previous_versions"][-1]["review"]["support"] == "supported"


@pytest.mark.parametrize("fault", ["span", "duplicate", "missing", "foreign", "hash"])
def test_bad_review_batch_has_no_partial_commit(fault):
    l = ledger(); reads = {"e1":read()}
    l.report(report(Data="X", Init="Y"), reads)
    cases = l.review_cases(reads); reviews = [judgment(c) for c in cases]
    if fault == "span": reviews[-1]["evidence_spans"][0]["end_line"] = 99
    if fault == "duplicate": reviews[-1] = reviews[0]
    if fault == "missing": reviews.pop()
    if fault == "foreign": reviews[-1]["field_id"] = "foreign"
    if fault == "hash": reviews[-1]["candidate_hash"] = "stale"
    with pytest.raises(ValueError): l.apply_reviews(dict(reviews=reviews), cases, "fixture")
    assert l.snapshot()["reviewed"] == 0


def test_repair_only_changes_gaps_and_keeps_original_claims_and_evidence():
    l = ledger(); reads = {"e1":read(), "e2":read("e2", quote="Initialized from Y.")}
    base = report(Data="X", Init="本次未核实"); original = deepcopy(base)
    l.report(base, reads); scope = l.request_repair()
    proposed = report(eid="e2", Data="damaged", Init="Y")
    proposed["title"] = "Changed title"
    patched = l.merge_patch(base, proposed, scope)
    assert base == original
    assert patched["title"] == base["title"] and patched["claims"] == base["claims"]
    assert value_for(patched,"s1","Data") == ("X",["e1"])
    assert value_for(patched,"s1","Init") == ("Y",["e2"])
    l.report(patched, reads)
    assert not l.gaps()


def test_final_report_overrides_intermediate_candidates():
    l = ledger(); reads = {"e1":read()}
    l.record(dict(source_id="s1", name="Data", answer="X", evidence_ids=["e1"]), reads)
    l.report(report(Init="Y"), reads)
    assert l.fields["s1","Data"]["coverage"] == "missing"


class LoopModel:
    def __init__(self, *, fill=True, review_error=False, tokens=100):
        self.store = None; self.wid = None; self.sid = None
        self.actions = 0; self.calls = 0; self.finish_count = 0
        self.fill, self.review_error, self.tokens = fill, review_error, tokens

    async def complete(self, messages, tools, max_tokens):
        self.calls += 1
        if tools[0]["function"]["name"] == "review_fields":
            cases = json.loads(messages[-1]["content"])["fields"]
            name, args = "review_fields", dict(reviews=[judgment(c) for c in cases])
            if self.review_error: args["reviews"][0]["candidate_hash"] = "stale"
        else:
            self.actions += 1
            if self.actions == 1:
                name, args = "plan_research", dict(source_ids=[self.sid], fields=["Data", "Init"])
            elif self.actions == 2:
                name, args = "sources_read", dict(source_id=self.sid, start_line=1, line_count=2)
            else:
                self.finish_count += 1
                eid = self.store.list("alice",self.wid,"evidence")[0]["id"]
                name = "finish_report"
                args = report(self.sid,eid,Data="X" if self.finish_count == 1 else "damaged")
                if self.finish_count > 1 and self.fill:
                    args["comparison_rows"][0]["attributes"]["Init"] = "Y"
        return dict(tokens=self.tokens, response_model="fixture", message=dict(role="assistant",tool_calls=[dict(
            id=f"c{self.calls}",type="function",function=dict(name=name,arguments=json.dumps(args)))]))


def run_loop(tmp_path, model, mode="bounded", max_steps=8, requirements=(), cancel_when=None,
             max_tokens=50000, memory_mode="legacy", strategies=None):
    app = create_app(Settings(data_dir=tmp_path,jwt_secret=SECRET),model=model)
    with TestClient(app) as client:
        wid = workspace(client); sid = source(client,wid,text="Training data X.\nInitialized from Y.")
        model.store, model.wid, model.sid = app.state.store, wid, sid
        base=f"/api/v1/workspaces/{wid}"
        session=client.post(base+"/sessions",headers=headers(),json={}).json()["id"]
        response=client.post(base+"/runs",headers=headers(),json=dict(session_id=session,question="Data and Init?",
            **(strategies or {}),
            field_mode=mode,max_tokens=max_tokens,max_steps=max_steps,max_field_repairs=2,memory_mode=memory_mode,
            required_fields=[dict(source_id=sid, **field) for field in requirements]))
        assert response.status_code == 202, response.text
        rid=response.json()["id"]
        for _ in range(200):
            if cancel_when is not None and cancel_when.is_set():
                assert client.post(base+f"/runs/{rid}/cancel",headers=headers()).json()["status"] == "cancelled"
                cancel_when = None
            run=client.get(base+"/runs/"+rid,headers=headers()).json()
            if run["status"] in {"completed","failed","cancelled"}: break
            time.sleep(.01)
        reviews=client.get(base+f"/runs/{rid}/field-reviews",headers=headers()).json()["items"]
        assert client.get(base+f"/runs/{rid}/field-reviews",headers=headers("bob")).status_code==404
        events=client.get(base+f"/runs/{rid}/events",headers=headers()).json()["items"]
        return run,reviews,events


def test_complete_runtime_repairs_gap_preserves_answers_counts_review_and_exposes_state(tmp_path):
    model=LoopModel()
    run,reviews,events=run_loop(tmp_path,model)
    assert run["status"]=="completed",run.get("error")
    assert run["field_state"]["present"]==2
    assert run["field_state"]["reviewed"]==2
    assert run["field_state"]["semantic_success"] is None
    assert value_for(run["report"],model.sid,"Data")[0]=="X"
    assert run["tokens_used"]==model.calls*100 and run["step"]==model.calls
    assert reviews[0]["status"]=="recorded"
    assert len([e for e in events if e["type"]=="field_repair_requested"])==1


@pytest.mark.parametrize("mode,finishes", [("off",1),("observe",1),("bounded",2)])
def test_modes_and_no_progress_terminate_without_fabricated_success(tmp_path,mode,finishes):
    model=LoopModel(fill=False)
    run,reviews,_=run_loop(tmp_path,model,mode)
    assert run["status"]=="completed", run.get("error")
    assert model.finish_count==finishes
    if mode=="off": assert run["field_state"]=={} and not reviews
    else:
        assert run["field_state"]["unresolved"]==1
        assert run["field_state"]["stop_reason"] == ("no_progress" if mode=="bounded" else "unresolved_fields")


def test_bad_semantic_review_keeps_report_and_cost_but_not_verdict(tmp_path):
    model=LoopModel(review_error=True)
    run,reviews,_=run_loop(tmp_path,model)
    assert run["status"]=="completed"
    assert run["field_state"]["review_status"]=="failed" and not run["field_state"]["reviewed"]
    assert run["tokens_used"]==model.calls*100
    assert reviews[0]["assistant"] is not None


def test_last_step_skips_repair_and_review_and_reports_gap(tmp_path):
    model=LoopModel()
    run,reviews,_=run_loop(tmp_path,model,max_steps=3)
    assert run["status"]=="completed",run.get("error")
    assert model.calls==3 and not reviews
    assert run["field_state"]["stop_reason"]=="max_steps"
    assert run["field_state"]["unresolved"]==1


def test_duplicate_requirements_rejected():
    req=dict(source_id="s1",name="Data")
    with pytest.raises(ValueError):
        RunCreate(session_id="s",question="q",field_mode="observe",required_fields=[req,req])


def test_unknown_generation_usage_skips_additional_reviewer(tmp_path):
    model=LoopModel(tokens=None)
    run,reviews,_=run_loop(tmp_path,model,mode="observe")
    assert run["status"]=="completed",run.get("error")
    assert run["tokens_estimated"] and not reviews
    assert run["field_state"]["review_status"]=="skipped_budget_or_no_candidates"


def test_explicit_requirements_cannot_reference_another_workspace(tmp_path):
    model=LoopModel()
    with TestClient(create_app(Settings(data_dir=tmp_path,jwt_secret=SECRET),model=model)) as client:
        wid, other=workspace(client), workspace(client)
        sid=source(client,other)
        base=f"/api/v1/workspaces/{wid}"
        session=client.post(base+"/sessions",headers=headers(),json={}).json()["id"]
        r=client.post(base+"/runs",headers=headers(),json=dict(session_id=session,question="q",field_mode="observe",
            required_fields=[dict(source_id=sid,name="Data")]))
        assert r.status_code==404 and model.calls==0


def test_review_over_budget_fails_run_and_records_returned_usage(tmp_path):
    class ExpensiveReviewer(LoopModel):
        async def complete(self,messages,tools,max_tokens):
            result=await super().complete(messages,tools,max_tokens)
            if tools[0]["function"]["name"]=="review_fields": result["tokens"]=51000
            return result
    run,reviews,_=run_loop(tmp_path,ExpensiveReviewer())
    assert run["status"]=="failed" and run["tokens_used"]>50000
    assert reviews[0]["tokens_used"]==51000 and run["field_state"]["reviewed"]==0


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.TimeoutError], ids=["service_error", "timeout"])
def test_failed_repair_preserves_valid_report_and_explicit_gap(tmp_path, failure):
    class BrokenRepair(LoopModel):
        async def complete(self,messages,tools,max_tokens):
            if self.finish_count:
                raise failure("Provider unavailable during repair")
            return await super().complete(messages,tools,max_tokens)
    model=BrokenRepair()
    run,reviews,events=run_loop(tmp_path,model)
    assert run["status"]=="completed" and run["source_coverage"]["status"]=="partial"
    assert value_for(run["report"],model.sid,"Data")[0]=="X"
    assert run["field_state"]["stop_reason"]=="repair_failed" and run["field_state"]["unresolved"]==1
    assert any(e["type"]=="field_repair_failed" for e in events)


def test_explicit_user_criteria_reach_first_model_call_and_persist(tmp_path):
    class InspectRequirements(LoopModel):
        async def complete(self, messages, tools, max_tokens):
            if self.calls == 0:
                assert "Distinguish training from evaluation data" in messages[0]["content"]
                assert "Name the training initialization" in messages[0]["content"]
            return await super().complete(messages, tools, max_tokens)
    requirements = [dict(name="Data", criterion="Distinguish training from evaluation data"),
                    dict(name="Init", criterion="Name the training initialization")]
    run, _, _ = run_loop(tmp_path, InspectRequirements(), requirements=requirements)
    assert run["status"] == "completed"
    assert run["field_state"]["requirement_origin"] == "request"
    assert [f["criterion"] for f in run["field_state"]["fields"]] == [f["criterion"] for f in requirements]
    assert [f["criterion"] for f in run["required_fields"]] == [f["criterion"] for f in requirements]


def test_cancel_during_repair_never_promotes_checkpoint_to_success(tmp_path):
    waiting = Event()
    class WaitingRepair(LoopModel):
        async def complete(self, messages, tools, max_tokens):
            if self.finish_count:
                waiting.set()
                await asyncio.sleep(30)
            return await super().complete(messages, tools, max_tokens)
    run, reviews, events = run_loop(tmp_path, WaitingRepair(), cancel_when=waiting)
    assert run["status"] == "cancelled" and run["report"] is None and not reviews
    assert run["tokens_used"] == 300
    assert any(e["type"] == "field_repair_requested" for e in events)
    assert any(e["type"] == "run_cancelled" for e in events)
    assert not any(e["type"] == "report_ready" for e in events)


def test_finalization_keeps_validation_feedback_after_context_rebuild(tmp_path):
    class InvalidThenCorrected(LoopModel):
        async def complete(self, messages, tools, max_tokens):
            result = await super().complete(messages, tools, max_tokens)
            if self.finish_count == 1:
                call = result["message"]["tool_calls"][0]["function"]
                draft = json.loads(call["arguments"])
                draft["claims"] = [dict(text="Unsupported fixture claim", evidence_ids=[])]
                call["arguments"] = json.dumps(draft)
            elif self.finish_count == 2:
                assert len(tools) == 1 and tools[0]["function"]["name"] == "finish_report"
                assert "claims.0.evidence_ids" in "\n".join(m["content"] for m in messages)
                assert "Training data X." in "\n".join(m["content"] for m in messages)
            return result
    model = InvalidThenCorrected()
    run, _, events = run_loop(tmp_path, model, mode="off", max_steps=4)
    assert run["status"] == "completed", run.get("error")
    assert model.finish_count == 2 and model.calls == 4
    assert run["tokens_used"] == 400
    assert len([e for e in events if e["type"] == "tool_error"]) == 1
