"""Audit the published pilot with Python's standard library; no GPU or downloads."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[1]
    root = repo / "docs/results/scifact-verifier-pilot-v1"
    read = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
    manifest = json.loads((repo / "docs/dataset-manifests/scifact-verifier-pilot-v1.json").read_text(encoding="utf-8"))
    plan = manifest["plan"]
    for path, key in [("backend/app/research_backend/benchmarks/verifier_pilot.py", "code_sha256"),
                      ("docs/experiments/SCIFACT_VERIFIER_PILOT.md", "protocol_sha256")]:
        if hashlib.sha256((repo / path).read_bytes()).hexdigest() != plan[key]:
            raise ValueError("Frozen source changed: " + path)
    analysis = read("analysis.json")
    plan_hash = hashlib.sha256((json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest()
    if analysis["plan_sha256"] != plan_hash or read("protocol.json")["plan_sha256"] != plan_hash:
        raise ValueError("Run does not match the pre-registered plan")
    raw = (root / "predictions.jsonl").read_bytes()
    if hashlib.sha256(raw).hexdigest() != analysis["predictions_sha256"]:
        raise ValueError("Prediction hash mismatch")
    records = [json.loads(line) for line in raw.splitlines()]
    keys = {(r["method"], r["split"], r["id"]) for r in records}
    if len(keys) != len(records):
        raise ValueError("Duplicate predictions")
    gold = {r["id"]: r["label"] for r in read("scoring-cases.json")["cases"]}
    if len(gold) != plan["counts"]["test"]["cases"]:
        raise ValueError("Test denominator changed")
    methods = ["base"] + ["lora-" + str(s) for s in plan["config"]["seeds"]]
    split_ids = dict(manifest["case_ids"])
    split_ids.pop("train", None)
    split_ids["empty-evidence"] = split_ids["test"][:32]
    expected = {(method, split, case) for method in methods
                for split, cases in split_ids.items() for case in cases}
    if keys != expected or any(r["status"] != "completed" for r in records):
        raise ValueError("Missing, extra or failed formal record")
    if set(gold) != set(split_ids["test"]) or dict(Counter(gold.values())) != plan["counts"]["test"]["labels"]:
        raise ValueError("Test scoring metadata changed")
    labels = ("supported", "contradicted", "insufficient")
    scores = {}
    for method in methods:
        chosen = [r for r in records if r["method"] == method and r["split"] == "test"]
        predictions = {r["id"]: r["verdict"] for r in chosen if r["status"] == "completed"}
        if set(predictions) != set(gold):
            raise ValueError("Unexpected or missing test prediction: " + method)
        matrix = Counter((gold[i], p) for i, p in predictions.items())
        f1s = []
        for label in labels:
            tp = matrix[label, label]
            fp = sum(v for (truth, pred), v in matrix.items() if pred == label and truth != label)
            fn = sum(v for (truth, pred), v in matrix.items() if truth == label and pred != label)
            f1s.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
        scores[method] = sum(f1s) / 3
        if not math.isclose(scores[method], analysis["results"][method]["test"]["macro_f1"], abs_tol=1e-12):
            raise ValueError("Independent macro F1 mismatch: " + method)
    if read("run-summary.json") != {"errors": [], "smoke": False, "complete": True}:
        raise ValueError("Formal run incomplete")
    print(json.dumps({"status": "passed", "test_cases": len(gold), "prediction_records": len(records),
                      "independently_recomputed_test_macro_f1": scores}, indent=2))


if __name__ == "__main__":
    main()
