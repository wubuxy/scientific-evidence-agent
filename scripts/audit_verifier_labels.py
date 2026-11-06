"""Audit the published label ablation without models, network or ML packages.

Recompute all point metrics and calibration decisions. --bootstrap independently
recomputes the paired group intervals, retaining multiplicity in resamples.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

from audit_verifier_scale import close, macro_f1


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binary(gold, records):
    positive = [i for i, g in gold.items() if g == "supported"]
    negative = [i for i, g in gold.items() if g != "supported"]
    done = {i: r for i, r in records.items() if r["status"] == "completed"}
    accepted = {i for i, r in done.items() if r["verdict"] == "supported"}
    tp, fp = len(accepted & set(positive)), len(accepted & set(negative))
    tn = sum(i in done and done[i]["verdict"] != "supported" for i in negative)
    auc = None
    if len(done) == len(gold) and positive and negative:
        auc = sum((done[p]["support_score"] > done[n]["support_score"]) +
                  .5 * (done[p]["support_score"] == done[n]["support_score"])
                  for p in positive for n in negative) / (len(positive) * len(negative))
    return dict(cases=len(gold), accuracy=(tp + tn) / len(gold),
                supported_precision=tp / len(accepted) if accepted else None,
                supported_recall=tp / len(positive) if positive else None,
                unsupported_false_supported_rate=fp / len(negative) if negative else None,
                balanced_accuracy=(tp / len(positive) + tn / len(negative)) / 2 if positive and negative else None,
                auroc=auc, missing_predictions=len(gold) - len(done), acceptance_rate=len(accepted) / len(gold))


def threshold(gold, records):
    if len(records) != len(gold) or any(r["status"] != "completed" for r in records.values()):
        return None
    negative = [i for i in gold if gold[i] != "supported"]
    if not negative or len(negative) == len(gold):
        return None
    candidates = sorted({0., math.nextafter(1., math.inf)} | {r["support_score"] for r in records.values()})
    best = None
    for cut in candidates:
        accepted = [i for i in gold if records[i]["support_score"] >= cut]
        true = sum(gold[i] == "supported" for i in accepted)
        false = len(accepted) - true
        if false / len(negative) <= .05:
            value = (true, -false, cut)
            if best is None or value > best:
                best = value
    return best[-1]


def contrast(ids, gold, indexed, seeds):
    scores = {}
    truth = [gold[i] for i in ids]
    positive = sum(g == "supported" for g in truth)
    negative = len(truth) - positive
    for arm in ["raw", "proposed"]:
        values = []
        for seed in seeds:
            rows = indexed[f"{arm}-{seed}"]["test"]
            guesses = [rows[i]["verdict"] if i in rows and rows[i]["status"] == "completed" else None for i in ids]
            values.append(dict(macro_f1=macro_f1(truth, guesses),
                false_support_rate=sum(g != "supported" and p == "supported" for g, p in zip(truth, guesses)) / negative if negative else None,
                support_recall=sum(g == p == "supported" for g, p in zip(truth, guesses)) / positive if positive else None))
        scores[arm] = {k: statistics.mean(v[k] for v in values) if all(v[k] is not None for v in values) else None for k in values[0]}
    return {k: scores["proposed"][k] - scores["raw"][k] if scores["raw"][k] is not None else None for k in scores["raw"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--root", type=Path, help="Alternate published artifact directory")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    root = args.root or repo / "docs/results/scifact-label-ablation-v1"
    plan_path = repo / "docs/dataset-manifests/scifact-label-ablation-v1.json"
    plan, analysis, protocol = read(plan_path), read(root / "analysis.json"), read(root / "protocol.json")
    manifest = read(root / "artifact-manifest.json")
    if manifest["plan_sha256"] != sha(plan_path):
        raise ValueError("Artifact manifest plan mismatch")
    for name, expected_hash in manifest["files"].items():
        if Path(name).name != name or sha(root / name) != expected_hash:
            raise ValueError("Artifact package changed: " + name)
    if sha(plan_path) != analysis["plan_sha256"] or sha(plan_path) != protocol["plan_sha256"]:
        raise ValueError("Plan hash mismatch")
    if sha(root / "predictions.jsonl") != analysis["predictions_sha256"]:
        raise ValueError("Prediction hash mismatch")
    for name in ["labels-original.json", "labels-published.json", "labels-doc597.json",
                 "reference-predictions.json", "correction-overlay.json", "training-order.json"]:
        if sha(root / name) != plan["files"][name]:
            raise ValueError("Frozen data changed: " + name)
    old = repo / "docs/results/scifact-verifier-scale-v2"
    if sha(old / "predictions.jsonl") != plan["original_predictions_sha256"]:
        raise ValueError("Archived reference source changed")
    expected_references = [json.loads(l) for l in (old / "predictions.jsonl").read_text().splitlines()
                           if json.loads(l)["method"] in plan["references"]]
    references = read(root / "reference-predictions.json")["records"]
    if expected_references != references:
        raise ValueError("Archived references were modified")
    views = {v: read(root / f"labels-{v}.json") for v in ["original", "published", "doc597"]}
    originals = views["original"]
    overlay = read(root / "correction-overlay.json")["changes"]
    if tuple(len(overlay[k]) for k in ["train", "calibration", "test"]) != (7, 1, 6):
        raise ValueError("Intervention population changed")
    for split in originals:
        expected = dict(originals[split])
        if split in ["calibration", "test"]:
            for row in overlay[split]:
                if expected[row["id"]] != row["original"]:
                    raise ValueError("Proposal starts from wrong label")
                expected[row["id"]] = row["proposed"]
        if expected != views["published"][split]:
            raise ValueError("Published view differs from frozen overlay")
        if split == "test":
            expected["official-dev-597-12779444"] = "insufficient"
        if expected != views["doc597"][split]:
            raise ValueError("Document-scoped view changed unrelated labels")
    methods = plan["methods"] + plan["references"]
    records = [json.loads(l) for l in (root / "predictions.jsonl").read_text().splitlines()]
    indexed = {m: {s: {} for s in originals} for m in methods}
    for r in records + references:
        m, s, i = r["method"], r["split"], r["id"]
        if m not in indexed or s not in originals or i not in originals[s] or i in indexed[m][s]:
            raise ValueError("Unexpected or duplicate prediction")
        if r["status"] == "completed":
            allowed = {"supported", "unsupported"} if m == "minicheck-bf16" else {"supported", "contradicted", "insufficient"}
            if r["verdict"] not in allowed or not 0 <= r["support_score"] <= 1:
                raise ValueError("Invalid classification")
            if m in plan["methods"]:
                probs = r["choice_probabilities"]
                weights = [math.exp(x - max(r["choice_logits"])) for x in r["choice_logits"]]
                if not math.isclose(sum(probs), 1., abs_tol=1e-6) or not all(math.isclose(p, w / sum(weights), abs_tol=1e-6) for p, w in zip(probs, weights)):
                    raise ValueError("Saved probabilities do not match logits")
                if r["verdict"] != ["supported", "contradicted", "insufficient"][probs.index(max(probs))] or r["support_score"] != probs[0]:
                    raise ValueError("Saved verdict does not match logits")
        indexed[m][s][i] = r
    complete = all(len(indexed[m][s]) == len(originals[s]) and all(r["status"] == "completed" for r in indexed[m][s].values()) for m in plan["methods"] for s in originals)
    summary = read(root / "run-summary.json")
    complete = complete and summary["complete"] and all(r["completed"] for r in summary["conditions"])
    if complete != analysis["complete"] or len(records) != analysis["actual_new_predictions"] or len(references) != analysis["archived_reference_predictions"]:
        raise ValueError("Completion/count mismatch")
    for view, splits in views.items():
        for method in methods:
            expected = analysis["results"][view][method]
            cut = threshold(splits["calibration"], indexed[method]["calibration"])
            close(cut, expected["support_threshold"], "calibration-only threshold")
            for split, gold in splits.items():
                rows = indexed[method][split]
                for k, value in binary(gold, rows).items():
                    close(value, expected[split]["binary"][k], (view, method, split, k).__repr__())
                if method != "minicheck-bf16":
                    guesses = [rows[i]["verdict"] if i in rows and rows[i]["status"] == "completed" else None for i in gold]
                    close(macro_f1(list(gold.values()), guesses), expected[split]["three_class"]["macro_f1"], "macro F1")
                elif "three_class" in expected[split]:
                    raise ValueError("Binary baseline has invented three-class results")
                if cut is not None:
                    changed = {i: dict(r, verdict="supported" if r["support_score"] >= cut else "unsupported") for i, r in rows.items()}
                    for k, value in binary(gold, changed).items():
                        close(value, expected[split]["threshold_binary"][k], "threshold " + k)
    scoring = read(old / "scoring-cases.json")
    if {r["id"]: r["label"] for r in scoring["cases"]} != originals["test"]:
        raise ValueError("Original test population/gold changed")
    groups = scoring["ordered_groups"]
    seeds = plan["config"]["seeds"]
    for view, gold in views.items():
        for k, value in contrast(list(originals["test"]), gold["test"], indexed, seeds).items():
            close(value, analysis["paired_contrasts"][view]["delta"][k], "paired " + k)
    if args.bootstrap and complete:
        draws = {v: {k: [] for k in ["macro_f1", "false_support_rate", "support_recall"]} for v in ["original", "published"]}
        rng = random.Random(plan["config"]["bootstrap_seed"])
        for _ in range(plan["config"]["bootstrap_draws"]):
            ids = [i for group in rng.choices(groups, k=len(groups)) for i in group]
            for view in draws:
                for k, value in contrast(ids, views[view]["test"], indexed, seeds).items():
                    if value is not None:
                        draws[view][k].append(value)
        for view in draws:
            for k, values in draws[view].items():
                expected = analysis["paired_contrasts"][view]["intervals"][k]
                close(len(values), expected["valid_draws"], "bootstrap count")
                for key, quantiles in [("ci95", (.025, .975)), ("simultaneous_ci9875", (.00625, .99375))]:
                    if expected[key] is None:
                        continue
                    values.sort()
                    intervals = []
                    for q in quantiles:
                        position = (len(values) - 1) * q
                        low = math.floor(position)
                        intervals.append(values[low] + (values[math.ceil(position)] - values[low]) * (position - low))
                    close(intervals, expected[key], "independent interval")
    training = read(root / "training.json")
    if complete and set(training) != set(plan["methods"]):
        raise ValueError("Missing training metadata")
    for m, info in training.items():
        if info["steps"] != 128 or info["examples_seen"] != 512 or info["precision"] != "bf16" or info["trainable_parameters"] != 2523136:
            raise ValueError("Training budget changed")
        if len(info["losses"]) != 128 or not all(math.isfinite(x) for x in info["losses"]) or info["adapter_max_delta"] <= 0:
            raise ValueError("Training incomplete or nonfinite")
    print(json.dumps(dict(complete=complete, new_predictions=len(records), archived_references=len(references),
                         views=list(views), groups=len(groups), independent_bootstrap=args.bootstrap), indent=2))


if __name__ == "__main__":
    main()
