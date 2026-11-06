"""Production report Runtime evaluation and gold-blind joint-evidence review."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time

from ..local_rag import LocalHybridRag
from ..providers import ChatModel
from ..retrieval import GrepSearch
from ..runtime import Runtime
from ..schemas import Report, RunCreate, SourceCreate
from ..store import Store
from ..tools import ToolBus
from .measurement import MeasuredModel
from .trials import digest, frozen_inputs, load, validate_plan, write_new, task_clusters, cluster_interval


UNKNOWN = {"", "本次未核实", "unknown", "not verified", "not reported", "n/a"}


async def run_report(task, documents, settings, method, directory, embedder, max_tokens, max_steps, max_seconds, model=None):
    """Call the same Runtime used by the workbench, with isolated canonical sources."""
    started = time.monotonic()
    owner, store = "report-eval", Store(directory / "state")
    store.initialize()
    wid = store.create_workspace(owner, dict(title=task["id"]))["id"]
    mapping = {}
    for sid in task["source_ids"]:
        document = documents[sid]
        blocks, current, size = [], [], 0
        for line in document["text"].splitlines():
            if current and size + len(line) + 1 > 40000:
                blocks.append(dict(text="\n".join(current)))
                current, size = [], 0
            current.append(line)
            size += len(line) + 1
        if current:
            blocks.append(dict(text="\n".join(current)))
        source = store.add_source(owner, wid, SourceCreate(title=document["title"], kind="paper", blocks=blocks))
        if store.source_text(owner, wid, source["id"]) != document["text"]:
            raise ValueError("Canonical report source changed during import")
        mapping[sid] = source["id"]
    strategy = method.removeprefix("report-")
    rag = LocalHybridRag(settings, store, embedder=embedder)
    if strategy == "rag":
        for sid in mapping.values():
            await asyncio.to_thread(rag.index, owner, wid, sid)
    session = store.put(owner, wid, "session", dict(title="report evaluation"))
    fields = [dict(source_id=mapping[t["source_id"]], field=t["field"]) for t in task["targets"]]
    question = task["question"] + "\nRequired paper IDs and fields: " + json.dumps(fields, ensure_ascii=False)
    body = RunCreate(session_id=session["id"], question=question, retrieval_strategy=strategy,
                     max_tokens=max_tokens, max_steps=max_steps, max_seconds=max_seconds)
    run = store.put(owner, wid, "run", dict(**body.model_dump(), status="queued", model=settings.llm_model,
        tokens_used=0, tokens_estimated=False, step=0, report=None, error=None))
    store.put(owner, wid, "message", dict(role="user", content=question, run_id=run["id"]), parent_id=session["id"])
    measured = MeasuredModel(model or ChatModel(settings), max_tokens)
    runtime = Runtime(store, ToolBus(store, GrepSearch(store, settings.rg_binary), rag, None), measured)
    await runtime.execute(owner, wid, run["id"])
    final = store.get(owner, wid, "run", run["id"])
    calls = store.list(owner, wid, "tool_call", parent_id=run["id"], limit=2000)
    reverse = {value:key for key,value in mapping.items()}
    reads = {}
    for call in calls:
        if call["name"] == "sources_read" and call["status"] == "succeeded":
            evidence = call["data"]
            reads[evidence["id"]] = {k:evidence[k] for k in ("id", "start_line", "end_line", "quote")} | dict(source_id=reverse[evidence["source_id"]])
    report = final.get("report")
    if report:
        for row in report["comparison_rows"]:
            row["source_id"] = reverse[row["source_id"]]
    events, cursor = [], 0
    while batch := store.events(owner, wid, run["id"], cursor):
        events.extend(batch)
        cursor = batch[-1]["id"]
    result = dict(task_id=task["id"], method=method, status=final["status"], error=final.get("error"),
        report=report, reads=list(reads.values()), runtime="research_backend.runtime.Runtime",
        tokens_used=measured.tokens, known_tokens_used=measured.known_tokens,
        model_calls=len(measured.calls), elapsed_seconds=round(time.monotonic()-started, 3))
    (directory / "trace.json").write_text(json.dumps(dict(model_calls=measured.calls, events=events), ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "prediction.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def units(report, documents):
    """Claims plus individual comparison cells; never silently ignore table facts."""
    result = []
    for i, claim in enumerate(report["claims"]):
        text = claim["text"] + ("\nScope: " + claim["qualification"] if claim["qualification"] else "")
        result.append(dict(unit_id=f"claim-{i}", claim=text, evidence_ids=claim["evidence_ids"]))
    for i, row in enumerate(report["comparison_rows"]):
        values = {k:row[k] for k in ("method", "dataset", "model", "metric", "result", "conditions")}
        values.update({"attribute:"+key:value for key,value in row["attributes"].items()})
        for field, value in values.items():
            if value.strip().lower() in UNKNOWN:
                continue
            result.append(dict(unit_id=f"row-{i}-{field}",
                claim=f"Paper {documents[row['source_id']]['title']}; {field}: {value}",
                evidence_ids=row["evidence_ids"]))
    return result


def inspect_prediction(prediction, task, documents, budget):
    reads = prediction.get("reads", [])
    valid, errors = {}, []
    if len({e["id"] for e in reads}) != len(reads):
        raise ValueError("Duplicate report read evidence ID")
    for evidence in reads:
        source = documents.get(evidence.get("source_id"))
        start, end = evidence["start_line"], evidence["end_line"]
        if (source and evidence["source_id"] in task["source_ids"] and type(start) is int and type(end) is int
                and 1 <= start <= end <= len(source["text"].splitlines())
                and "\n".join(source["text"].splitlines()[start-1:end]) == evidence["quote"]):
            valid[evidence["id"]] = evidence
        else:
            errors.append("noncanonical_read")
    report = None
    try:
        report = Report.model_validate(prediction.get("report")).model_dump()
        if any(row["source_id"] not in task["source_ids"] for row in report["comparison_rows"]):
            raise ValueError("Unexpected comparison source")
    except (ValueError, TypeError):
        errors.append("invalid_or_missing_report")
        report = None
    extracted = units(report, documents) if report else []
    if not extracted:
        errors.append("no_verifiable_units")
    for unit in extracted:
        if not unit["evidence_ids"] or any(eid not in valid for eid in unit["evidence_ids"]):
            errors.append("invalid_or_unread_citation")
    for row in (report or {}).get("comparison_rows", []):
        if not any(eid in valid and valid[eid]["source_id"] == row["source_id"] for eid in row["evidence_ids"]):
            errors.append("comparison_source_mismatch")
    covered = []
    for target in task["targets"]:
        value_present = False
        for row in (report or {}).get("comparison_rows", []):
            if row["source_id"] != target["source_id"]:
                continue
            value = row.get("attributes", {}).get(target["field"], row.get(target["field"], ""))
            value_present |= isinstance(value,str) and value.strip().lower() not in UNKNOWN
        covered.append(bool(value_present))
    cited_sources = {valid[eid]["source_id"] for unit in extracted for eid in unit["evidence_ids"] if eid in valid}
    complete = prediction["status"] == "completed"
    tokens = prediction.get("tokens_used")
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        raise ValueError("Invalid report token usage")
    if tokens is not None and tokens > budget:
        errors.append("token_budget_exceeded")
    known = prediction.get("known_tokens_used",tokens)
    if known is not None and (type(known) is not int or known<0 or tokens is not None and known>tokens):
        raise ValueError("Invalid known report usage")
    return dict(status=prediction["status"], contract_pass=complete and not errors,
        field_presence_rate=sum(covered)/len(covered),
        cited_source_coverage=len(cited_sources & set(task["source_ids"]))/len(task["source_ids"]),
        errors=sorted(set(errors)), units=extracted, valid_reads=valid,
        semantic_success=None, tokens_used=tokens, known_tokens_used=known)


def report_records(inputs_path, root):
    root = Path(root)
    plan = load(root / "plan.json")
    inputs = validate_plan(plan, inputs_path)
    if plan["scope"] != "production_report_runtime_repeats" or digest(root / "plan.json") != load(root / "study.json")["plan_sha256"]:
        raise ValueError("Invalid report study plan")
    documents = {d["id"]:d for d in inputs["documents"]}
    tasks = {t["id"]:t for t in inputs["tasks"]}
    records, previous_config = [], None
    for repeat in range(plan["repeats"]):
        trial = root / f"trial-{repeat:03d}"
        predictions = []
        if (trial / "protocol.json").exists():
            protocol = load(trial / "protocol.json")
            for key in ("dataset", "split", "inputs_sha256", "model", "extra_body", "max_tokens", "max_steps", "methods", "task_ids"):
                if protocol.get(key) != plan[key]:
                    raise ValueError("Report trial protocol drift: " + key)
            if {k.replace("\\","/"):v for k,v in protocol["code_sha256"].items()} != plan["code_sha256"]:
                raise ValueError("Report trial code drift")
            config = {k:protocol.get(k) for k in ("embedding_model","retrieval")}
            if previous_config is not None and config!=previous_config:
                raise ValueError("Report runtime configuration drift")
            previous_config = config
            if (trial / "predictions.jsonl").exists():
                predictions = [json.loads(line) for line in (trial / "predictions.jsonl").read_text("utf-8").splitlines() if line.strip()]
        elif (trial / "predictions.jsonl").exists():
            raise ValueError("Report predictions have no protocol")
        keyed = {(p["method"],p["task_id"]):p for p in predictions}
        if len(keyed) != len(predictions):
            raise ValueError("Duplicate report prediction")
        if any(m not in plan["methods"] or tid not in plan["task_ids"] for m,tid in keyed):
            raise ValueError("Unexpected report prediction")
        for method in plan["methods"]:
            for tid in plan["task_ids"]:
                prediction = keyed.get((method,tid),dict(status="missing", reads=[], report=None))
                records.append(dict(inspect_prediction(prediction,tasks[tid],documents,plan["max_tokens"]),
                    repeat=repeat, method=method, task_id=tid))
    return plan, inputs, records


def export_cases(inputs_path, root):
    plan, inputs, records = report_records(inputs_path, root)
    tasks = {t["id"]:t for t in inputs["tasks"]}
    cases = []
    for record in records:
        for unit in record["units"]:
            evidence = [record["valid_reads"][eid] for eid in dict.fromkeys(unit["evidence_ids"]) if eid in record["valid_reads"]]
            case = dict(task_id=record["task_id"], repeat=record["repeat"], method=record["method"],
                unit_id=unit["unit_id"], question=tasks[record["task_id"]]["question"], claim=unit["claim"],
                source_ids=tasks[record["task_id"]]["source_ids"], evidence=evidence,
                citation_contract_valid=bool(unit["evidence_ids"]) and len(evidence)==len(set(unit["evidence_ids"])))
            case["id"] = hashlib.sha256(json.dumps(case,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            cases.append(case)
    return dict(format=1, purpose="joint-evidence review candidates; labels are not provided to a model",
                plan_sha256=digest(Path(root)/"plan.json"), cases=cases)


def analyze_reports(inputs_path, gold_path, root, reviews_path=None):
    plan, inputs, records = report_records(inputs_path, root)
    if digest(gold_path) != load(Path(inputs_path).parent/"manifest.json")["gold_sha256"]:
        raise ValueError("Report gold hash mismatch")
    bundle = export_cases(inputs_path,root)
    reviews = {}
    if reviews_path:
        from .claim_labels import validated_reviews
        reviews = validated_reviews(bundle, reviews_path)
    summaries, means = {}, {}
    for method in plan["methods"]:
        rows = [r for r in records if r["method"]==method]
        task_groups = {tid:[r for r in rows if r["task_id"]==tid] for tid in plan["task_ids"]}
        means[method] = {tid:sum(r["field_presence_rate"] if r["status"]=="completed" else 0 for r in group)/plan["repeats"] for tid,group in task_groups.items()}
        reliability, n = {}, plan["repeats"]
        for k in range(1,n+1):
            counts = [sum(r["contract_pass"] for r in group) for group in task_groups.values()]
            reliability[str(k)] = dict(pass_at_k=sum(1-math.comb(n-c,k)/math.comb(n,k) for c in counts)/len(counts),
                pass_all_k=sum(math.comb(c,k)/math.comb(n,k) for c in counts)/len(counts))
        cases = [c for c in bundle["cases"] if c["method"]==method]
        reviewed = [reviews[c["id"]] for c in cases if c["id"] in reviews and reviews[c["id"]].get("status")=="completed"]
        complete_cost = all(r["tokens_used"] is not None for r in rows)
        summaries[method] = dict(planned_attempts=len(rows), completed=sum(r["status"]=="completed" for r in rows),
            contract_pass_rate=sum(r["contract_pass"] for r in rows)/len(rows), contract_reliability=reliability,
            field_presence_rate=sum(means[method].values())/len(task_groups),
            cited_source_coverage=sum(r["cited_source_coverage"] if r["status"]=="completed" else 0 for r in rows)/len(rows),
            mean_tokens=sum(r["tokens_used"] for r in rows)/len(rows) if complete_cost else None,
            total_known_tokens=sum(r["known_tokens_used"] or 0 for r in rows), unknown_token_attempts=sum(r["tokens_used"] is None for r in rows),
            review_units=len(cases), reviewed_units=len(reviewed),
            candidate_supported_rate=sum(r["review"]["verdict"]=="supported" for r in reviewed)/len(cases) if reviews_path and cases else None,
            candidate_verdict_counts=dict(Counter(r["review"]["verdict"] for r in reviewed)), semantic_success_rate=None)
    selected = [t for t in inputs["tasks"] if t["id"] in plan["task_ids"]]
    baseline = means[plan["reference"]]
    artifacts = {f"trial-{repeat:03d}":{name:digest(Path(root)/f"trial-{repeat:03d}"/name)
        if (Path(root)/f"trial-{repeat:03d}"/name).exists() else None for name in ("protocol.json","predictions.jsonl")}
        for repeat in range(plan["repeats"])}
    return dict(scope=plan["scope"],plan_sha256=bundle["plan_sha256"], gold_sha256=digest(gold_path),
        scorer_code_sha256=digest(__file__),trial_artifacts_sha256=artifacts,
        reviews_sha256=digest(reviews_path) if reviews_path else None,summaries=summaries,records=records,
        paired_field_presence={m:cluster_interval({tid:v-baseline[tid] for tid,v in group.items()},task_clusters(selected),plan["analysis_seed"]) for m,group in means.items() if m!=plan["reference"]},
        limitations=["Contract, source coverage and field presence are not semantic correctness or answer adequacy.",
                     "Candidate verdicts require independent human calibration; semantic success remains null.",
                     "All planned failures and missing attempts remain in task denominators; no judge costs are included in task tokens."])


def export_annotation_bundle(bundle, directory):
    """Keep method metadata with the coordinator; distribute only the human view."""
    from .claim_labels import checked_cases
    cases = checked_cases(bundle)
    # Case IDs from export_cases are opaque hashes. Sorting removes method/trial
    # blocks without changing claims, evidence or the scorer's original bundle.
    human_cases = [dict(id=c["id"], question=c["question"], claim=c["claim"],
        evidence=[{key:e[key] for key in ("id", "source_id", "start_line", "end_line", "quote")}
                  for e in c["evidence"]]) for c in sorted(cases, key=lambda c:c["id"])]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    write_new(directory/"cases.json", bundle)
    write_new(directory/"human-cases.json", dict(format=1,
        purpose="human evidence review; method, run metadata and candidate labels withheld", cases=human_cases))
    for name in ("annotator-a", "annotator-b", "adjudication"):
        write_new(directory/(name+".json"), dict(annotation_source="human", annotator=name,
            cases_sha256=digest(directory/"cases.json"),
            labels=[dict(id=c["id"], verdict=None, rationale=None, evidence_spans=[]) for c in human_cases]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    export = sub.add_parser("export")
    export.add_argument("--inputs",type=Path,required=True)
    export.add_argument("--run",type=Path,required=True)
    export.add_argument("--out",type=Path,required=True)
    args = parser.parse_args()
    bundle = export_cases(args.inputs,args.run)
    export_annotation_bundle(bundle, args.out)
    print(json.dumps(dict(cases=len(bundle["cases"]), status="exported_unannotated_human_packets",
        distribute="human-cases.json and the assigned annotator file; keep cases.json with coordinator")))


if __name__ == "__main__":
    main()
