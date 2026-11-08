"""Opt-in strategies must preserve provenance and the canonical output contract."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from research_backend.memory import EvidenceMemory
from research_backend.schemas import CompactReport, Report, RunCreate
from research_backend.strategies import lexical_writer_evidence, read_guidance
from research_backend.providers import provider_tools


def test_read_first_preserves_read_when_same_id_returns_as_a_search_lead():
    memory = EvidenceMemory("test", priority="read_first")
    read = dict(id="e1", source_id="s1", start_line=1, end_line=3, quote="full original evidence")
    memory.observe("sources_read", {}, {"data": read})
    memory.observe("sources_search", {"query": "full"}, {"data": {"hits": [dict(read, quote="full")]}})
    for i in range(20):
        memory.observe("sources_search", {}, {"data": {"hits": [dict(read, id=f"lead{i}", quote="lead "*100)]}})
    state = memory.state(char_budget=700)
    assert state["evidence"][0]["id"] == "e1"
    assert state["evidence"][0]["citable"] is True
    assert memory.evidence["e1"]["quote"] == read["quote"]


def test_navigation_merges_overlap_and_suggests_valid_windows_without_forcing_skip():
    reads = {str(i):dict(source_id="s", start_line=a, end_line=b) for i,(a,b) in enumerate([(1,30),(20,40)])}
    leads = [dict(source_id="s", start_line=35, end_line=39),dict(source_id="s", start_line=88, end_line=90)]
    note = read_guidance(reads, leads, [dict(id="s", line_count=90)])
    state = json.loads(note.split("：",1)[1])
    assert state["covered_ranges"] == {"s": [[1,40]]}
    assert state["suggested_reads"][0]["previously_read_lines"] == 0
    assert all(1<=r["line_count"]<=100 and r["start_line"]+r["line_count"]-1<=90 for r in state["suggested_reads"])
    assert state["evidence_sufficiency"] == "not_assessed" and "可以再次回读" in note


def test_writer_selection_is_located_verbatim_and_can_retain_late_conditions():
    lines = [f"Background sentence {i}." for i in range(60)]
    lines[45] = "Evaluation metric F1 is measured under oracle gold evidence conditions."
    evidence = {"e1":dict(id="e1",source_id="s",start_line=100,end_line=159,quote="\n".join(lines))}
    before = deepcopy(evidence)
    selected = lexical_writer_evidence(evidence, "指标与条件", [])
    assert any("oracle gold evidence" in e["quote"] for e in selected.values())
    for excerpt in selected.values():
        assert excerpt["id"] == "e1" and excerpt["source_id"] == "s"
        assert excerpt["quote"] == "\n".join(lines[excerpt["start_line"]-100:excerpt["end_line"]-99])
    assert evidence == before and len(selected) == 2


def test_compact_wire_contract_retains_citation_proposal_and_attribute_validation():
    draft = dict(title="Test",claims=[dict(text="Observation",evidence_ids=["e"])],
        comparison_rows=[dict(source_id="s",method="M",evidence_ids=["e"],attributes={"实验条件":"unknown"})],
        limitations=["Requires review"],proposals=[dict(text="Try a held-out comparison",basis_claim_indices=[0],
            assumptions=["Independent tasks available"],validation_plan="Freeze before running")])
    report = Report.model_validate(CompactReport.model_validate(draft).model_dump())
    assert report.comparison_rows[0].dataset == "本次未核实"
    assert report.proposals[0].status == "proposed_not_executed"
    for fault in ("citation", "proposal", "attribute"):
        wrong = deepcopy(draft)
        if fault=="citation": wrong["comparison_rows"][0]["evidence_ids"]=[]
        elif fault=="proposal": wrong["proposals"][0]["basis_claim_indices"]=[1]
        else: wrong["comparison_rows"][0]["attributes"]={"bad\nname":"x"}
        with pytest.raises(ValidationError): CompactReport.model_validate(wrong)
    def size(model):
        return len(json.dumps(provider_tools([dict(type="function",function=dict(name="finish_report",parameters=model.model_json_schema()))])))
    assert size(CompactReport) < size(Report)


def test_strategy_defaults_are_backward_compatible_and_invalid_modes_rejected():
    request=RunCreate(session_id="s",question="q")
    assert not request.read_guidance and request.memory_priority=="recent"
    assert request.writer_context=="prefix" and request.report_schema=="full"
    with pytest.raises(ValidationError): RunCreate(session_id="s",question="q",memory_priority="read_first")


def test_combined_strategies_complete_real_runtime_and_keep_canonical_report(tmp_path):
    from tests.test_field_state import LoopModel, run_loop
    class Model(LoopModel):
        async def complete(self,messages,tools,max_tokens):
            response=await super().complete(messages,tools,max_tokens)
            if self.finish_count:
                definition=next(t['function'] for t in tools if t['function']['name']=='finish_report')
                row_schema=definition['parameters']['$defs']['CompactComparisonRow']
                assert 'dataset' not in row_schema['properties']
                function=response['message']['tool_calls'][0]['function']
                report=json.loads(function['arguments'])
                for row in report['comparison_rows']:
                    for key in ('dataset','model','metric','result','conditions','comparability'): row.pop(key)
                function['arguments']=json.dumps(report)
            return response
    flags=dict(read_guidance=True,memory_priority='read_first',writer_context='lexical',report_schema='compact')
    run,_,events=run_loop(tmp_path,Model(),memory_mode='evidence',max_steps=3,strategies=flags)
    assert run['status']=='completed',run.get('error')
    assert all(run[key]==value for key,value in flags.items())
    assert run['report']['comparison_rows'][0]['dataset']=='本次未核实'
    assert run['field_state']['semantic_success'] is None
    assert any(e['type']=='budget_decision' and e['data']['phase']=='final' for e in events)
