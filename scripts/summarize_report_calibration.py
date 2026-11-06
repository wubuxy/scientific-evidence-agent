"""Publish mechanical metrics; keep candidate opinions with the coordinator until blind review ends."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/"backend/app"))
from research_backend.benchmarks.claim_labels import validated_reviews
from research_backend.benchmarks.trials import digest,load,write_new


def summarize(run, annotations, judges, report_scores, public):
    run,annotations,judges,public=map(Path,(run,annotations,judges,public))
    plan=load(run/"plan.json");bundle=load(annotations/"cases.json");scores=load(report_scores)
    cases=bundle["cases"];models=load(judges/"plan.json")["models"]
    opinions={};coverage={};cost=0;unknown=False
    for model in models:
        path=judges/model/"reviews.json"
        values=load(path);opinions[model]=validated_reviews(bundle,path)
        rows=list(opinions[model].values())
        coverage[model]=dict(planned=len(cases),recorded=len(rows),
            completed=sum(r["status"]=="completed" for r in rows),
            failures=dict(Counter(r["error"] for r in rows if r["status"]!="completed")),
            returned_usage_tokens=values["tokens_used"],known_returned_usage_tokens=values["known_tokens_used"],
            logical_model_calls=len(values["calls"]),reviews_sha256=digest(path))
        cost+=values["known_tokens_used"];unknown|=values["tokens_used"] is None
    joint=[c["id"] for c in cases if all(opinions[m].get(c["id"],{}).get("status")=="completed" for m in models)]
    agree=sum(opinions[models[0]][i]["review"]["verdict"]==opinions[models[1]][i]["review"]["verdict"] for i in joint)
    coordinator=dict(only_for_coordinator_until_independent_labels_locked=True,
        common_completed=len(joint),agreement_count=agree,agreement_rate=agree/len(joint) if joint else None,
        verdict_counts={m:dict(Counter(r["review"]["verdict"] for r in opinions[m].values() if r["status"]=="completed")) for m in models},
        cases_sha256=digest(annotations/"cases.json"),note="Model agreement is not accuracy; same-family judges are correlated.")
    write_new(judges/"coordinator-comparison.json",coordinator)
    mechanical=scores["summaries"]["report-grep"]
    selection=load(annotations/"selection-summary.json")
    summary=dict(date="2026-11-07",scope="natural report generation and blind-review preparation; human calibration pending",
        tasks=len(plan["task_ids"]),papers=12,repeats=plan["repeats"],planned_reports=16,
        generator_model=plan["model"],generation=mechanical,
        natural_report_units=selection["source_case_count"],selected_human_units=len(cases),
        sampling_counts=selection["report_counts"],judges=coverage,
        known_judge_returned_usage_tokens=cost,any_judge_unknown_usage=unknown,
        preflight_returned_usage_tokens=621,
        human_labels_completed=0,human_accuracy=None,cohen_kappa=None,model_agreement_public=None,
        withheld_until_human_labels="Candidate verdicts, model agreement and item-level results stay with the coordinator to protect independent annotation.",
        usage_boundary="Provider-returned logical-call usage, not independently audited wire retries or invoice billing; see transport-policy-note.json",
        original_plan_sha256=digest(run/"plan.json"),judge_plan_sha256=digest(judges/"plan.json"),
        cases_sha256=digest(annotations/"cases.json"),
        annotation_packets={n:dict(bytes=(annotations/n).stat().st_size,sha256=digest(annotations/n)) for n in ("annotator-a.zip","annotator-b.zip")},
        limitations=["Contract and field presence are not semantic support or answer adequacy.",
            "The human sample is generated-report units, not independent papers; partial reports are not replaced.",
            "27B is also the generator; 397B is a same-family larger comparator, not established ground truth."])
    public.mkdir(parents=True,exist_ok=False)
    write_new(public/"summary.json",summary)
    clean=[{k:v for k,v in row.items() if k not in {"units","valid_reads"}} for row in scores["records"]]
    write_new(public/"mechanical-report-scores.json",dict(scope=scores["scope"],summaries=scores["summaries"],
        records=clean,plan_sha256=scores["plan_sha256"],scorer_code_sha256=scores["scorer_code_sha256"],
        trial_artifacts_sha256=scores["trial_artifacts_sha256"],limitations=scores["limitations"]))
    print(json.dumps({k:summary[k] for k in ("planned_reports","selected_human_units","human_labels_completed","known_judge_returned_usage_tokens")}))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("run","annotations","judges","report-scores","public"):
        p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args();summarize(a.run,a.annotations,a.judges,a.report_scores,a.public)
