"""Post-hoc diagnosis of the frozen verifier experiment; no model execution.

The old test is now observed development evidence. Threshold replays below are
exploratory and never replace the preregistered results or their adoption gate.
Requires the original SciFact archive and the frozen prepared data directory.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import tarfile


LABELS = ("supported", "contradicted", "insufficient")
METHODS = ("large-bf16", "large-lora-20261106", "large-lora-20261107",
           "large-lora-20261108", "minicheck-bf16")
SEEDS = (20261106, 20261107, 20261108)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable_order(rows, salt):
    return sorted(rows, key=lambda r: hashlib.sha256((salt + r["id"]).encode()).hexdigest())


def components(rows):
    parent = list(range(len(rows)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, row in enumerate(rows):
        for key in row["group_keys"]:
            if key in seen:
                parent[find(i)] = find(seen[key])
            seen[key] = i
    return len({find(i) for i in range(len(rows))})


def binary_metrics(rows, predictions, threshold=None):
    tp = fp = tn = fn = 0
    for row in rows:
        pred = predictions[row["id"]]
        positive = pred["verdict"] == "supported" if threshold is None else pred["support_score"] >= threshold
        actual = row["label"] == "supported"
        tp += positive and actual
        fp += positive and not actual
        tn += not positive and not actual
        fn += not positive and actual
    return dict(tp=tp, fp=fp, tn=tn, fn=fn,
                precision=tp / (tp + fp) if tp + fp else None,
                recall=tp / (tp + fn), false_positive_rate=fp / (fp + tn),
                balanced_accuracy=(tp / (tp + fn) + tn / (tn + fp)) / 2)


def calibration_threshold(rows, predictions):
    # An exploratory rule fixed in this script: maximize calibration recall
    # subject to <=5% empirical FPR. Ties prefer fewer FP, then larger threshold.
    scores = [predictions[r["id"]]["support_score"] for r in rows]
    candidates = sorted({0.0, math.nextafter(1.0, math.inf), *scores})
    feasible = []
    for threshold in candidates:
        result = binary_metrics(rows, predictions, threshold)
        if result["false_positive_rate"] <= .05:
            feasible.append((result["tp"], -result["fp"], threshold))
    return max(feasible)[2]


def three_class_metrics(rows, predictions):
    if any(predictions[r["id"]]["verdict"] not in LABELS for r in rows):
        raise ValueError("Binary-only models have no three-class score")
    per_class = {}
    for label in LABELS:
        tp = sum(r["label"] == label and predictions[r["id"]]["verdict"] == label for r in rows)
        fp = sum(r["label"] != label and predictions[r["id"]]["verdict"] == label for r in rows)
        fn = sum(r["label"] == label and predictions[r["id"]]["verdict"] != label for r in rows)
        per_class[label] = dict(f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
                                recall=tp / (tp + fn) if tp + fn else None)
    return dict(macro_f1=sum(v["f1"] for v in per_class.values()) / 3, per_class=per_class)


def external_sensitivity(parts, predictions, directory):
    """Apply a separate view of published corrections, never rewrite gold.

    Null doc_id in a claim-level correction means *annotated* evidence docs,
    not every cited document. The per-document exception with null doc_id is
    unresolved here and is kept in a separate explicitly resolved scenario.
    """
    mapping = {"SUPPORT": "supported", "CONTRADICT": "contradicted", "NEI": "insufficient"}
    provenance = read(directory / "provenance.json")
    for name, metadata in provenance["files"].items():
        assert sha(directory / name) == metadata["sha256"], "External correction changed: " + name
    records = {origin: read(directory / f"{origin}_corrections.json")["corrections"] for origin in ("train", "dev")}
    changed, corrected, unresolved = {}, {}, []
    for split, rows in parts.items():
        corrected[split], changed[split] = [], []
        for row in rows:
            origin, cid, pid = row["id"].rsplit("-", 2)
            updated = row
            for correction in records[origin.split("-")[1]]:
                if int(cid) != correction["claim_id"]:
                    continue
                doc = correction.get("doc_id")
                if correction["error_type"] == "per_document_outcome_mismatch" and doc is None:
                    unresolved.append(dict(id=row["id"], reason="Per-document correction has no doc_id; do not broadcast to claim"))
                    continue
                if doc is not None and str(doc) != pid:
                    continue
                if row["label"] != mapping[correction["original_label"]]:
                    continue  # Cited but unannotated abstracts retain insufficient.
                updated = dict(row, label=mapping[correction["corrected_label"]])
                changed[split].append(dict(id=row["id"], original_label=row["label"], proposed_label=updated["label"], error_type=correction["error_type"]))
            corrected[split].append(updated)
    scenarios = {"original": parts, "published_unambiguous": corrected}
    # Local scope resolution, kept distinct from verbatim external JSON:
    # abstract 12779444 measures mortality; the other two report incidence.
    resolved = {s: list(rows) for s, rows in corrected.items()}
    resolved["test"] = [dict(r, label="insufficient") if r["id"] == "official-dev-597-12779444" else r for r in corrected["test"]]
    scenarios["published_plus_local_doc597_resolution"] = resolved
    metrics = {}
    for name, splits in scenarios.items():
        metrics[name] = {}
        for method in METHODS:
            metrics[name][method] = {}
            for split in ("calibration", "test"):
                p = predictions[method][split]
                value = dict(binary=binary_metrics(splits[split], p))
                if method != "minicheck-bf16":
                    value["three_class"] = three_class_metrics(splits[split], p)
                metrics[name][method][split] = value
    return dict(status="Sensitivity to a published single-annotator correction proposal; not newly adjudicated gold",
                source_commit=provenance["source_commit"],
                sources={n: sha(directory / n) for n in ("train_corrections.json", "dev_corrections.json")},
                matched_corrections=changed, unresolved_doc_specific=unresolved,
                local_scope_resolution=dict(id="official-dev-597-12779444", proposed_label="insufficient",
                    reason="The full supplied abstract reports mortality, not incidence; independent human adjudication pending"),
                metrics=metrics)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, default=Path("docs/results/scifact-verifier-scale-v2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path)
    parser.add_argument("--external-corrections", type=Path,
                        default=Path("docs/dataset-manifests/scifact-audit-bionlp2026"))
    args = parser.parse_args()
    data, results = args.data_dir, args.results_dir
    plan = read(data / "plan.json")
    freeze = read(results / "frozen-manifest.json")
    assert sha(data / "plan.json") == freeze["plan_sha256"]
    assert sha(args.raw) == plan["raw_sha256"]
    names = ["training.json", "calibration-inputs.json", "calibration-gold.json", "test-inputs.json", "test-gold.json"]
    for name in names:
        assert sha(data / name) == plan["files"][name], name
    parts = {"train": read(data / "training.json")["cases"]}
    for split in ("calibration", "test"):
        gold = read(data / (split + "-gold.json"))["labels"]
        inputs = read(data / (split + "-inputs.json"))["cases"]
        assert len(inputs) == len({r["id"] for r in inputs})
        assert {r["id"] for r in inputs} == set(gold)
        parts[split] = [dict(r, label=gold[r["id"]]) for r in inputs]
    with tarfile.open(args.raw) as archive:
        raw = {name: [json.loads(line) for line in archive.extractfile("data/" + name + ".jsonl")]
               for name in ("corpus", "claims_train", "claims_dev")}
    corpus = {str(d["doc_id"]): d for d in raw["corpus"]}
    claims = {"official-" + split: {str(c["id"]): c for c in raw["claims_" + split]}
              for split in ("train", "dev")}
    stats, input_audit, across, signatures = {}, Counter(), {}, defaultdict(list)
    for split, rows in parts.items():
        assert len(rows) == len({r["id"] for r in rows})
        word_lengths = []
        for row in rows:
            origin, claim_id, paper_id = row["id"].rsplit("-", 2)
            claim, doc = claims[origin][claim_id], corpus[paper_id]
            annotations = claim["evidence"].get(paper_id, [])
            labels = {a["label"] for a in annotations}
            assert len(labels) <= 1
            expected = {"SUPPORT": "supported", "CONTRADICT": "contradicted"}.get(next(iter(labels), None), "insufficient")
            assert expected == row["label"]
            assert row["claim"] == claim["claim"]
            assert paper_id in set(map(str, claim["cited_doc_ids"])) | set(claim["evidence"])
            quote = doc["title"] + "\n" + "\n".join(f"{i + 1}: {s}" for i, s in enumerate(doc["abstract"]))
            assert len(row["evidence"]) == 1 and row["evidence"][0]["quote"] == quote
            for annotation in annotations:
                assert all(0 <= i < len(doc["abstract"]) for i in annotation["sentences"])
            input_audit["rows_verified_against_official_source"] += 1
            input_audit["explicit_annotation" if annotations else "cited_abstract_without_annotation"] += 1
            normalized = (" ".join(row["claim"].lower().split()), " ".join(quote.lower().split()))
            signatures[normalized].append((split, row["id"], row["label"]))
            word_lengths.append(len(quote.split()))
        across[split] = {k for r in rows for k in r["group_keys"]}
        stats[split] = dict(cases=len(rows), labels=dict(Counter(r["label"] for r in rows)),
                            groups=components(rows), unique_claims=len({r["claim"] for r in rows}),
                            unique_papers=len({r["evidence"][0]["source_id"] for r in rows}),
                            abstracts_with_ellipsis=sum("…" in r["evidence"][0]["quote"] for r in rows),
                            evidence_words=dict(min=min(word_lengths), median=statistics.median(word_lengths), max=max(word_lengths)))
    overlaps = {a + "/" + b: len(across[a] & across[b]) for a, b in (("train", "calibration"), ("train", "test"), ("calibration", "test"))}
    assert not any(overlaps.values())
    duplicates = [v for v in signatures.values() if len(v) > 1]
    train_seen = {}
    for seed in SEEDS:
        order = list(range(len(parts["train"])))
        random.Random(seed).shuffle(order)
        seen = [parts["train"][i] for i in order[:512]]
        train_seen[str(seed)] = dict(cases=len(seen), labels=dict(Counter(r["label"] for r in seen)), groups=components(seen))
    preds = {m: {s: {} for s in ("calibration", "test")} for m in METHODS}
    for line in (results / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
        p = json.loads(line)
        if p["method"] not in METHODS or p["split"] not in ("calibration", "test"):
            continue
        target = preds[p["method"]][p["split"]]
        assert p["id"] not in target and p["status"] == "completed"
        target[p["id"]] = p
    source_analysis = read(results / "analysis.json")
    sensitivity = external_sensitivity(parts, preds, args.external_corrections)
    default, replays = {}, {}
    for method in METHODS:
        for split in ("calibration", "test"):
            assert set(preds[method][split]) == {r["id"] for r in parts[split]}
        default[method] = {s: binary_metrics(parts[s], preds[method][s]) for s in ("calibration", "test")}
        for split in ("calibration", "test"):
            recorded = source_analysis["results"][method][split]["binary"]
            assert math.isclose(default[method][split]["false_positive_rate"], recorded["unsupported_false_supported_rate"])
            assert math.isclose(default[method][split]["precision"], recorded["supported_precision"])
            if method != "minicheck-bf16":
                assert math.isclose(three_class_metrics(parts[split], preds[method][split])["macro_f1"],
                                    source_analysis["results"][method][split]["three_class"]["macro_f1"])
        threshold = calibration_threshold(parts["calibration"], preds[method]["calibration"])
        replays[method] = dict(calibration_only_selected_threshold=threshold,
            calibration=binary_metrics(parts["calibration"], preds[method]["calibration"], threshold),
            observed_test=binary_metrics(parts["test"], preds[method]["test"], threshold),
            fixed_thresholds={str(t): {s: binary_metrics(parts[s], preds[method][s], t) for s in ("calibration", "test")}
                              for t in (.5, .75, .9, .95)})
    base = preds["large-bf16"]["test"]
    trained = [m for m in METHODS if "lora" in m]
    errors = {}
    for method in trained:
        p = preds[method]["test"]
        errors[method] = dict(
            new_false_support=[r["id"] for r in parts["test"] if r["label"] != "supported" and base[r["id"]]["verdict"] != "supported" and p[r["id"]]["verdict"] == "supported"],
            repaired_false_support=[r["id"] for r in parts["test"] if r["label"] != "supported" and base[r["id"]]["verdict"] == "supported" and p[r["id"]]["verdict"] != "supported"],
            false_support_below_half=[r["id"] for r in parts["test"] if r["label"] != "supported" and p[r["id"]]["verdict"] == "supported" and p[r["id"]]["support_score"] < .5])
    common_wrong = [r for r in parts["test"] if all(preds[m]["test"][r["id"]]["verdict"] != r["label"] for m in trained)]
    disagreement = [r["id"] for r in parts["test"] if len({preds[m]["test"][r["id"]]["verdict"] for m in trained}) > 1]
    # Selection rule is fixed before inspecting this training sample's text.
    train_review = [r for label in LABELS for r in stable_order([r for r in parts["train"] if r["label"] == label], "train-review-v1:")[:10]]
    review_ids = {r["id"] for r in train_review + common_wrong}
    for label in LABELS:
        correct = [r for r in parts["test"] if r["label"] == label and all(preds[m]["test"][r["id"]]["verdict"] == r["label"] for m in trained)]
        review_ids.update(r["id"] for r in stable_order(correct, "correct-review-v1:")[:5])
    review_ids.update(i for m in trained for i in errors[m]["new_false_support"])
    review_ids.update(r["id"] for rows in sensitivity["matched_corrections"].values() for r in rows)
    review_ids.update(r["id"] for r in sensitivity["unresolved_doc_specific"])
    reviews = stable_order([r for rows in parts.values() for r in rows if r["id"] in review_ids], "blinded-review-order-v1:")
    if args.review_dir:
        args.review_dir.mkdir(parents=True, exist_ok=True)
        anonymous = {r["id"]: "review-" + hashlib.sha256(("packet-v1:" + r["id"]).encode()).hexdigest()[:12] for r in reviews}
        packet = dict(status="unannotated; requires independent human review", cases=[dict(id=anonymous[r["id"]], claim=r["claim"], evidence=r["evidence"], proposed_label=None, rationale=None) for r in reviews])
        (args.review_dir / "blind-inputs.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        split_by_id = {r["id"]: split for split, rows in parts.items() for r in rows}
        key = dict(selection="30 stratified hash-selected train cases; all common LoRA test errors; 5 all-correct controls per class; all newly false-supported test cases; all matched external correction candidates and unresolved doc-specific cases",
                   cases=[dict(id=anonymous[r["id"]], source_id=r["id"], official_label=r["label"], split=split_by_id[r["id"]]) for r in reviews])
        (args.review_dir / "coordinator-key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    losses = {str(t["seed"]): dict(block_means=[statistics.mean(t["losses"][i:i+32]) for i in range(0,128,32)],
                                  note="Different minibatches; not a validation curve or an overfitting test") for t in read(results / "training.json")}
    report = dict(status="post-hoc exploratory diagnosis; frozen gold and results unchanged",
        provenance=dict(plan_sha256=sha(data / "plan.json"), raw_sha256=sha(args.raw), predictions_sha256=sha(results / "predictions.jsonl"), script_sha256=sha(__file__)),
        source_checks=dict(input_audit=input_audit, group_key_overlaps=overlaps, duplicate_inputs=duplicates),
        partitions=stats, training_exposure=train_seen, default_binary=default,
        threshold_replays=replays, error_transitions=errors,
        common_wrong=dict(count=len(common_wrong), labels=dict(Counter(r["label"] for r in common_wrong)), ids=[r["id"] for r in common_wrong]),
        seed_disagreement=dict(count=len(disagreement), ids=disagreement), training_losses=losses,
        external_correction_sensitivity=sensitivity,
        training_review_ids=[r["id"] for r in train_review], review_packet_cases=len(reviews),
        limitations=["Post-hoc threshold results on an observed test are exploratory, not a fresh confirmatory experiment.",
                     "The 65 calibration pairs include only 43 negatives; empirical <=5% FPR is not a population guarantee.",
                     "Source mapping checks cannot establish semantic gold correctness.",
                     "The review sample intentionally overrepresents errors; it cannot estimate overall label-noise prevalence.",
                     "Human annotation is pending. No model agreement is used to change official gold."])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(partitions=stats, common_wrong=len(common_wrong), review_packet_cases=len(reviews), output=str(args.output)), ensure_ascii=False))


if __name__ == "__main__":
    main()
