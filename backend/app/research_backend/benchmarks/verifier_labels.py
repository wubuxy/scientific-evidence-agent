"""Frozen, exploratory raw-label versus published-proposal LoRA ablation.

Preparation/scoring may read gold; GPU generation only reads visible inputs and
training labels. Existing v2 training implementation and hyperparameters remain
unchanged. Published proposals are not described as human-adjudicated truth.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gc
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import statistics
import time
import weakref

from . import verifier_pilot as v1
from . import verifier_scale as v2
from .trials import load, write_new


CONFIG = dict(seeds=[20261106, 20261107, 20261108], steps=128, accumulation=4,
              precision="bf16", bootstrap_draws=5000, bootstrap_seed=20261109,
              calibration_max_fpr=.05, wall_seconds=10800)
METHODS = ["raw-20261106", "proposed-20261106", "proposed-20261107",
           "raw-20261107", "raw-20261108", "proposed-20261108"]
REFERENCES = ["large-bf16", "minicheck-bf16"]
SPLITS = ["calibration", "test", "challenge", "empty-evidence"]
LABEL_MAP = {"SUPPORT": "supported", "CONTRADICT": "contradicted", "NEI": "insufficient"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def code_files():
    root = Path(__file__).resolve().parents[4]
    names = ["backend/app/research_backend/benchmarks/verifier_labels.py",
             "backend/app/research_backend/benchmarks/verifier_scale.py",
             "backend/app/research_backend/benchmarks/verifier_pilot.py",
             "backend/app/research_backend/benchmarks/trials.py",
             "docs/experiments/SCIFACT_LABEL_ABLATION_PILOT.md"]
    return {name: v2.sha(root / name) for name in names}


def apply_proposals(rows, corrections):
    changed, output, unresolved = [], [], []
    for row in rows:
        _, claim_id, paper_id = row["id"].rsplit("-", 2)
        updated = row
        for correction in corrections:
            if int(claim_id) != correction["claim_id"]:
                continue
            document_id = correction.get("doc_id")
            if correction["error_type"] == "per_document_outcome_mismatch" and document_id is None:
                unresolved.append(row["id"])
                continue
            if document_id is not None and str(document_id) != paper_id:
                continue
            if row["label"] != LABEL_MAP[correction["original_label"]]:
                continue  # Do not change an unannotated cited abstract.
            if updated is not row:
                raise ValueError("Duplicate correction for " + row["id"])
            updated = dict(row, label=LABEL_MAP[correction["corrected_label"]])
            changed.append(dict(id=row["id"], original=row["label"], proposed=updated["label"],
                                error_type=correction["error_type"]))
        output.append(updated)
    return output, changed, unresolved


def assert_label_only(original, proposed):
    if len(original) != len(proposed):
        raise ValueError("Training population changed")
    for a, b in zip(original, proposed):
        if {k: v for k, v in a.items() if k != "label"} != {k: v for k, v in b.items() if k != "label"}:
            raise ValueError("Only training labels may differ")


def prepare(old_data, old_results, corrections, out):
    old_data, old_results, corrections, out = map(Path, (old_data, old_results, corrections, out))
    original_plan = load(old_data / "plan.json")
    frozen = load(old_results / "frozen-manifest.json")
    if v2.sha(old_data / "plan.json") != frozen["plan_sha256"]:
        raise ValueError("Wrong frozen v2 data")
    provenance = load(corrections / "provenance.json")
    for name, meta in provenance["files"].items():
        if v2.sha(corrections / name) != meta["sha256"]:
            raise ValueError("External proposal changed")
    all_rows = {}
    for split in ["calibration", "test", "challenge"]:
        for suffix in ["inputs", "gold"]:
            name = split + "-" + suffix + ".json"
            if v2.sha(old_data / name) != original_plan["files"][name]:
                raise ValueError("Old data changed: " + name)
        inputs = load(old_data / (split + "-inputs.json"))["cases"]
        gold = load(old_data / (split + "-gold.json"))["labels"]
        if len(inputs) != len({r["id"] for r in inputs}) or {r["id"] for r in inputs} != set(gold):
            raise ValueError("Input/gold IDs differ")
        all_rows[split] = [dict(r, label=gold[r["id"]]) for r in inputs]
    if v2.sha(old_data / "training.json") != original_plan["files"]["training.json"]:
        raise ValueError("Old training changed")
    training = load(old_data / "training.json")["cases"]
    train_corrections = load(corrections / "train_corrections.json")["corrections"]
    dev_corrections = load(corrections / "dev_corrections.json")["corrections"]
    proposed, changes, _ = apply_proposals(training, train_corrections)
    assert_label_only(training, proposed)
    if len(changes) != 7:
        raise ValueError("Expected exactly seven published training proposals")
    all_rows["empty-evidence"] = [dict(r, evidence=[], label="insufficient") for r in all_rows["test"][:32]]
    originals = {s: {r["id"]: r["label"] for r in rows} for s, rows in all_rows.items()}
    revised = {s: dict(labels) for s, labels in originals.items()}
    overlays = {"train": changes}
    for split, proposals in [("calibration", train_corrections), ("test", dev_corrections)]:
        rows, changed, unresolved = apply_proposals(all_rows[split], proposals)
        revised[split] = {r["id"]: r["label"] for r in rows}
        overlays[split] = changed
        if split == "test":
            overlays["unresolved_test_doc_ids"] = unresolved
    if (len(overlays["calibration"]), len(overlays["test"])) != (1, 6):
        raise ValueError("Unexpected calibration/test proposal population")
    scoped = {s: dict(labels) for s, labels in revised.items()}
    scoped["test"]["official-dev-597-12779444"] = "insufficient"
    if set(scoped["test"]) != set(originals["test"]):
        raise ValueError("Document-specific sensitivity must not add a case")
    old_analysis = load(old_results / "analysis.json")
    if v2.sha(old_results / "predictions.jsonl") != old_analysis["predictions_sha256"]:
        raise ValueError("Reference predictions changed")
    reference_rows = [json.loads(line) for line in (old_results / "predictions.jsonl").read_text().splitlines()
                      if json.loads(line)["method"] in REFERENCES]
    reference_index = defaultdict(dict)
    for row in reference_rows:
        key = (row["method"], row["split"])
        if row["id"] in reference_index[key] or row["status"] != "completed":
            raise ValueError("Duplicate or failed reference")
        reference_index[key][row["id"]] = row
    for m in REFERENCES:
        for s in SPLITS:
            if set(reference_index[m, s]) != set(originals[s]):
                raise ValueError("Reference inputs differ")
    model_dir = original_plan["model_dirs"]["large"]
    if v2.model_files(model_dir) != original_plan["model_sha256"]["large"]:
        raise ValueError("Frozen 7B weights changed")
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / "training-raw.json", dict(cases=training))
    write_new(out / "training-proposed.json", dict(cases=proposed))
    for s, rows in all_rows.items():
        write_new(out / (s + "-inputs.json"), dict(cases=[{k: v for k, v in r.items() if k != "label"} for r in rows]))
    for name, labels in [("original", originals), ("published", revised), ("doc597", scoped)]:
        write_new(out / ("labels-" + name + ".json"), labels)
    write_new(out / "correction-overlay.json", dict(status="published proposals, independent human adjudication pending", changes=overlays))
    write_new(out / "reference-predictions.json", dict(source_sha256=v2.sha(old_results / "predictions.jsonl"), records=reference_rows))
    schedules = {}
    changed_ids = {r["id"] for r in changes}
    for seed in CONFIG["seeds"]:
        order = list(range(len(training)))
        random.Random(seed).shuffle(order)
        ids = [training[i]["id"] for i in order[:CONFIG["steps"] * CONFIG["accumulation"]]]
        schedules[str(seed)] = dict(ids=ids, changed_labels_seen=[i for i in ids if i in changed_ids])
    write_new(out / "training-order.json", schedules)
    plan = dict(format=1, config=CONFIG, inherited_training_config=v2.CONFIG, methods=METHODS,
                references=REFERENCES, model_dirs={"large": model_dir},
                model_sha256=original_plan["model_sha256"]["large"],
                code_sha256=code_files(), original_plan_sha256=frozen["plan_sha256"],
                original_predictions_sha256=old_analysis["predictions_sha256"],
                external_provenance=provenance,
                counts={s: len(rows) for s, rows in all_rows.items()}, training_cases=len(training),
                files={p.name: v2.sha(p) for p in sorted(out.glob("*.json"))},
                scope="Observed SciFact v2 development data; exploratory label intervention, no new human gold")
    write_new(out / "plan.json", plan)
    return dict(plan_sha256=v2.sha(out / "plan.json"), counts=plan["counts"],
                proposed_training_labels=len(changes), actual_training_exposure={k: len(v["changed_labels_seen"]) for k, v in schedules.items()})


def check_plan(directory, scoring=False, verify_weights=True):
    directory = Path(directory)
    plan = load(directory / "plan.json")
    if plan["config"] != CONFIG or plan["inherited_training_config"] != v2.CONFIG or plan["code_sha256"] != code_files():
        raise ValueError("Code or configuration changed after freeze")
    for name, expected in plan["files"].items():
        if name.startswith("labels-") and not scoring:
            continue
        if v2.sha(directory / name) != expected:
            raise ValueError("Frozen file changed: " + name)
    if verify_weights and v2.model_files(plan["model_dirs"]["large"]) != plan["model_sha256"]:
        raise ValueError("Model weights changed")
    assert_label_only(load(directory / "training-raw.json")["cases"], load(directory / "training-proposed.json")["cases"])
    return plan


def infer(model, tokenizer, rows, method, split, output, deadline):
    import torch
    model.eval()
    tokens = [tokenizer.encode(letter, add_special_tokens=False) for letter in v1.LETTERS]
    if any(len(t) != 1 for t in tokens) or len({t[0] for t in tokens}) != 3:
        raise ValueError("Expected distinct single-token labels")
    for row in rows:
        if time.monotonic() > deadline:
            raise TimeoutError("Frozen wall time exceeded")
        torch.cuda.synchronize()
        started = time.monotonic()
        with torch.inference_mode():
            batch = v2.encode_batch(tokenizer, row)
            logits = v1.last_logits(model, batch).float()[0]
            if not torch.isfinite(logits).all():
                raise ValueError("Nonfinite logits")
            choice = logits[[t[0] for t in tokens]]
            probs = choice.softmax(-1)
            record = dict(id=row["id"], method=method, split=split, status="completed",
                          verdict=v1.LABELS[int(choice.argmax())], support_score=float(probs[0]),
                          choice_logits=choice.tolist(), choice_probabilities=probs.tolist(),
                          choice_mass=float(logits.softmax(-1)[[t[0] for t in tokens]].sum()),
                          input_tokens=int(batch["input_ids"].shape[1]))
        torch.cuda.synchronize()
        record["seconds"] = time.monotonic() - started
        output.write(json.dumps(record) + "\n")
        output.flush()


def condition(plan, directory, out, method, data, output, deadline):
    import torch
    from peft import PeftModel
    arm, seed = method.split("-")
    seed = int(seed)
    training = load(directory / ("training-" + arm + ".json"))["cases"]
    order = list(range(len(training)))
    random.Random(seed).shuffle(order)
    actual = [training[i]["id"] for i in order[:CONFIG["steps"] * CONFIG["accumulation"]]]
    if actual != load(directory / "training-order.json")[str(seed)]["ids"]:
        raise ValueError("Training order drift")
    model = base = None
    started = time.monotonic()
    try:
        tokenizer = v2.tokenizer_for(plan["model_dirs"]["large"])
        model = v2.load_model(plan, "large", "bf16")
        model = v2.train(model, tokenizer, training, seed, "bf16", out / method, deadline)
        previous = weakref.ref(model)
        model = None
        gc.collect()
        torch.cuda.empty_cache()
        if previous() is not None:
            raise RuntimeError("Training model retained before reload")
        base = v2.load_model(plan, "large", "bf16")
        model = PeftModel.from_pretrained(base, out / method, is_trainable=False)
        for split, rows in data.items():
            infer(model, tokenizer, rows, method, split, output, deadline)
        return dict(method=method, completed=True, seconds=time.monotonic() - started)
    finally:
        model = base = None
        gc.collect()
        torch.cuda.empty_cache()


def run(directory, out):
    import torch
    directory, out = Path(directory), Path(out)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    torch.set_num_threads(4)
    plan = check_plan(directory)
    out.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + CONFIG["wall_seconds"]
    write_new(out / "protocol.json", dict(plan_sha256=v2.sha(directory / "plan.json"), methods=METHODS,
        started_at=timestamp(), gpu=torch.cuda.get_device_name(), cuda=torch.version.cuda,
        packages={p: importlib.metadata.version(p) for p in ["torch", "transformers", "peft", "bitsandbytes"]},
        references="Archived v2 baseline predictions; no new baseline GPU runs"))
    data = {s: load(directory / (s + "-inputs.json"))["cases"] for s in SPLITS}
    records = []
    with (out / "predictions.jsonl").open("x", encoding="utf-8") as output:
        for method in METHODS:
            print(json.dumps(dict(method=method, event="started", time=timestamp())), flush=True)
            try:
                record = condition(plan, directory, out, method, data, output, deadline)
            except Exception as error:
                record = dict(method=method, completed=False, error=type(error).__name__, message=str(error)[:1000])
            records.append(record)
            print(json.dumps(record), flush=True)
    summary = dict(complete=all(r["completed"] for r in records), conditions=records, finished_at=timestamp())
    write_new(out / "run-summary.json", summary)
    return summary


def index_records(records, methods, gold):
    indexed = {m: {s: {} for s in SPLITS} for m in methods}
    for record in records:
        m, s, identity = record["method"], record["split"], record["id"]
        if m not in indexed or s not in gold or identity not in gold[s]:
            raise ValueError("Unexpected prediction identity")
        if identity in indexed[m][s]:
            raise ValueError("Duplicate prediction")
        if record.get("status") == "completed":
            allowed = ("supported", "unsupported") if m == "minicheck-bf16" else v1.LABELS
            if record["verdict"] not in allowed or not math.isfinite(record["support_score"]) or not 0 <= record["support_score"] <= 1:
                raise ValueError("Invalid prediction")
        indexed[m][s][identity] = record
    return indexed


def select_threshold(gold, records):
    if set(gold) != set(records) or any(r.get("status") != "completed" for r in records.values()):
        return None
    positive = sum(v == "supported" for v in gold.values())
    negative = len(gold) - positive
    if not positive or not negative:
        return None
    candidates = {0.0, math.nextafter(1.0, math.inf), *(r["support_score"] for r in records.values())}
    feasible = []
    for threshold in candidates:
        accepted = [i for i, r in records.items() if r["support_score"] >= threshold]
        tp = sum(gold[i] == "supported" for i in accepted)
        fp = len(accepted) - tp
        if fp / negative <= CONFIG["calibration_max_fpr"]:
            feasible.append((tp, -fp, threshold))
    return max(feasible)[2]


def threshold_records(records, threshold):
    return {i: dict(r, verdict="supported" if r["support_score"] >= threshold else "unsupported")
            for i, r in records.items() if r.get("status") == "completed"}


def binary_metrics(gold, records):
    completed = {i: r for i, r in records.items() if r.get("status") == "completed"}
    value = v2.binary_metrics(gold, completed)
    value["acceptance_rate"] = sum(r["verdict"] == "supported" for r in completed.values()) / len(gold)
    return value


def sample_metrics(truth, guesses):
    pairs = Counter(zip(truth, guesses))
    totals = Counter(truth)
    predicted = Counter(guesses)
    f1 = []
    for label in v1.LABELS:
        denom = totals[label] + predicted[label]
        f1.append(2 * pairs[label, label] / denom if denom else 0)
    negatives = len(truth) - totals["supported"]
    return dict(macro_f1=statistics.mean(f1),
        false_support_rate=sum(pairs[l, "supported"] for l in v1.LABELS if l != "supported") / negatives if negatives else None,
        support_recall=pairs["supported", "supported"] / totals["supported"] if totals["supported"] else None)


def paired_delta(ids, gold, indexed):
    result = {}
    for arm in ["raw", "proposed"]:
        rows = [sample_metrics([gold[i] for i in ids],
                    [r.get("verdict") if r.get("status") == "completed" else None
                     for i in ids for r in [indexed[f"{arm}-{seed}"]["test"].get(i, {})]]) for seed in CONFIG["seeds"]]
        result[arm] = {key: statistics.mean(r[key] for r in rows) if all(r[key] is not None for r in rows) else None for key in rows[0]}
    return {key: result["proposed"][key] - result["raw"][key] if result["proposed"][key] is not None and result["raw"][key] is not None else None for key in result["raw"]}


def percentile(values, q):
    values = sorted(values)
    location = (len(values) - 1) * q
    low, high = math.floor(location), math.ceil(location)
    return values[low] + (values[high] - values[low]) * (location - low)


def score(directory, run_dir):
    directory, run_dir = Path(directory), Path(run_dir)
    plan = check_plan(directory, scoring=True, verify_weights=False)
    protocol = load(run_dir / "protocol.json")
    if protocol["plan_sha256"] != v2.sha(directory / "plan.json") or protocol["methods"] != METHODS:
        raise ValueError("Run does not match frozen plan")
    views = {name: load(directory / ("labels-" + name + ".json")) for name in ["original", "published", "doc597"]}
    records = [json.loads(line) for line in (run_dir / "predictions.jsonl").read_text().splitlines()]
    references = load(directory / "reference-predictions.json")["records"]
    index_records(records, METHODS, views["original"])
    index_records(references, REFERENCES, views["original"])
    indexed = index_records(records + references, METHODS + REFERENCES, views["original"])
    summary = load(run_dir / "run-summary.json")
    if [r["method"] for r in summary["conditions"]] != METHODS:
        raise ValueError("Run summary does not cover the frozen conditions")
    complete = summary["complete"] and all(r["completed"] for r in summary["conditions"]) and all(set(indexed[m][s]) == set(views["original"][s]) and all(r.get("status") == "completed" for r in indexed[m][s].values()) for m in METHODS for s in SPLITS)
    results = {}
    for view, gold in views.items():
        results[view] = {}
        for method in METHODS + REFERENCES:
            row = {}
            threshold = select_threshold(gold["calibration"], indexed[method]["calibration"])
            for split in SPLITS:
                p = indexed[method][split]
                value = dict(binary=binary_metrics(gold[split], p))
                if method != "minicheck-bf16":
                    value["three_class"] = v1.classification_metrics(gold[split], {i: r.get("verdict") for i, r in p.items() if r.get("status") == "completed"})
                if threshold is not None:
                    value["threshold_binary"] = binary_metrics(gold[split], threshold_records(p, threshold))
                row[split] = value
            row["support_threshold"] = threshold
            results[view][method] = row
    test_rows = load(directory / "test-inputs.json")["cases"]
    ids = [r["id"] for r in test_rows]
    groups = [[r["id"] for r in group] for group in v1.connected_groups(test_rows)]
    contrasts = {view: dict(delta=paired_delta(ids, gold["test"], indexed)) for view, gold in views.items()}
    if complete:
        draws = {view: defaultdict(list) for view in ["original", "published"]}
        rng = random.Random(CONFIG["bootstrap_seed"])
        for _ in range(CONFIG["bootstrap_draws"]):
            selected = [i for group in rng.choices(groups, k=len(groups)) for i in group]
            for view in draws:
                for metric, value in paired_delta(selected, views[view]["test"], indexed).items():
                    if value is not None:
                        draws[view][metric].append(value)
        for view in draws:
            contrasts[view]["intervals"] = {metric: dict(ci95=[percentile(values, .025), percentile(values, .975)],
                simultaneous_ci9875=[percentile(values, .00625), percentile(values, .99375)] if metric != "support_recall" else None,
                valid_draws=len(values)) for metric, values in draws[view].items()}
    return dict(scope=plan["scope"], complete=complete, results=results, paired_contrasts=contrasts,
        bootstrap_groups=len(groups), bootstrap_draws=CONFIG["bootstrap_draws"] if complete else 0,
        planned_new_predictions=len(METHODS) * sum(plan["counts"].values()), actual_new_predictions=len(records),
        archived_reference_predictions=len(references), plan_sha256=v2.sha(directory / "plan.json"),
        predictions_sha256=v2.sha(run_dir / "predictions.jsonl"),
        limitations=["Published correction proposals are not new independently adjudicated gold.",
                     "Observed development data; intervals are exploratory and conditional on the three trained seeds.",
                     "Four primary contrasts: two metrics across original/published gold; Bonferroni 98.75% intervals.",
                     "No production adoption claim or complete-Agent effect is tested."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    for flag in ["old-data", "old-results", "corrections", "out"]:
        prepare_parser.add_argument("--" + flag, type=Path, required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--plan-dir", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    score_parser = sub.add_parser("score")
    score_parser.add_argument("--plan-dir", type=Path, required=True)
    score_parser.add_argument("--run-dir", type=Path, required=True)
    score_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.old_data, args.old_results, args.corrections, args.out)
    elif args.command == "run":
        result = run(args.plan_dir, args.out)
    else:
        result = score(args.plan_dir, args.run_dir)
        write_new(args.out, result)
        result = dict(complete=result["complete"], paired_contrasts=result["paired_contrasts"], output=str(args.out))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
