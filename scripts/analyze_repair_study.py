"""Publish score-only repair-study artifacts; keep source text and traces local."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend/app"))
from research_backend.benchmarks.trials import analyze_trials, cluster_interval, digest, load, task_clusters, write_new


def summarize(analysis, inputs, plan, root):
    selected = [t for t in inputs["tasks"] if t["id"] in plan["task_ids"]]
    clusters = task_clusters(selected)
    means = analysis["per_task_mean_f1"]
    contrasts = {}
    for reference in ("repair-search", "repair-keyword", "repair-rewrite", "repair-baseline"):
        differences = {tid:means["repair-reread"][tid]-means[reference][tid] for tid in plan["task_ids"]}
        contrasts[reference] = cluster_interval(differences, clusters, plan["analysis_seed"])
    summaries = analysis["summaries"]
    cost_c, cost_e = (summaries[m]["mean_tokens"] for m in ("repair-reread", "repair-search"))
    primary = contrasts["repair-search"]
    cost_ratio = cost_c / cost_e if cost_c is not None and cost_e else None
    criteria = dict(f1_gain_at_least_5pp=primary["mean_difference"] >= .05,
        lower_95_above_zero=primary["bootstrap_95"] is not None and primary["bootstrap_95"][0] > 0,
        cost_ratio_at_most_1_2=cost_ratio <= 1.2 if cost_ratio is not None else None)
    by_key = {(r["repeat"],r["task_id"],r["method"]):r for r in analysis["records"]}
    diagnostics, usage = [], []
    for repeat in range(plan["repeats"]):
        trial = root / f"trial-{repeat:03d}"
        usage.append(load(trial/"usage.json") if (trial/"usage.json").exists() else None)
        for tid in plan["task_ids"]:
            path = trial/"_shared"/tid/"prediction.json"
            base = load(path) if path.exists() else {}
            original = {(e["source_id"], e["start_line"], e["end_line"]) for e in base.get("reads",[])}
            for method in plan["methods"]:
                directory = trial/method/tid
                prediction = load(directory/"prediction.json") if (directory/"prediction.json").exists() else {}
                intervention = load(directory/"intervention.json") if (directory/"intervention.json").exists() else {}
                review = intervention.get("review") or {}
                reads = prediction.get("reads",[])
                current = {(e["source_id"],e["start_line"],e["end_line"]) for e in reads}
                row = by_key[(repeat,tid,method)]
                base_f1 = by_key[(repeat,tid,"repair-baseline")]["answer_token_f1"]
                diagnostics.append(dict(repeat=repeat,task_id=tid,method=method,status=row["status"],
                    prefix_status=base.get("status","missing"),error=prediction.get("error"),
                    intervention_status=prediction.get("intervention_status"),
                    review={k:review.get(k) for k in ("support","adequacy","role","stage")},
                    extra_read_windows=len(current-original),read_windows=len(reads),
                    f1_delta_from_prefix=row["answer_token_f1"]-base_f1,
                    answer_changed=prediction.get("answers")!=base.get("answers"),
                    model_calls=prediction.get("model_calls"),intervention_calls=prediction.get("intervention_calls")))
    counts = {}
    for method in plan["methods"]:
        rows = [d for d in diagnostics if d["method"]==method]
        counts[method] = dict(status=dict(Counter(d["status"] for d in rows)),
            interventions=dict(Counter(d["intervention_status"] or "missing" for d in rows)),
            extra_read_attempts=sum(d["extra_read_windows"]>0 for d in rows),
            improved_vs_prefix=sum(d["f1_delta_from_prefix"]>1e-12 for d in rows),
            worsened_vs_prefix=sum(d["f1_delta_from_prefix"] < -1e-12 for d in rows))
    return dict(scope="exploratory reused development pool; lexical QA metrics, not semantic report quality",
        plan_sha256=analysis["plan_sha256"],task_clusters=clusters,
        primary_comparison="repair-reread minus repair-search",contrasts=contrasts,
        secondary_intervals="Unadjusted exploratory intervals; do not choose a secondary contrast as the main finding",
        cost_ratio_c_to_e=cost_ratio,advance_criteria=criteria,advance_to_confirmation=all(v is True for v in criteria.values()),
        summaries=summaries,usage_by_repeat=usage,
        known_physical_tokens=sum(u["measured_llm_tokens"] for u in usage if u),
        known_logical_policy_tokens=sum(u["logical_policy_tokens"] for u in usage if u),
        usage_complete=all(u is not None and u["missing_usage_runs"]==0 for u in usage),
        diagnostics=counts,per_attempt_diagnostics=diagnostics)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("inputs","gold","run","out"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--prior-failure-summary",type=Path)
    args=parser.parse_args()
    analysis=analyze_trials(args.inputs,args.gold,args.run)
    result=summarize(analysis,load(args.inputs),load(args.run/"plan.json"),args.run)
    if args.prior_failure_summary:
        previous=load(args.prior_failure_summary)
        result["prior_infrastructure_batch"]={k:previous[k] for k in
            ("status","planned_policy_outcomes","failed_policy_outcomes","model_calls","known_model_tokens")}
        result["prior_failure_summary_sha256"]=digest(args.prior_failure_summary)
    args.out.mkdir(parents=True,exist_ok=False)
    write_new(args.out/"analysis.json",analysis)
    write_new(args.out/"summary.json",result)
    write_new(args.out/"provenance.json",dict(analysis_script_sha256=digest(__file__),
        inputs_sha256=digest(args.inputs),gold_sha256=digest(args.gold),plan_sha256=digest(args.run/"plan.json"),
        source_text_and_raw_traces="Archived separately; score-only publication does not independently reproduce answer scoring without original inputs and predictions"))
    print(json.dumps({k:result[k] for k in ("primary_comparison","contrasts","cost_ratio_c_to_e","advance_criteria","known_physical_tokens","usage_complete")},indent=2))


if __name__=="__main__":
    main()
