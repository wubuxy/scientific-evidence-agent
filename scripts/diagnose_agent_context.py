"""Audit saved failures and context projections offline. Never calls a model.

Historical provider usage is measured. Matched-schema estimates only isolate
the final system prompt change; they are not new run costs or quality results.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"backend/app"))

from research_backend.providers import inline_tool_schemas, provider_tools
from research_backend.runtime import FINAL_SYSTEM, estimate_prompt_tokens, normalize_report_columns
from research_backend.schemas import Report


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diagnose(directory):
    trace = json.loads((directory/"trace.json").read_text(encoding="utf-8"))
    run = json.loads((directory/"run.json").read_text(encoding="utf-8"))
    drafts = json.loads((directory/"rejected-drafts.json").read_text(encoding="utf-8"))
    usage = [event["data"] for event in trace["events"] if event["type"] == "model_usage"]
    assert len(usage) == len(trace["calls"])
    measured_input = sum(item["prompt_tokens"] for item in usage)
    measured_total = sum(item["total_tokens"] for item in usage)
    assert measured_total == run["tokens_used"]
    definition = [dict(type="function",function=dict(name="finish_report",
        description="交付带真实证据 ID 的结构化科研报告；列出限制，报告待人工审核",parameters=Report.model_json_schema()))]
    projections = []
    for call, item in zip(trace["calls"],usage):
        if not item["finishing"]:
            continue
        original = call["messages"]
        projected = deepcopy(original)
        _, progress = original[0]["content"].split("\n本次覆盖进度",1)
        progress = progress.split("\n当前进入收尾阶段",1)[0]
        projected[0]["content"] = FINAL_SYSTEM + "\n本次覆盖进度" + progress
        before = estimate_prompt_tokens(original,definition)
        after = estimate_prompt_tokens(projected,definition)
        assert original[1:] == projected[1:]
        projections.append(dict(step=item["step"],measured_old_prompt_tokens=item["prompt_tokens"],
            matched_schema_estimate_old_system=before,matched_schema_estimate_phase_system=after,
            estimated_reduction=before-after,question_evidence_feedback_unchanged=True))
    rejected = 0
    for saved in drafts:
        try:
            normalized,_ = normalize_report_columns(saved["arguments"])
            Report.model_validate(normalized)
        except ValueError:
            rejected += 1
    if not drafts or rejected != len(drafts):
        raise ValueError("An uncited historical draft unexpectedly passes the new contract")
    return dict(case=directory.name,status=run["status"],historical_calls=len(usage),
        measured_input_tokens=measured_input,measured_total_tokens=measured_total,
        input_fraction=round(measured_input/measured_total,4),
        first_forced_final=next({k:item[k] for k in ("step","tokens_before_call","stop_reason")}
                               for item in usage if item["finishing"]),
        final_prompt_projection=projections,historical_drafts=len(drafts),still_rejected_by_current_contract=rejected,
        schema_characters_before_annotation_removal=len(json.dumps(inline_tool_schemas(definition),ensure_ascii=False)),
        schema_characters_after_annotation_removal=len(json.dumps(provider_tools(definition),ensure_ascii=False)),
        inputs_sha256={name:sha(directory/name) for name in ("trace.json","run.json","rejected-drafts.json")})


def main(args):
    if args.out.exists():
        raise SystemExit("Output exists; choose a new path")
    artifacts = ROOT/"docs/results/research-judgment-dev001"
    result = dict(scope="offline_saved_trace_audit_and_matched_schema_prompt_projection",model_calls=0,
        quality_result=None,provider_savings=None,
        limitations=["Current schema held constant to isolate system text; old tool payloads were not recorded.",
            "No counterfactual model responses; this does not prove an extra read or a successful report.",
            "Input share measures cumulative billed input, not context-window overflow.",
            "A proposal tag or valid citation cannot establish semantic support."],
        cases=[diagnose(artifacts/name) for name in ("original","feedback-fix")],
        code_sha256={name:sha(ROOT/name) for name in (
            "backend/app/research_backend/runtime.py","backend/app/research_backend/providers.py",
            "backend/app/research_backend/schemas.py","backend/app/research_backend/memory.py",
            "backend/app/research_backend/field_state.py","scripts/diagnose_agent_context.py")})
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"model_calls":0,"cases":[{k:c[k] for k in ("case","input_fraction","still_rejected_by_current_contract","final_prompt_projection")} for c in result["cases"]]},ensure_ascii=False))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    main(parser.parse_args())
