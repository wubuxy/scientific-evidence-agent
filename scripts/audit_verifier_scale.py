"""Independently audit archived v2 predictions; --bootstrap also checks intervals.

Only Python's standard library is needed. No model weights, GPU or network.
Frozen runtime source is identified by its recorded Git commit, not by requiring
the application working tree to remain unchanged forever.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random

LABELS = ("supported", "contradicted", "insufficient")


def close(actual, expected, description):
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(description)
        for a, b in zip(actual, expected):
            close(a, b, description)
    elif actual is None or expected is None:
        if actual is not expected:
            raise ValueError(description)
    elif not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(description + ": " + repr((actual, expected)))


def macro_f1(truth, predicted):
    matrix = Counter(zip(truth, predicted))
    f1s = []
    for label in LABELS:
        tp = matrix[label, label]
        fp = sum(n for (gold, pred), n in matrix.items() if pred == label and gold != label)
        fn = sum(n for (gold, pred), n in matrix.items() if gold == label and pred != label)
        denominator = 2 * tp + fp + fn
        f1s.append(2 * tp / denominator if denominator else 0)
    return sum(f1s) / 3


def deltas(scores, seeds, precision):
    return dict(scale=scores["large-bf16"] - scores["small-bf16"],
                quantization=scores["large-nf4"] - scores["large-bf16"],
                training=sum(scores["large-lora-" + str(s)] for s in seeds) / len(seeds)
                         - scores["large-" + precision])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--root", type=Path, help="Alternate archived run directory")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    root = args.root or repo / "docs/results/scifact-verifier-scale-v2"
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    frozen = read(root / "frozen-manifest.json")
    plan, protocol, analysis = frozen["plan"], read(root / "protocol.json"), read(root / "analysis.json")
    plan_hash = hashlib.sha256((json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest()
    if not (frozen["plan_sha256"] == protocol["plan_sha256"] == analysis["plan_sha256"] == plan_hash):
        raise ValueError("Frozen plan mismatch")
    if protocol["preflight_sha256"] != frozen["preflight_sha256"]:
        raise ValueError("Hardware decision changed")
    raw = (root / "predictions.jsonl").read_bytes()
    if hashlib.sha256(raw).hexdigest() != analysis["predictions_sha256"]:
        raise ValueError("Raw prediction hash mismatch")
    records = [json.loads(line) for line in raw.splitlines()]
    keyed = {(r["method"], r["split"], r["id"]): r for r in records}
    if len(keyed) != len(records):
        raise ValueError("Duplicate prediction")
    seeds = plan["config"]["seeds"]
    methods = ["small-bf16", "small-nf4", "large-bf16", "large-nf4"]
    methods += ["large-lora-" + str(s) for s in seeds] + ["minicheck-bf16"]
    if protocol["methods"] != methods:
        raise ValueError("Method selection changed")
    fixture = read(repo / "backend/tests/fixtures/support_review_inputs.json")
    splits = dict(calibration=plan["case_ids"]["calibration"], test=plan["case_ids"]["test"],
                  challenge=[r["id"] for r in fixture["cases"]], empty_evidence=plan["case_ids"]["test"][:32])
    splits["empty-evidence"] = splits.pop("empty_evidence")
    allowed = {(m, split, i) for m in methods for split, ids in splits.items() for i in ids}
    if set(keyed) - allowed:
        raise ValueError("Unexpected method, split or case")
    scoring = read(root / "scoring-cases.json")
    gold = {r["id"]: r["label"] for r in scoring["cases"]}
    ids = list(gold)
    if ids != splits["test"]:
        raise ValueError("Test IDs/order changed")
    gold_bytes = (json.dumps(dict(labels=gold), ensure_ascii=False, indent=2) + "\n").encode()
    if hashlib.sha256(gold_bytes).hexdigest() != plan["files"]["test-gold.json"]:
        raise ValueError("Scoring labels differ from frozen gold")
    truth = [gold[i] for i in ids]
    positive = [i for i in ids if gold[i] == "supported"]
    negative = [i for i in ids if gold[i] != "supported"]
    predictions, scores = {}, {}
    for method in methods:
        rows = {i: keyed[method, "test", i] for i in ids if (method, "test", i) in keyed
                and keyed[method, "test", i]["status"] == "completed"}
        permitted = set(LABELS) if method != "minicheck-bf16" else {"supported", "unsupported"}
        if any(r["verdict"] not in permitted or not math.isfinite(r["support_score"])
               or not 0 <= r["support_score"] <= 1 for r in rows.values()):
            raise ValueError("Invalid verdict/score")
        predicted = {i: rows[i]["verdict"] if i in rows else None for i in ids}
        tp = sum(predicted[i] == "supported" for i in positive)
        fp = sum(predicted[i] == "supported" for i in negative)
        tn = sum(predicted[i] in {"contradicted", "insufficient", "unsupported"} for i in negative)
        values = dict(accuracy=(tp + tn) / len(ids),
                      supported_precision=tp / (tp + fp) if tp + fp else None,
                      supported_recall=tp / len(positive),
                      unsupported_false_supported_rate=fp / len(negative),
                      balanced_accuracy=(tp / len(positive) + tn / len(negative)) / 2,
                      missing_predictions=len(ids) - len(rows))
        # Pairwise AUROC is independent of the scorer's average-rank algorithm.
        values["auroc"] = (sum((rows[p]["support_score"] > rows[n]["support_score"])
                                   + .5 * (rows[p]["support_score"] == rows[n]["support_score"])
                                   for p in positive for n in negative) / (len(positive) * len(negative))) if len(rows) == len(ids) else None
        for key, value in values.items():
            close(value, analysis["results"][method]["test"]["binary"][key], method + "/" + key)
        if method != "minicheck-bf16":
            predictions[method] = predicted
            scores[method] = macro_f1(truth, [predicted[i] for i in ids])
            close(scores[method], analysis["results"][method]["test"]["three_class"]["macro_f1"], method)
    points = deltas(scores, seeds, protocol["training_precision"])
    for name, point in points.items():
        close(point, analysis["primary_contrasts"][name]["delta"], name)
    if args.bootstrap:
        groups = scoring["ordered_groups"]
        flat = [i for group in groups for i in group]
        if len(flat) != len(ids) or set(flat) != set(ids):
            raise ValueError("Bootstrap groups do not partition test cases")
        ownership, case_keys = {}, {r["id"]: set(r["group_keys"]) for r in scoring["cases"]}
        for index, group in enumerate(groups):
            connected, remaining = set(), set(group)
            while remaining:
                next_case = next((i for i in group if i in remaining and
                                  (not connected or any(case_keys[i] & case_keys[j] for j in connected))), None)
                if next_case is None:
                    raise ValueError("Disconnected bootstrap group")
                remaining.remove(next_case)
                connected.add(next_case)
            for i in group:
                for key in case_keys[i]:
                    if key in ownership and ownership[key] != index:
                        raise ValueError("Shared paper/claim split across bootstrap groups")
                    ownership[key] = index
        rng, samples = random.Random(seeds[0]), defaultdict(list)
        for _ in range(plan["config"]["bootstrap_draws"]):
            selected = [i for group in rng.choices(groups, k=len(groups)) for i in group]
            ss = {m: macro_f1([gold[i] for i in selected], [p[i] for i in selected]) for m, p in predictions.items()}
            for name, point in deltas(ss, seeds, protocol["training_precision"]).items():
                samples[name].append(point)
        for name, values in samples.items():
            values.sort()
            n = len(values)
            expected = analysis["primary_contrasts"][name]
            close([values[int(n * .025)], values[int(n * .975)]], expected["pointwise_95"], name + "/95CI")
            close([values[int(n * .05 / 6)], values[min(n - 1, int(n * (1 - .05 / 6)))]], expected["bonferroni_98_333"], name + "/adjustedCI")
    print(json.dumps(dict(status="passed", test_cases=len(ids), prediction_records=len(records),
                         planned_records=len(allowed), independently_recomputed_deltas=points,
                         bootstrap_checked=args.bootstrap,
                         training_success_gate=analysis["training_success_gate"]), indent=2))


if __name__ == "__main__":
    main()
