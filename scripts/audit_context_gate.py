"""Verify the two frozen development failures; project the read handoff offline.

Audit success means artifact consistency, not task success. No model is called.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend/app"))
from research_backend.grounding import read_context, unread_leads
from research_backend.runtime import FINAL_SYSTEM, FINAL_EVIDENCE_MIN, FINAL_OUTPUT_RESERVE, estimate_prompt_tokens, final_context


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition,message):
    if not condition:
        raise ValueError(message)


def audit(directory):
    summary=load(directory/"summary.json"); plan=load(directory/"plan.json")
    require(summary["plan_sha256"]==sha(directory/"plan.json"),"Plan changed")
    require(plan["repeats"]==2 and summary["planned_attempts"]==2,"Attempt denominator changed")
    require(summary["completed_attempts"]==2 and summary["logical_model_calls"]==12,"Attempt count drift")
    require(summary["semantic_accuracy"] is None and summary["human_rubric_score"] is None,"Fabricated aggregate human score")
    require({r["repeat"] for r in summary["results"]}=={"repeat-01","repeat-02"} and len(summary["results"])==2,"Missing/duplicate attempt")
    checks=[]
    for row in summary["results"]:
        name=row["repeat"]; folder=directory/name
        require(sha(folder/"manifest.json")==plan["manifest_sha256"][name],"Manifest drift")
        for filename,expected in row["artifact_sha256"].items():
            require(sha(folder/filename)==expected,"Artifact changed: "+name+"/"+filename)
        manifest=load(folder/"manifest.json")
        require(sha(directory/"task.json")==manifest["input_sha256"]["inputs/task.json"],"Task changed")
        require(sha(directory/"rubric.json")==manifest["rubric_sha256"],"Rubric changed")
        run=load(folder/"run.json"); trace=load(folder/"trace.json"); reads=load(folder/"reads.json")
        calls=trace["calls"]; tools=load(folder/"tool-calls.json"); drafts=load(folder/"report-drafts.json")
        require(run["status"]=="failed" and run.get("report") is None and row["status"]=="failed","Failure was promoted")
        require(not reads and row["sources_read_calls"]==0,"Read count drift")
        require(all(c["name"]!="sources_read" for c in tools),"Missing exported reads")
        require(sum(c["name"]=="sources_search" for c in tools)==row["sources_search_calls"]==8,"Search count drift")
        require(len(calls)==6 and len(drafts)==3 and all(d["validation_status"]=="rejected" for d in drafts),"Call/draft drift")
        require(row["rejected_drafts"]==3 and row["valid_report_delivered"] is False,"Draft summary drift")
        total=sum(c["tokens_used"] for c in calls)
        require(total==run["tokens_used"]==row["tokens_used"] and total<=plan["max_tokens_per_run"],"Usage drift")
        require(all(c["prompt_tokens"]+c["completion_tokens"]==c["tokens_used"] for c in calls),"Usage parts drift")
        require(row["semantic_accuracy"] is None and row["human_rubric_score"] is None,"Fabricated human score")
        # A counterfactual context projection immediately after the first search
        # batch. Use only its saved hits and the already-known task, no answer key.
        queries={json.loads(c["function"]["arguments"])["query"] for c in calls[1]["assistant"]["tool_calls"]}
        leads=[]
        for call in tools:
            if call["name"]=="sources_search" and call["arguments"]["query"] in queries:
                leads.extend(unread_leads(call["data"].get("hits",[]),{},call["arguments"]["query"]))
        require(bool(leads),"Expected first-batch search leads")
        previous=calls[2]["messages"]
        progress="\n本次覆盖进度"+previous[0]["content"].split("\n本次覆盖进度",1)[1]
        question=previous[1]["content"]
        read_tools=[t for t in calls[1]["provider_tools"] if t["function"]["name"]=="sources_read"]
        require(len(read_tools)==1,"Read tool missing")
        projected=read_context(question,progress,leads)
        calibration=min(2.0,max(.5,calls[1]["prompt_tokens"]/estimate_prompt_tokens(calls[1]["messages"],calls[1]["provider_tools"])))
        read_reserve=math.ceil(estimate_prompt_tokens(projected,read_tools)*calibration*1.1)
        final=final_context(FINAL_SYSTEM+progress,question,{},FINAL_EVIDENCE_MIN)
        final_reserve=math.ceil(estimate_prompt_tokens(final,calls[3]["provider_tools"])*calibration*1.1)
        spent=sum(c["tokens_used"] for c in calls[:2])
        allowance=min(1200,plan["max_tokens_per_run"]-spent-read_reserve-final_reserve-FINAL_OUTPUT_RESERVE)
        checks.append(dict(repeat=name,measured_tokens=total,first_batch_leads=len(leads),tokens_after_first_search=spent,
            projected_read_prompt_reserve=read_reserve,projected_final_prompt_reserve=final_reserve,
            projected_read_output_allowance=allowance,read_handoff_fits_estimate=allowance>=700,
            projected_read_tool_names=[t["function"]["name"] for t in read_tools],semantic_success=None))
    require(sum(c["measured_tokens"] for c in checks)==summary["returned_tokens"]==56034,"Aggregate cost drift")
    require(summary["valid_reports"]==0 and summary["human_review_packets"]==0,"Invalid quality result")
    require(all(item["score"] is None for item in load(directory/"rubric.json")["items"]),"Rubric is not pending")
    return dict(audit="passed",task_gate="failed",planned_attempts=2,valid_reports=0,returned_tokens=56034,
        offline_projection=True,new_model_calls=0,quality_result=None,checks=checks,
        caveat="Projected affordability does not establish actual tool use, evidence sufficiency or quality.",
        code_sha256={str(p.relative_to(ROOT)).replace("\\","/"):sha(p) for p in (
            ROOT/"scripts/audit_context_gate.py",ROOT/"backend/app/research_backend/grounding.py",ROOT/"backend/app/research_backend/runtime.py")})


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=ROOT/"docs/results/context-gate-dev001-v1")
    parser.add_argument("--out",type=Path)
    args=parser.parse_args()
    result=audit(args.root)
    if args.out:
        if args.out.exists(): raise SystemExit("Output exists; choose a new path")
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
