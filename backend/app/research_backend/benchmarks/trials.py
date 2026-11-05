"""Frozen full-trajectory repeats and scorer-only reliability analysis.

Generation uses the existing short-answer benchmark agent, NOT the workbench's
full-report Runtime. No gold path or gold contents enter the generation process.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import sys


METHODS = {"grep", "grep-ledger", "rag", "rag-once", "report-grep", "report-rag",
           "repair-baseline", "repair-rewrite", "repair-reread", "repair-keyword", "repair-search"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def code_hashes():
    root = Path(__file__).resolve().parents[1]
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*.py"))}


def frozen_inputs(path):
    inputs, manifest = load(path), load(Path(path).parent / "manifest.json")
    if manifest["inputs_sha256"] != digest(path):
        raise ValueError("Inputs differ from the frozen manifest")
    identifiers = [task["id"] for task in inputs["tasks"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate task ID in inputs")
    return inputs, manifest


def make_plan(inputs_path, task_ids, methods, *, repeats=2, model="qwen3.5-27b", reference=None,
              max_tokens=20000, max_steps=10, max_seconds=300, concurrency=2,
              success_f1=1.0, seed=20261105, allow_test=False):
    inputs, _ = frozen_inputs(inputs_path)
    if inputs["split"] == "test" and not allow_test:
        raise ValueError("Held-out test requires --allow-test after the method is frozen")
    if not isinstance(task_ids, list) or not task_ids or not all(isinstance(t, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", t) for t in task_ids) or len(task_ids) != len(set(task_ids)):
        raise ValueError("Task IDs must be unique, nonempty, safe path components")
    if set(task_ids) - {t["id"] for t in inputs["tasks"]}:
        raise ValueError("Task ID absent from frozen inputs")
    if not isinstance(methods, list) or not methods or not all(isinstance(m, str) for m in methods) or len(methods) != len(set(methods)) or set(methods) - METHODS:
        raise ValueError("Methods must be a unique supported list")
    reference = reference or methods[0]
    if reference not in methods:
        raise ValueError("Reference method must be included")
    report_suite = all(m.startswith("report-") for m in methods)
    if any(m.startswith("report-") for m in methods) and not report_suite:
        raise ValueError("Report and short-answer metrics require separate plans")
    if report_suite and max_steps > 30:
        raise ValueError("Production report Runtime supports at most 30 steps")
    if any(m.startswith("repair-") for m in methods):
        chosen = [t for t in inputs["tasks"] if t["id"] in task_ids]
        if any(len(t["source_ids"]) != 1 or len(t["targets"]) != 1 for t in chosen):
            raise ValueError("Repair study requires one paper and target per task")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ValueError("Expected a nonempty model name")
    if any(type(v) is not int for v in (repeats, max_tokens, max_steps, max_seconds, concurrency, seed)) or not 1 <= repeats <= 10 or not 2000 <= max_tokens <= 100000 or not 1 <= max_steps <= 40 or not 10 <= max_seconds <= 900 or not 1 <= concurrency <= 4:
        raise ValueError("Invalid repeat, budget, steps, timeout or concurrency")
    if isinstance(success_f1, bool) or not isinstance(success_f1, (int,float)) or not math.isfinite(success_f1) or not 0 < success_f1 <= 1:
        raise ValueError("Success F1 must be in (0, 1]")
    return dict(format=1, scope="production_report_runtime_repeats" if report_suite else "short_answer_agent_full_trajectory_repeats_not_workbench_report_quality",
        dataset=inputs["dataset"], split=inputs["split"], inputs_sha256=digest(inputs_path),
        manifest_sha256=digest(Path(inputs_path).parent / "manifest.json"), task_ids=task_ids,
        methods=methods, reference=reference, repeats=repeats, model=model, extra_body={"enable_thinking": False}, max_tokens=max_tokens,
        max_steps=max_steps, max_seconds=max_seconds, concurrency=concurrency, success_f1=success_f1,
        analysis_seed=seed, code_sha256=code_hashes(),
        nominal_llm_token_ceiling=len(task_ids)*len(methods)*repeats*max_tokens,
        created_utc=datetime.now(timezone.utc).isoformat(),
        notes=["Nominal LLM ceiling excludes embedding/judge calls and cannot recover unknown provider billing.",
               "Shared embedding cache; elapsed time is not a cold-start latency benchmark.",
               "Lexical success requires every target to meet the frozen token-F1 threshold and valid nonempty citations when answered; not semantic correctness."])


def validate_plan(plan, inputs_path, *, require_current_code=False):
    inputs, _ = frozen_inputs(inputs_path)
    if plan.get("format") != 1 or plan["inputs_sha256"] != digest(inputs_path) or plan["manifest_sha256"] != digest(Path(inputs_path).parent / "manifest.json"):
        raise ValueError("Plan and frozen input manifest do not match")
    if plan["dataset"] != inputs["dataset"] or plan["split"] != inputs["split"]:
        raise ValueError("Plan dataset/split mismatch")
    checked = make_plan(inputs_path, plan["task_ids"], plan["methods"], repeats=plan["repeats"],
        model=plan["model"], reference=plan["reference"], max_tokens=plan["max_tokens"], max_steps=plan["max_steps"],
        max_seconds=plan["max_seconds"], concurrency=plan["concurrency"], success_f1=plan["success_f1"],
        seed=plan["analysis_seed"], allow_test=True)
    for key in ("scope", "extra_body", "nominal_llm_token_ceiling"):
        if plan.get(key) != checked[key]:
            raise ValueError("Invalid derived plan field: " + key)
    if require_current_code and plan["code_sha256"] != checked["code_sha256"]:
        raise ValueError("Agent code changed after the plan was written; create a new plan before running")
    return inputs


def run_trials(plan_path, inputs_path, output, *, env_file=None, allow_test=False, resume=False):
    plan, output = load(plan_path), Path(output)
    validate_plan(plan, inputs_path, require_current_code=True)
    if plan["split"] == "test" and not allow_test:
        raise ValueError("Held-out generation requires --allow-test at execution time")
    plan_hash = digest(plan_path)
    if resume:
        if digest(output / "plan.json") != plan_hash or load(output / "study.json")["plan_sha256"] != plan_hash:
            raise ValueError("Resume plan differs from the original")
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "plan.json").write_bytes(Path(plan_path).read_bytes())
        write_new(output / "task_ids.json", plan["task_ids"])
        write_new(output / "study.json", dict(plan_sha256=plan_hash, repeats=plan["repeats"], scope=plan["scope"]))
    for repeat in range(plan["repeats"]):
        trial = output / f"trial-{repeat:03d}"
        if trial.exists():
            if resume:
                print(json.dumps(dict(repeat=repeat, status="preserved_existing_trial_no_retry")), flush=True)
                continue
            raise FileExistsError(trial)
        command = [sys.executable, "-m", "research_backend.benchmarks.run", "--inputs", str(Path(inputs_path).resolve()),
            "--out", str(trial.resolve()), "--model", plan["model"], "--methods", *plan["methods"],
            "--task-ids-file", str((output / "task_ids.json").resolve()),
            "--cache", str((output / "embedding-cache").resolve())]
        for key in ("max_tokens", "max_steps", "max_seconds", "concurrency"):
            command += ["--" + key.replace("_", "-"), str(plan[key])]
        if env_file:
            command += ["--env-file", str(Path(env_file).resolve())]
        if allow_test:
            command.append("--allow-test")
        environment = os.environ.copy()
        app = str(Path(__file__).resolve().parents[2])
        environment["PYTHONPATH"] = app + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        timeout = (len(plan["task_ids"])*(len(plan["methods"])+2)*plan["max_seconds"] if any(m.startswith("repair-") for m in plan["methods"])
                   else math.ceil(len(plan["task_ids"])*len(plan["methods"])/plan["concurrency"])*plan["max_seconds"]) + 120
        subprocess.run(command, check=True, timeout=timeout, env=environment)


def task_clusters(tasks):
    """Keep tasks sharing ANY paper in one bootstrap unit, including cross-paper tasks."""
    parent = {t["id"]: t["id"] for t in tasks}

    def find(identifier):
        while parent[identifier] != identifier:
            parent[identifier] = parent[parent[identifier]]
            identifier = parent[identifier]
        return identifier

    seen = {}
    for task in tasks:
        for sid in task["source_ids"]:
            if sid in seen:
                parent[find(task["id"])] = find(seen[sid])
            else:
                seen[sid] = task["id"]
    groups = defaultdict(list)
    for task in tasks:
        groups[find(task["id"])].append(task["id"])
    return list(groups.values())


def cluster_interval(deltas, clusters, seed, draws=5000):
    result = dict(mean_difference=sum(deltas.values())/len(deltas), tasks=len(deltas), independent_paper_groups=len(clusters), bootstrap_95=None)
    if len(clusters) < 2:
        result["warning"] = "Fewer than two disjoint paper groups; no meaningful bootstrap interval"
        return result
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        values = [deltas[tid] for group in rng.choices(clusters, k=len(clusters)) for tid in group]
        samples.append(sum(values)/len(values))
    samples.sort()
    result["bootstrap_95"] = [samples[int(.025*draws)], samples[min(draws-1, int(.975*draws))]]
    return result


def trajectory_diagnostics(directory):
    path = directory / "state/research.sqlite3"
    if not path.exists():
        return None
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as database:
        calls = [json.loads(row[0]) for row in database.execute("SELECT payload FROM records WHERE kind='tool_call'")]
    fingerprints = Counter(json.dumps([call["name"], call["arguments"]], sort_keys=True, ensure_ascii=False) for call in calls)
    return dict(tool_calls=len(calls), failed_tool_calls=sum(call["status"] != "succeeded" for call in calls),
                repeated_identical_calls=sum(n-1 for n in fingerprints.values()),
                note="Repeated calls are diagnostic counts, not proof of an agent error")


def analyze_trials(inputs_path, gold_path, run_directory, reviews_path=None):
    if load(Path(run_directory) / "plan.json")["scope"] == "production_report_runtime_repeats":
        from .reports import analyze_reports
        return analyze_reports(inputs_path, gold_path, run_directory, reviews_path)
    if reviews_path:
        raise ValueError("Joint-claim reviews apply to report plans")
    # Gold-side import is deliberately confined to the scoring phase.
    from .score import score_prediction

    root = Path(run_directory)
    plan = load(root / "plan.json")
    inputs = validate_plan(plan, inputs_path)
    manifest, gold = load(Path(inputs_path).parent / "manifest.json"), load(gold_path)
    if digest(gold_path) != manifest["gold_sha256"] or digest(root / "plan.json") != load(root / "study.json")["plan_sha256"]:
        raise ValueError("Gold or study plan hash mismatch")
    tasks = {t["id"]: t for t in inputs["tasks"]}
    documents = {d["id"]: d for d in inputs["documents"]}
    selected = [tasks[tid] for tid in plan["task_ids"]]
    records, protocol_reference, artifacts = [], None, {}
    for repeat in range(plan["repeats"]):
        trial = root / f"trial-{repeat:03d}"
        artifacts[trial.name] = {name:digest(trial / name) if (trial / name).exists() else None
                                for name in ("protocol.json", "predictions.jsonl")}
        predictions = []
        if (trial / "protocol.json").exists():
            protocol = load(trial / "protocol.json")
            for key in ("dataset", "split", "inputs_sha256", "model", "extra_body", "max_tokens", "max_steps", "methods", "task_ids"):
                if protocol.get(key) != plan[key]:
                    raise ValueError("Trial protocol drift: " + key)
            normalized_code = {k.replace("\\", "/"): v for k,v in protocol["code_sha256"].items()}
            if normalized_code != plan["code_sha256"]:
                raise ValueError("Trial code differs from preregistered plan")
            if protocol_reference is not None:
                for key in ("extra_body", "embedding_model", "retrieval"):
                    if protocol.get(key) != protocol_reference.get(key):
                        raise ValueError("Trial runtime configuration drift: " + key)
            if any(m.startswith("repair-") for m in plan["methods"]):
                from .repair_arms import POLICY
                if protocol.get("repair_policy") != POLICY:
                    raise ValueError("Trial repair policy drift")
            protocol_reference = protocol
            if (trial / "predictions.jsonl").exists():
                predictions = [json.loads(line) for line in (trial / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        elif (trial / "predictions.jsonl").exists():
            raise ValueError("Predictions have no trial protocol")
        keyed = {(p["task_id"], p["method"]): p for p in predictions}
        if len(keyed) != len(predictions):
            raise ValueError("Duplicate prediction; never choose the better attempt")
        if any(tid not in plan["task_ids"] or method not in plan["methods"] for tid,method in keyed):
            raise ValueError("Unexpected task or method in predictions")
        for method in plan["methods"]:
            for tid in plan["task_ids"]:
                prediction = keyed.get((tid, method), dict(task_id=tid, method=method, status="missing", answers=[], reads=[]))
                answers = prediction.get("answers", [])
                if len({answer["target_id"] for answer in answers}) != len(answers):
                    raise ValueError("Duplicate target answer")
                if len({item["id"] for item in prediction.get("reads", [])}) != len(prediction.get("reads", [])):
                    raise ValueError("Duplicate read evidence ID")
                row = score_prediction(prediction, tasks[tid], gold["tasks"][tid], documents)
                row["known_tokens_used"] = prediction.get("known_tokens_used", row["tokens_used"])
                for key in ("tokens_used", "known_tokens_used", "elapsed_seconds"):
                    value = row[key]
                    if value is not None and (isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0):
                        raise ValueError("Invalid measured resource: " + key)
                complete = row["status"] == "completed"
                citation_complete = ({a["target_id"] for a in answers} == {t["id"] for t in tasks[tid]["targets"]}
                                     and all(answer["unanswerable"] or answer["evidence_ids"] for answer in answers))
                passed = complete and citation_complete and all(target["answer_token_f1"] >= plan["success_f1"] and target["citation_ids_valid"] for target in row["targets"])
                if row["tokens_used"] is not None and row["tokens_used"] > plan["max_tokens"]:
                    passed = False
                category = ("execution_or_missing" if not complete else "lexical_pass" if passed else
                    "no_read" if not prediction.get("reads") else
                    "invalid_citation" if not citation_complete or not all(t["citation_ids_valid"] for t in row["targets"]) else
                    "annotated_evidence_not_read" if row["read_paragraph_recall"] == 0 else "answer_or_budget_mismatch")
                records.append(dict(row, repeat=repeat, lexical_pass=bool(passed), diagnostic_observation=category,
                    intervention_status=prediction.get("intervention_status"), model_calls=prediction.get("model_calls"),
                    trajectory=trajectory_diagnostics(trial / ("_shared" if method.startswith("repair-") else method) / tid)))
    summaries, task_means = {}, {}
    for method in plan["methods"]:
        rows = [r for r in records if r["method"] == method]
        task_rows = {tid: [r for r in rows if r["task_id"] == tid] for tid in plan["task_ids"]}
        task_means[method] = {tid: sum(r["answer_token_f1"] for r in group)/plan["repeats"] for tid,group in task_rows.items()}
        known_tokens = [r["tokens_used"] for r in rows if r["tokens_used"] is not None]
        partial_known_tokens = [r["known_tokens_used"] for r in rows if r["tokens_used"] is None and r["known_tokens_used"] is not None]
        known_times = [r["elapsed_seconds"] for r in rows if r["elapsed_seconds"] is not None]
        reliability = {}
        n = plan["repeats"]
        for k in range(1,n+1):
            successes = [sum(r["lexical_pass"] for r in group) for group in task_rows.values()]
            reliability[str(k)] = dict(pass_at_k=sum(1-math.comb(n-c,k)/math.comb(n,k) for c in successes)/len(successes),
                pass_all_k=sum(math.comb(c,k)/math.comb(n,k) for c in successes)/len(successes))
        summaries[method] = dict(tasks=len(task_rows), repeats=n, planned_attempts=len(rows),
            completed=sum(r["status"] == "completed" for r in rows),
            mean_answer_token_f1=sum(task_means[method].values())/len(task_rows), lexical_reliability=reliability,
            mean_tokens=sum(known_tokens)/len(rows) if len(known_tokens)==len(rows) else None,
            total_known_tokens=sum(known_tokens)+sum(partial_known_tokens), known_token_attempts=len(known_tokens), unknown_token_attempts=len(rows)-len(known_tokens),
            mean_elapsed_seconds_known_only=sum(known_times)/len(known_times) if known_times else None,
            known_elapsed_attempts=len(known_times), diagnostic_counts=dict(Counter(r["diagnostic_observation"] for r in rows)))
        for metric in ("read_paragraph_recall", "cited_paragraph_overlap_f1"):
            values = [r[metric] for r in rows if r[metric] is not None]
            summaries[method][metric] = sum(values)/len(values) if values else None
            summaries[method][metric + "_n"] = len(values)
        targets = [target for row in rows for target in row["targets"]]
        summaries[method]["citation_address_valid_target_rate"] = sum(t["citation_ids_valid"] for t in targets)/len(targets)
        summaries[method]["abstention_agreement_with_any_reference"] = sum(t["abstention_agrees_with_any_reference"] for t in targets)/len(targets)
    clusters = task_clusters(selected)
    baseline = task_means[plan["reference"]]
    comparisons = {method:cluster_interval({tid:values[tid]-baseline[tid] for tid in baseline}, clusters, plan["analysis_seed"])
                   for method,values in task_means.items() if method != plan["reference"]}
    return dict(scope=plan["scope"], plan_sha256=digest(root / "plan.json"), gold_sha256=digest(gold_path),
        scorer_code_sha256={"trials.py":digest(__file__), "score.py":digest(Path(__file__).with_name("score.py"))},
        trial_artifacts_sha256=artifacts, summaries=summaries,
        reference=plan["reference"], paired_answer_f1=comparisons, per_task_mean_f1=task_means, records=records,
        limitations=["Lexical success and valid exported citations are not semantic support or workbench full-report accuracy.",
            "Missing and failed planned attempts remain in all quality/reliability denominators; missing usage is never zero cost.",
            "Repeats are averaged per task; bootstrap resamples connected paper groups, not independent individual repetitions.",
            "Pass-at-k and pass-all-k are combinatorial estimates over the observed trials; exchangeability and finite-sample uncertainty apply.",
            "Trajectory categories are observations, not causal failure labels. Embedding/judge costs are outside per-attempt LLM tokens."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Freeze task IDs, code, repeats and lexical-success definition without model calls")
    plan.add_argument("--inputs", type=Path, required=True)
    plan.add_argument("--task-ids-file", type=Path, required=True)
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--methods", nargs="+", choices=sorted(METHODS), required=True)
    plan.add_argument("--reference")
    plan.add_argument("--repeats", type=int, default=2)
    plan.add_argument("--model", default="qwen3.5-27b")
    plan.add_argument("--max-tokens", type=int, default=20000)
    plan.add_argument("--max-steps", type=int, default=10)
    plan.add_argument("--max-seconds", type=int, default=300)
    plan.add_argument("--concurrency", type=int, default=2)
    plan.add_argument("--success-f1", type=float, default=1.0)
    plan.add_argument("--seed", type=int, default=20261105)
    plan.add_argument("--allow-test", action="store_true")
    run = commands.add_parser("run", help="Run complete repeated agent trajectories; calls the configured model")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--inputs", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--env-file", type=Path)
    run.add_argument("--allow-test", action="store_true")
    run.add_argument("--resume", action="store_true")
    score = commands.add_parser("score", help="Score all planned attempts independently of generation")
    score.add_argument("--inputs", type=Path, required=True)
    score.add_argument("--gold", type=Path, required=True)
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--out", type=Path, required=True)
    score.add_argument("--reviews", type=Path, help="Optional joint-claim candidate reviews for a report plan")
    args = parser.parse_args()
    if args.command == "plan":
        value = make_plan(args.inputs, load(args.task_ids_file), args.methods, repeats=args.repeats, model=args.model,
            reference=args.reference, max_tokens=args.max_tokens, max_steps=args.max_steps, max_seconds=args.max_seconds,
            concurrency=args.concurrency, success_f1=args.success_f1, seed=args.seed, allow_test=args.allow_test)
        write_new(args.out, value)
        print(json.dumps(dict(plan_sha256=digest(args.out), tasks=len(value["task_ids"]), repeats=value["repeats"], nominal_llm_token_ceiling=value["nominal_llm_token_ceiling"])))
    elif args.command == "run":
        run_trials(args.plan, args.inputs, args.out, env_file=args.env_file, allow_test=args.allow_test, resume=args.resume)
    else:
        value = analyze_trials(args.inputs, args.gold, args.run, args.reviews)
        write_new(args.out, value)
        print(json.dumps(value["summaries"], ensure_ascii=False))


if __name__ == "__main__":
    main()
