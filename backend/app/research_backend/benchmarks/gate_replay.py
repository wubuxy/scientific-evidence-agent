"""Post-hoc cached policy replay. Routing never accepts labels or candidate scores."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from ..runtime import estimate_prompt_tokens
from .repair_arms import Query
from .repair_replay import Diagnosis, needs_repair, tool
from .run import Result
from .trials import digest, load, write_new

METHOD = "repair-gated-search"


def replay(base, review_prediction, review_audit, search, search_audit, ceiling):
    """Choose only on the shared review, then validate cached-call affordability."""
    result = deepcopy(base)
    result.update(method=METHOD, elapsed_seconds=None, replay_only=True)
    known = base.get("known_tokens_used", base.get("tokens_used")) or 0
    result["known_tokens_used"] = known
    if base["status"] != "completed" or base.get("tokens_used") is None:
        result["intervention_status"] = "prefix_ineligible"
        return result
    calls = review_audit.get("calls", [])[:1]
    if not calls:
        result.update(status="failed", error="missing_review_call", tokens_used=None,
                      intervention_status="replay_unavailable")
        return result
    review_cost = calls[0].get("tokens_used")
    if review_cost is None:
        result.update(tokens_used=None, intervention_status="unknown_review_usage")
        return result
    known += review_cost
    result.update(tokens_used=known, known_tokens_used=known,
                  model_calls=base.get("model_calls", 0)+1, intervention_calls=1)
    if known > ceiling:
        result.update(status="failed", error="token_budget_exceeded", intervention_status="replay_unavailable")
        return result
    diagnosis = review_audit.get("review")
    if not diagnosis or review_prediction.get("intervention_status", "").startswith("review_failed:"):
        result["intervention_status"] = "retain_review_failed"
        return result
    diagnosis = Diagnosis.model_validate(diagnosis)
    if not needs_repair(diagnosis):
        result["intervention_status"] = "retain_review_passed"
        return result
    calls = search_audit.get("calls", [])
    if not calls:
        result.update(status="failed", error="missing_search_calls", tokens_used=None,
                      intervention_status="replay_unavailable")
        return result
    for index, call in enumerate(calls):
        definition = tool("propose_search", Query) if index == 0 else tool("finish_report", Result)
        output_limit = 300 if index == 0 else 900
        # Require the same output allowance as the original request, not merely
        # its observed completion length; otherwise replay would change sampling.
        estimated = int(estimate_prompt_tokens(call["messages"], [definition])*1.15)
        if ceiling-known-estimated < output_limit or call.get("tokens_used") is None:
            result.update(status="failed", error="cached_call_not_replayable", tokens_used=None,
                          known_tokens_used=known, intervention_status="replay_unavailable")
            return result
        known += call["tokens_used"]
    result = deepcopy(search)
    result.update(method=METHOD, elapsed_seconds=None, replay_only=True, tokens_used=known,
                  known_tokens_used=known, model_calls=search.get("model_calls", 0)+1,
                  intervention_calls=len(calls)+1, intervention_status="gated_search_replayed")
    if known > ceiling:
        result.update(status="failed", error="token_budget_exceeded")
    return result


def artifact_paths(plan):
    for repeat in range(plan["repeats"]):
        for tid in plan["task_ids"]:
            for method, names in (("repair-baseline", ["prediction.json"]),
                                  ("repair-reread", ["prediction.json", "intervention.json"]),
                                  ("repair-search", ["prediction.json", "intervention.json"])):
                for name in names:
                    yield f"trial-{repeat:03d}/{method}/{tid}/{name}"


def make_plan(prior):
    prior = Path(prior)
    source = load(prior/"plan.json")
    return dict(scope="posthoc_token_constrained_cached_policy_replay_not_new_trials",
        created_utc=datetime.now(timezone.utc).isoformat(), source_plan_sha256=digest(prior/"plan.json"),
        task_ids=source["task_ids"], repeats=source["repeats"], max_tokens=source["max_tokens"],
        analysis_seed=source["analysis_seed"], inputs_sha256=source["inputs_sha256"],
        artifacts_sha256={p:digest(prior/p) if (prior/p).exists() else None for p in artifact_paths(source)},
        code_sha256={p:digest(Path(__file__).with_name(p)) for p in
                     ("gate_replay.py", "repair_replay.py", "repair_arms.py")})


def generate(plan_path, prior, out):
    plan, prior, out = load(plan_path), Path(prior), Path(out)
    current = make_plan(prior)
    if any(plan[k] != current[k] for k in plan if k != "created_utc"):
        raise ValueError("Source artifacts or replay implementation changed after plan freeze")
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/"plan.json", plan)
    for repeat in range(plan["repeats"]):
        for tid in plan["task_ids"]:
            trial = prior/f"trial-{repeat:03d}"
            try:
                result = replay(load(trial/"repair-baseline"/tid/"prediction.json"),
                    load(trial/"repair-reread"/tid/"prediction.json"),
                    load(trial/"repair-reread"/tid/"intervention.json"),
                    load(trial/"repair-search"/tid/"prediction.json"),
                    load(trial/"repair-search"/tid/"intervention.json"), plan["max_tokens"])
            except Exception as exc:
                result = dict(task_id=tid, method=METHOD, status="failed", error=type(exc).__name__,
                              answers=[], reads=[], tokens_used=None, intervention_status="replay_unavailable")
            write_new(out/f"trial-{repeat:03d}"/tid/"prediction.json", result)


def score(inputs_path, gold_path, prior, replay_root):
    # Gold is confined to this scoring function; generate() cannot read it.
    from .score import score_prediction
    from .trials import analyze_trials, cluster_interval, task_clusters
    original = analyze_trials(inputs_path, gold_path, prior)
    plan = load(Path(replay_root)/"plan.json")
    if plan["source_plan_sha256"] != original["plan_sha256"]:
        raise ValueError("Replay and scored source plan differ")
    inputs, gold = load(inputs_path), load(gold_path)
    tasks = {t["id"]:t for t in inputs["tasks"]}
    documents = {d["id"]:d for d in inputs["documents"]}
    rows, artifacts = [], {}
    for repeat in range(plan["repeats"]):
        for tid in plan["task_ids"]:
            relative = f"trial-{repeat:03d}/{tid}/prediction.json"
            path = Path(replay_root)/relative
            artifacts[relative] = digest(path) if path.exists() else None
            p = load(path) if path.exists() else dict(method=METHOD, status="missing", answers=[], reads=[])
            row = score_prediction(p, tasks[tid], gold["tasks"][tid], documents)
            rows.append(dict(row, repeat=repeat, intervention_status=p.get("intervention_status"),
                known_tokens_used=p.get("known_tokens_used"),
                f1_delta_from_prefix=row["answer_token_f1"]-next(r["answer_token_f1"] for r in original["records"]
                    if r["task_id"]==tid and r["repeat"]==repeat and r["method"]=="repair-baseline")))
    means = {tid:sum(r["answer_token_f1"] for r in rows if r["task_id"]==tid)/plan["repeats"] for tid in plan["task_ids"]}
    clusters = task_clusters([tasks[tid] for tid in plan["task_ids"]])
    other = original["per_task_mean_f1"]
    return dict(scope=plan["scope"], plan_sha256=digest(Path(replay_root)/"plan.json"),
        source_plan_sha256=plan["source_plan_sha256"], artifacts_sha256=artifacts,
        scorer_sha256=digest(__file__), gold_sha256=digest(gold_path), additional_physical_calls=0,
        planned=len(rows), completed=sum(r["status"]=="completed" for r in rows),
        mean_answer_token_f1=sum(means.values())/len(means),
        mean_logical_tokens=sum(r["tokens_used"] for r in rows)/len(rows) if all(r["tokens_used"] is not None for r in rows) else None,
        decisions=dict(Counter(r["intervention_status"] or "missing" for r in rows)),
        improved_vs_prefix=sum(r["f1_delta_from_prefix"]>1e-12 for r in rows),
        worsened_vs_prefix=sum(r["f1_delta_from_prefix"] < -1e-12 for r in rows),
        comparisons={"G_minus_E":cluster_interval({t:means[t]-other["repair-search"][t] for t in means},clusters,plan["analysis_seed"]),
                     "C_minus_G":cluster_interval({t:other["repair-reread"][t]-means[t] for t in means},clusters,plan["analysis_seed"])},
        per_task_mean_f1=means, records=rows,
        limitations=["Post-hoc same-pool cached replay, not independent online trials or a causal estimate.",
                     "No end-to-end latency guarantee; C/G also differ in review feedback supplied to writer.",
                     "Lexical F1 and citation addresses do not establish semantic support."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--prior", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("generate")
    for name in ("plan", "prior", "out"):
        p.add_argument("--"+name, type=Path, required=True)
    p = sub.add_parser("score")
    for name in ("inputs", "gold", "prior", "replay", "out"):
        p.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan": write_new(args.out, make_plan(args.prior))
    elif args.command == "generate": generate(args.plan, args.prior, args.out)
    else: write_new(args.out, score(args.inputs, args.gold, args.prior, args.replay))


if __name__ == "__main__":
    main()
