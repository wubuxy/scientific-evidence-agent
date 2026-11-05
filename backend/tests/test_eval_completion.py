"""Evaluation boundaries: real Runtime, paired interventions and honest calibration."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from research_backend.benchmarks import claim_labels, reports, repair_arms, trials
from research_backend.benchmarks.measurement import MeasuredModel
from research_backend.benchmarks.prepare import freeze
from research_backend.config import Settings


def completion(name, arguments, tokens=300):
    return dict(tokens=tokens, prompt_tokens=200, completion_tokens=100,
        message=dict(role="assistant", tool_calls=[dict(id="call", type="function",
            function=dict(name=name, arguments=json.dumps(arguments)))]))


@pytest.fixture
def report_study(tmp_path):
    document = dict(id="p1",title="Fixture paper",text="alpha\n"+"\n".join(["filler"]*43)+"\nfinal value beta",
                    paragraphs=[dict(id="para",start_line=45,end_line=45)])
    task = dict(id="t1",question="Which final value?",source_ids=["p1"],
                targets=[dict(id="answer",source_id="p1",field="value")])
    gold = dict(targets=[dict(id="answer",source_id="p1",references=[dict(answer="beta",unanswerable=False,evidence_paragraph_ids=["para"])])])
    directory = tmp_path/"dataset"
    freeze(directory,"synthetic-fixture-not-real-accuracy","dev",[(task,gold)],{"p1":document},1,1,{}, {})
    return directory/"inputs.json",directory/"gold.json",task,{"p1":document}


class ReportModel:
    async def complete(self,messages,tools,max_tokens):
        observations = [json.loads(m["content"]) for m in messages if m["role"]=="tool"]
        if not observations:
            sources = json.loads(messages[1]["content"].split("当前资料清单（元数据，不是证据）：",1)[1])["sources"]
            return completion("plan_research",dict(source_ids=[s["id"] for s in sources],fields=["value"]))
        reads = [o["data"] for o in observations if o.get("data",{}).get("quote")]
        if not reads:
            return completion("sources_read",dict(source_id=observations[-1]["data"]["source_ids"][0],start_line=45,line_count=1))
        evidence = reads[-1]
        return completion("finish_report",dict(title="Fixture report",claims=[dict(text="The final value is beta",evidence_ids=[evidence["id"]])],
            comparison_rows=[dict(source_id=evidence["source_id"],method="fixture",attributes={"value":"beta"},evidence_ids=[evidence["id"]])],
            limitations=["Synthetic fixture, not a scientific finding"]))


@pytest.mark.asyncio
async def test_report_evaluation_executes_production_runtime_and_exports_canonical_reads(report_study,tmp_path):
    _,_,task,documents = report_study
    directory = tmp_path/"report"
    directory.mkdir()
    result = await reports.run_report(task,documents,Settings(),"report-grep",directory,lambda texts:[],20000,6,30,ReportModel())
    assert result["status"]=="completed" and result["runtime"]=="research_backend.runtime.Runtime"
    assert result["tokens_used"]==900 and result["model_calls"]==3
    assert result["report"]["comparison_rows"][0]["source_id"]=="p1"
    checked = reports.inspect_prediction(result,task,documents,20000)
    assert checked["contract_pass"] and checked["field_presence_rate"]==1
    assert checked["semantic_success"] is None
    trace = trials.load(directory/"trace.json")
    assert any(e["type"]=="report_ready" for e in trace["events"])


@pytest.mark.asyncio
async def test_report_failure_usage_and_forged_citations_never_become_success(report_study,tmp_path):
    _,_,task,documents = report_study
    class BrokenModel:
        async def complete(self,*args):
            raise RuntimeError("offline provider failure")
    directory = tmp_path/"failed"
    directory.mkdir()
    result = await reports.run_report(task,documents,Settings(),"report-grep",directory,lambda texts:[],20000,6,30,BrokenModel())
    assert result["status"]=="failed" and result["tokens_used"] is None
    assert result["known_tokens_used"]==0 and result["model_calls"]==1
    fake = dict(status="completed",tokens_used=1,reads=[dict(id="e",source_id="p1",start_line=1,end_line=1,quote="invented")],
        report=dict(title="bad",claims=[dict(text="alpha",evidence_ids=["e"])],limitations=["fixture"]))
    result = reports.inspect_prediction(fake,task,documents,20000)
    assert not result["contract_pass"] and "invalid_or_unread_citation" in result["errors"]


@pytest.mark.asyncio
async def test_report_plans_score_missing_attempts_and_joint_reviews(report_study,tmp_path):
    inputs,gold,task,documents = report_study
    plan = trials.make_plan(inputs,["t1"],["report-grep"],repeats=2,model="fake")
    root = tmp_path/"study"
    trials.write_new(root/"plan.json",plan)
    trials.write_new(root/"study.json",dict(plan_sha256=trials.digest(root/"plan.json")))
    trial = root/"trial-000"
    protocol = {k:plan[k] for k in ("dataset","split","inputs_sha256","model","extra_body","max_tokens","max_steps","methods","task_ids","code_sha256")}
    trials.write_new(trial/"protocol.json",protocol)
    directory = trial/"report-grep/t1"
    directory.mkdir(parents=True)
    result = await reports.run_report(task,documents,Settings(),"report-grep",directory,lambda texts:[],20000,6,30,ReportModel())
    (trial/"predictions.jsonl").write_text(json.dumps(result),encoding="utf-8")
    summary = trials.analyze_trials(inputs,gold,root)["summaries"]["report-grep"]
    assert summary["planned_attempts"]==2 and summary["completed"]==1
    assert summary["field_presence_rate"]==.5 and summary["mean_tokens"] is None
    assert summary["contract_reliability"]["2"]==dict(pass_at_k=1,pass_all_k=0)
    bundle = reports.export_cases(inputs,root)
    assert len(bundle["cases"])==3  # claim, method cell, value cell
    assert all(c["evidence"][0]["quote"]=="final value beta" for c in bundle["cases"])
    assert summary["semantic_success_rate"] is None


@pytest.mark.requires_rg
@pytest.mark.asyncio
async def test_five_paired_arms_are_blind_and_include_each_policy_cost(report_study,tmp_path):
    _,_,task,documents = report_study
    base = dict(task_id="t1",method="grep",status="completed",tokens_used=1000,known_tokens_used=1000,model_calls=2,
        reads=[dict(id="old",source_id="p1",start_line=1,end_line=1,quote="alpha")],
        answers=[dict(target_id="answer",answer="alpha",unanswerable=False,evidence_ids=["old"])])
    seen = []
    class Model:
        async def complete(self,messages,tools,max_tokens):
            name = tools[0]["function"]["name"]
            payload = json.loads(messages[-1]["content"])
            seen.append((name,payload))
            if name=="review_answer":
                return completion(name,dict(support="insufficient",adequacy="partial",role="aligned",stage="aligned",
                    gap="missing beta",grep_query="final value",anchor_evidence_id="old"))
            if name=="propose_search":
                assert "review" not in payload
                return completion(name,dict(grep_query="final value"))
            evidence = payload["evidence"][-1]
            answer = "beta" if "final value beta" in evidence["quote"] else "alpha"
            return completion(name,dict(answers=[dict(target_id="answer",answer=answer,unanswerable=False,evidence_ids=[evidence["id"]])]))
    methods = ["repair-baseline","repair-rewrite","repair-reread","repair-keyword","repair-search"]
    results = await repair_arms.run_branches(task,documents,Settings(),base,methods,tmp_path,20000,Model())
    keyed = {r["method"]:r for r in results}
    assert [r["tokens_used"] for r in results]==[1000,1600,1600,1300,1600]
    assert keyed["repair-rewrite"]["answers"][0]["answer"]=="alpha"
    assert all(keyed[m]["answers"][0]["answer"]=="beta" for m in methods[2:])
    assert sum(name=="review_answer" for name,_ in seen)==1
    assert all("review" not in payload for name,payload in seen[-3:])
    assert all(r["paired_prefix"] and r["elapsed_seconds"] is None for r in results)
    physical = trials.load(tmp_path/"_shared/t1/repair_usage.json")
    assert physical["known_measured_tokens"]==2800
    assert sum(r["tokens_used"] for r in results)==7100


@pytest.mark.asyncio
async def test_unknown_intervention_usage_stops_that_branch_and_preserves_prefix_cost(report_study,tmp_path):
    _,_,task,documents = report_study
    base = dict(task_id="t1",status="completed",tokens_used=1000,model_calls=1,answers=[],reads=[])
    class Model:
        async def complete(self,*args):
            raise RuntimeError("offline failure")
    result = (await repair_arms.run_branches(task,documents,Settings(),base,["repair-rewrite"],tmp_path,20000,Model()))[0]
    assert result["tokens_used"] is None and result["known_tokens_used"]==1000
    assert result["intervention_calls"]==1 and result["answers"]==[]


def test_report_plan_rejects_mixed_metrics_and_runtime_step_drift(report_study):
    inputs,_,_,_ = report_study
    with pytest.raises(ValueError,match="separate plans"):
        trials.make_plan(inputs,["t1"],["grep","report-grep"])
    with pytest.raises(ValueError,match="30 steps"):
        trials.make_plan(inputs,["t1"],["report-grep"],max_steps=31)
    plan = trials.make_plan(inputs,["t1"],["report-grep"])
    plan["scope"]="short_answer_agent_full_trajectory_repeats_not_workbench_report_quality"
    with pytest.raises(ValueError,match="derived plan"):
        trials.validate_plan(plan,inputs)


def calibration_files(tmp_path):
    cases = [dict(id=str(i),question="fixture",claim="alpha",source_ids=["p1","p2"],citation_contract_valid=True,
        evidence=[dict(id="e1",source_id="p1",start_line=1,end_line=1,quote="alpha"),
                  dict(id="e2",source_id="p2",start_line=2,end_line=2,quote="beta")]) for i in range(3)]
    path = tmp_path/"cases.json"
    trials.write_new(path,dict(format=1,cases=cases))
    bundle = trials.load(path)
    rows = []
    for case,verdict in zip(cases,claim_labels.VERDICTS):
        review = claim_labels.resolve_review(dict(verdict=verdict,rationale="fixture only",
            evidence_spans=[dict(evidence_id="e1",start_line=1,end_line=1)]),case)
        rows.append(dict(id=case["id"],status="completed",review=review))
    review_path = tmp_path/"reviews.json"
    trials.write_new(review_path,dict(cases_fingerprint=claim_labels.fingerprint(bundle),records=rows))
    paths = []
    for who in ("a","b","adjudication"):
        p = tmp_path/(who+".json")
        trials.write_new(p,dict(annotation_source="synthetic_fixture",annotator=who,cases_sha256=trials.digest(path),
            labels=[dict(id=case["id"],verdict=verdict if who!="adjudication" else None) for case,verdict in zip(cases,claim_labels.VERDICTS)]))
        paths.append(p)
    return path,review_path,*paths


def test_calibration_is_explicit_and_missing_judgements_stay_in_denominator(tmp_path):
    files = calibration_files(tmp_path)
    result = claim_labels.calibrate(*files)
    assert result["cohen_kappa"]==1 and result["macro_f1"]==1
    assert result["annotation_source"]=="synthetic_fixture"
    reviews = trials.load(files[1])
    reviews["records"].pop()
    files[1].write_text(json.dumps(reviews),encoding="utf-8")
    result = claim_labels.calibrate(*files)
    assert result["accuracy_all_cases"]==pytest.approx(2/3) and result["judged_coverage"]==pytest.approx(2/3)
    assert result["macro_f1"]==pytest.approx(2/3)


def test_blank_labels_disagreements_and_forged_review_spans_are_rejected(tmp_path):
    files = calibration_files(tmp_path)
    annotations = trials.load(files[2])
    annotations["labels"][0]["verdict"]=None
    files[2].write_text(json.dumps(annotations),encoding="utf-8")
    with pytest.raises(ValueError,match="Incomplete"):
        claim_labels.calibrate(*files)
    annotations["labels"][0]["verdict"]="insufficient"
    files[2].write_text(json.dumps(annotations),encoding="utf-8")
    with pytest.raises(ValueError,match="adjudication"):
        claim_labels.calibrate(*files)
    adjudication = trials.load(files[4])
    adjudication["labels"][0]["verdict"]="insufficient"
    files[4].write_text(json.dumps(adjudication),encoding="utf-8")
    result = claim_labels.calibrate(*files)
    assert result["unsupported_false_supported_rate"]==pytest.approx(1/3)
    reviews = trials.load(files[1])
    reviews["records"][0]["review"]["evidence_spans"][0]["quote"]="invented"
    files[1].write_text(json.dumps(reviews),encoding="utf-8")
    with pytest.raises(ValueError,match="canonical evidence"):
        claim_labels.calibrate(*files)


@pytest.mark.asyncio
async def test_joint_source_judge_sees_all_evidence_without_labels_and_enforces_budget(tmp_path):
    files = calibration_files(tmp_path)
    bundle = trials.load(files[0])
    for case in bundle["cases"]:
        case["human_label"]="SHOULD_NOT_BE_SENT"
    class Judge:
        calls = 0
        async def complete(self,messages,tools,max_tokens):
            self.calls+=1
            assert "SHOULD_NOT_BE_SENT" not in json.dumps(messages)
            assert len(json.loads(messages[-1]["content"])["evidence"])==2
            return completion("review_claim",dict(verdict="insufficient",rationale="joint fixture",evidence_spans=[]),tokens=19000)
    model = Judge()
    result = await claim_labels.judge_bundle(bundle,model,20000)
    assert model.calls==1 and result["tokens_used"]==19000
    assert [r["status"] for r in result["records"]]==["completed","failed","failed"]
    assert len(result["records"])==3


@pytest.mark.asyncio
async def test_measured_boundary_retains_unknown_cost_on_cancellation(report_study,tmp_path):
    class Model:
        async def complete(self,*args):
            raise asyncio.CancelledError()
    model = MeasuredModel(Model(),20000,initial=300)
    with pytest.raises(asyncio.CancelledError):
        await model.complete([],[],1000)
    assert model.tokens is None and model.known_tokens==300 and len(model.calls)==1
    from research_backend.benchmarks.run import run_case
    _,_,task,documents = report_study
    class TimedModel:
        calls = 0
        entered = None
        async def complete(self,messages,tools,max_tokens):
            self.calls+=1
            if self.calls==1:
                sid = json.loads(messages[1]["content"])["sources"][0]["id"]
                return completion("sources_read",dict(source_id=sid,start_line=1,line_count=1))
            self.entered.set()
            await asyncio.sleep(10)
    directory = tmp_path/"cancelled"
    directory.mkdir()
    timed_model = TimedModel()
    timed_model.entered = asyncio.Event()
    operation = asyncio.create_task(run_case(task,documents,Settings(),"grep",directory,lambda texts:[],20000,6,timed_model))
    await asyncio.wait_for(timed_model.entered.wait(),timeout=5)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(operation,timeout=.01)
    saved = trials.load(directory/"prediction.json")
    assert saved["tokens_used"] is None and saved["known_tokens_used"]==300
    assert saved["status"]=="failed" and saved["model_calls"]==2


@pytest.mark.requires_rg
def test_all_evaluation_clis_through_loopback_model(tmp_path):
    script = Path(__file__).resolve().parents[2]/"scripts/smoke_agent_evals.py"
    result = subprocess.run([sys.executable,str(script),"--out",str(tmp_path/"smoke")],
        capture_output=True,text=True,encoding="utf-8",timeout=120)
    assert result.returncode==0, result.stdout+result.stderr
    summary = json.loads(result.stdout.splitlines()[-1])
    assert summary["production_report_attempts"]==2 and summary["paired_repair_attempts"]==10
    assert summary["reviewed_units"]==6 and summary["real_cloud_calls"]==0
