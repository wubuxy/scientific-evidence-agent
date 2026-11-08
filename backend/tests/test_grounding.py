"""Search must yield to a real read; no LLM or semantic success labels."""
import json

import pytest

from research_backend.grounding import read_context, unread_leads
from tests.test_field_state import run_loop, report


def hit(source="s1",start=1,end=10,query="data"):
    return dict(source_id=source,start_line=start,end_line=end,query=query,quote="Original text. "*100)


def test_read_coverage_uses_interval_union_and_does_not_hide_gaps():
    reads={"a":hit(end=5),"b":hit(start=6),"other":hit(source="s2")}
    assert not unread_leads([hit()],reads,"data")
    reads["b"]["start_line"]=7
    assert len(unread_leads([hit()],reads,"data"))==1
    assert len(unread_leads([hit(source="s3")],reads,"data"))==1


def test_read_context_balances_queries_preserves_task_and_marks_previews():
    leads=[hit(query="data",start=i,end=i+3) for i in range(10)] + [hit(source="s2",query="metrics")]
    context=read_context("Original task","Full criterion retained",leads,char_budget=1600)
    data=json.loads(context[-1]["content"].split("\n",1)[1])
    assert context[1]["content"] == "Original task"
    assert "Full criterion retained" in context[0]["content"]
    assert {x["source_id"] for x in data["search_leads"]} == {"s1","s2"}
    assert data["omitted_leads"] > 0 and data["evidence_sufficiency"] == "not_assessed"
    assert all(x["quote_truncated"] and not x["citable"] for x in data["search_leads"])
    assert all(x["quote"] in leads[0]["quote"] for x in data["search_leads"])


class SearchModel:
    def __init__(self,violate=False,miss=False,bad_read=False):
        self.calls=0; self.violate=violate; self.miss=miss; self.read=False; self.bad_read=bad_read

    async def complete(self,messages,tools,max_tokens):
        self.calls+=1
        names=[t["function"]["name"] for t in tools]
        if self.calls==1:
            name,args="plan_research",dict(source_ids=[self.sid],fields=["Data","Init"])
        elif self.calls==2:
            name,args="sources_search",dict(query="Training data",grep_query="UNFINDABLE" if self.miss else "Training",source_ids=[self.sid])
        elif not self.read:
            if self.miss:
                assert "sources_search" in names
            else:
                assert names==["sources_read"]
                preview=json.loads(next(m["content"] for m in messages if m["content"].startswith("待回读的搜索定位" )).split("\n",1)[1])
                assert preview["search_leads"][0]["source_id"]==self.sid
                assert not preview["search_leads"][0]["citable"]
            if self.violate and self.calls==3:
                name,args="sources_search",dict(query="again",grep_query="Training",source_ids=[self.sid])
            elif self.bad_read and self.calls==3:
                name,args="sources_read",dict(source_id=self.sid,start_line=1,line_count=0)
            else:
                if self.bad_read:
                    assert "line_count" in "\n".join(m["content"] for m in messages)
                name,args="sources_read",dict(source_id=self.sid,start_line=1,line_count=2)
                self.read=True
        else:
            eid=next(c["data"]["id"] for c in self.store.list("alice",self.wid,"tool_call")
                     if c["name"]=="sources_read" and c["status"]=="succeeded")
            name,args="finish_report",report(self.sid,eid,Data="X",Init="Y")
        return dict(tokens=100,message=dict(role="assistant",tool_calls=[dict(id=f"c{self.calls}",type="function",
            function=dict(name=name,arguments=json.dumps(args)))]))


@pytest.mark.parametrize("memory_mode",["legacy","evidence"])
@pytest.mark.requires_rg
def test_real_search_transitions_to_read_before_another_search_or_report(tmp_path,memory_mode):
    model=SearchModel()
    run,_,events=run_loop(tmp_path,model,mode="off",memory_mode=memory_mode)
    assert run["status"]=="completed",run.get("error")
    decisions=[e["data"] for e in events if e["type"]=="budget_decision"]
    assert decisions[2]["phase"]=="read" and decisions[2]["read_windows"]==0
    assert decisions[3]["read_windows"]==1
    assert any(e["type"]=="read_checkpoint_requested" for e in events)
    assert run["tokens_used"]==400


@pytest.mark.requires_rg
def test_cannot_bypass_read_stage_with_another_search(tmp_path):
    model=SearchModel(violate=True)
    run,_,events=run_loop(tmp_path,model,mode="off")
    assert run["status"]=="completed",run.get("error")
    assert any(e["type"]=="tool_error" and "只允许 sources_read" in e["data"]["error"] for e in events)
    executed=[e["data"]["name"] for e in events if e["type"]=="tool_completed"]
    assert executed.count("sources_search")==1 and executed.count("sources_read")==1


@pytest.mark.parametrize("constraint",["steps","tokens"])
@pytest.mark.requires_rg
def test_no_read_evidence_stops_without_paying_for_impossible_report(tmp_path,constraint):
    class CostModel(SearchModel):
        async def complete(self,*args):
            response=await super().complete(*args)
            if self.calls==2 and constraint=="tokens": response["tokens"]=12000
            return response
    model=CostModel()
    run,_,events=run_loop(tmp_path,model,mode="bounded",max_steps=3 if constraint=="steps" else 8,
                         max_tokens=50000 if constraint=="steps" else 14000)
    assert run["status"]=="failed" and run.get("report") is None
    assert model.calls==2 and run["tokens_used"]==(200 if constraint=="steps" else 12100)
    assert run["field_state"]["stop_reason"]=="no_read_evidence"
    assert run["field_state"]["semantic_success"] is None
    assert "尚未回读原文" in run["error"]
    assert any(e["type"]=="read_checkpoint_blocked" for e in events)
    assert not any(e["type"]=="report_ready" for e in events)


@pytest.mark.requires_rg
def test_empty_search_does_not_force_reading_a_nonexistent_hit(tmp_path):
    run,_,events=run_loop(tmp_path,SearchModel(miss=True),mode="off")
    assert run["status"]=="completed",run.get("error")
    assert not any(e["type"]=="read_checkpoint_requested" for e in events)


@pytest.mark.requires_rg
def test_invalid_read_keeps_error_feedback_and_leads_for_next_attempt(tmp_path):
    run,_,events=run_loop(tmp_path,SearchModel(bad_read=True),mode="off")
    assert run["status"]=="completed",run.get("error")
    assert run["tokens_used"]==500
    assert len([e for e in events if e["type"]=="read_checkpoint_requested"])==2
    assert any(e["type"]=="tool_error" and "line_count" in e["data"]["error"] for e in events)
