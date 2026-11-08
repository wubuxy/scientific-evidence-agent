"""Regression gates for the observed budget/contract failures; no model API."""
from copy import deepcopy
import json
import math

import pytest
from pydantic import ValidationError

from research_backend.memory import EvidenceMemory
from research_backend.field_state import FieldLedger
from research_backend.providers import provider_tools
from research_backend.runtime import (FINAL_EVIDENCE_MIN, FINAL_OUTPUT_RESERVE,
    estimate_prompt_tokens, final_context, fit_final_context, render_report, validation_feedback)
from research_backend.schemas import Report
from research_backend.benchmarks.reports import inspect_prediction
from tests.test_field_state import LoopModel, run_loop


def definition():
    return [dict(type="function", function=dict(name="finish_report", parameters=Report.model_json_schema()))]


def draft():
    return dict(title="Fixture", claims=[dict(text="Training uses X.", evidence_ids=["e1"])],
        comparison_rows=[], limitations=["Fixture only"], proposals=[dict(
            text="Evaluate generalization on a separate paper set.", basis_claim_indices=[0],
            assumptions=["Separate papers can be independently annotated."], validation_plan="Freeze tasks; review errors.")])


def test_schema_projection_preserves_named_title_and_citation_constraints():
    original = definition(); before = deepcopy(original)
    schema = provider_tools(original)[0]["function"]["parameters"]
    assert original == before
    assert "title" in schema["properties"] and "title" in schema["required"]
    assert "title" not in schema and "$defs" not in schema
    claims = schema["properties"]["claims"]["items"]
    assert claims["properties"]["evidence_ids"]["minItems"] == 1
    assert claims["additionalProperties"] is False
    assert schema["properties"]["proposals"]["items"]["properties"]["status"]["const"] == "proposed_not_executed"
    literal = {"title":"data", "examples":["data"]}
    original[0]["function"]["parameters"]["properties"]["fixture"] = dict(type="object",default=literal,enum=[literal])
    projected = provider_tools(original)[0]["function"]["parameters"]["properties"]["fixture"]
    assert projected["default"] == literal and projected["enum"] == [literal]


def test_final_context_uses_reserved_minimum_when_6000_chars_would_not_fit():
    reads = {f"e{i}":dict(id=f"e{i}",source_id=f"s{i}",start_line=1,end_line=100,
                         quote=("verbatim original line 中文\n" * 400)) for i in range(2)}
    original = deepcopy(reads)
    system, question, feedback = "criterion: keep all experimental conditions", "Compare two papers", "claims.0.evidence_ids"
    minimum = final_context(system, question, reads, FINAL_EVIDENCE_MIN, feedback)
    ceiling = math.ceil(estimate_prompt_tokens(minimum, definition()) * 1.1)
    assert math.ceil(estimate_prompt_tokens(final_context(system,question,reads,6000,feedback), definition())*1.1) > ceiling
    messages, _, reserve, chars = fit_final_context(system,question,reads,definition(),ceiling,report_feedback=feedback)
    assert reserve <= ceiling and chars < 6000
    assert messages[0]["content"] == system and messages[1]["content"] == question
    assert feedback in messages[-1]["content"]
    packed = json.loads(messages[2]["content"].split("\n",1)[1])
    assert {e["id"] for e in packed["evidence"]} == set(reads)
    for evidence in packed["evidence"]:
        assert evidence["quote"] and reads[evidence["id"]]["quote"].startswith(evidence["quote"])
        assert evidence["quote_truncated"] is True
    assert reads == original


@pytest.mark.parametrize("memory_mode", ["legacy", "evidence"])
def test_runtime_reserves_actual_request_output_and_delivers_typed_proposal(tmp_path, memory_mode):
    class Model(LoopModel):
        async def complete(self, messages, tools, max_tokens):
            result = await super().complete(messages,tools,max_tokens)
            if self.finish_count:
                function = result["message"]["tool_calls"][0]["function"]
                report = json.loads(function["arguments"])
                report["claims"] = [dict(text="Training uses X.", evidence_ids=report["comparison_rows"][0]["evidence_ids"],
                    kind="inference", qualification="Only if the task uses the same training setup.")]
                report["proposals"] = draft()["proposals"]
                function["arguments"] = json.dumps(report)
            prompt = int(estimate_prompt_tokens(messages,tools)*.8)
            result.update(tokens=prompt+min(200,max_tokens), prompt_tokens=prompt, completion_tokens=min(200,max_tokens))
            return result
    run,_,events = run_loop(tmp_path,Model(),mode="off",max_steps=3,max_tokens=20000,memory_mode=memory_mode)
    assert run["status"] == "completed", run.get("error")
    assert run["report"]["proposals"][0]["status"] == "proposed_not_executed"
    assert "方案建议（尚未执行" in run["markdown"] and "[推断]" in run["markdown"]
    decisions = [e["data"] for e in events if e["type"] == "budget_decision"]
    assert len(decisions) == 3 and decisions[-1]["finishing"]
    for decision in decisions:
        allocated = decision["tokens_before_call"] + decision["prompt_estimate"] + decision["output_allowance"]
        if not decision["finishing"]:
            allocated += decision["final_prompt_reserve"] + FINAL_OUTPUT_RESERVE
        assert allocated <= run["max_tokens"]
        assert decision["evidence_sufficiency"] == "not_assessed"


@pytest.mark.parametrize("fault", ["uncited", "bad_basis", "negative_basis", "boolean_basis", "no_assumptions", "no_validation", "unqualified", "executed"])
def test_proposals_do_not_relax_fact_citations_or_claim_validation(fault):
    report = draft()
    if fault == "uncited": report["claims"][0]["evidence_ids"] = []
    if fault == "bad_basis": report["proposals"][0]["basis_claim_indices"] = [1]
    if fault == "negative_basis": report["proposals"][0]["basis_claim_indices"] = [-1]
    if fault == "boolean_basis": report["proposals"][0]["basis_claim_indices"] = [False]
    if fault == "no_assumptions": report["proposals"][0]["assumptions"] = [" "]
    if fault == "no_validation": report["proposals"][0]["validation_plan"] = ""
    if fault == "unqualified": report["claims"][0]["kind"] = "inference"
    if fault == "executed": report["proposals"][0]["status"] = "verified"
    with pytest.raises(ValidationError): Report.model_validate(report)


def test_feedback_is_bounded_parseable_and_does_not_repeat_rejected_text():
    report = draft(); report["claims"] = [dict(text="x"*3900,evidence_ids=[]) for _ in range(20)]
    with pytest.raises(ValidationError) as error: Report.model_validate(report)
    feedback = validation_feedback(error.value)
    parsed = json.loads(feedback)
    assert len(feedback) <= 1600 and "x"*100 not in feedback
    assert parsed["errors"][0]["path"] == "claims.0.evidence_ids"
    assert len(parsed["errors"]) + parsed["omitted_errors"] == 20


def test_grouped_requirements_keep_distinct_criteria_and_all_source_states():
    ledger = FieldLedger([dict(source_id=f"s{i}",name="Data",criterion="Training, not evaluation data" if i<2 else "Evaluation data")
                          for i in range(3)])
    ledger.read("s0")
    grouped = ledger.compact_prompt()
    expanded = [dict(name=g["name"],criterion=g["criterion"],**state)
                for g in grouped["requirements"] for state in g["states"]]
    assert expanded == ledger.prompt()["fields"]
    assert len(grouped["requirements"]) == 2
    assert ledger.snapshot()["present"] == 0 and ledger.snapshot()["semantic_success"] is None


def test_memory_distinguishes_search_leads_and_does_not_repeat_full_drafts():
    memory = EvidenceMemory("training")
    item = dict(id="lead",source_id="s1",start_line=1,end_line=1,quote="Training uses X.")
    memory.observe("sources_search",dict(query="training"),dict(data={"hits":[item]}))
    memory.observe("sources_read",dict(source_id="s1"),dict(data=dict(item,id="read")))
    memory.observe("finish_report",dict(text="do-not-repeat"*10000),dict(error="invalid"))
    state = memory.state()
    cards = {item["id"]:item for item in state["evidence"]}
    assert cards["lead"]["citable"] is False and cards["read"]["citable"] is True
    assert "do-not-repeat" not in json.dumps(state)
    assert state["recent_actions"][-1]["error"] == "invalid"


def test_existing_unit_scorer_does_not_claim_proposal_quality():
    report = Report.model_validate(draft()).model_dump()
    result = inspect_prediction(dict(status="completed",report=report,tokens_used=100,reads=[dict(
        id="e1",source_id="s1",start_line=1,end_line=1,quote="Training uses X.")]),
        dict(source_ids=["s1"],targets=[dict(source_id="s1",field="Data")]),
        {"s1":dict(text="Training uses X.",title="Paper")},1000)
    assert result["contract_pass"] and len(result["units"]) == 1
    assert result["proposal_count"] == 1 and result["proposal_quality"] is None
    assert result["semantic_success"] is None
    assert "尚未执行" in render_report(report)
