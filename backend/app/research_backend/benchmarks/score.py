"""Score frozen predictions after generation; never imports the agent runner."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import string
from .download import sha256


def tokens(text):
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\b(a|an|the)\b", " ", text).split()


def answer_f1(prediction, reference):
    if prediction["unanswerable"] or reference["unanswerable"]:
        return float(prediction["unanswerable"] == reference["unanswerable"])
    pred, gold = tokens(prediction["answer"]), tokens(reference["answer"])
    if not pred or not gold:
        return float(pred == gold)
    overlap = sum((Counter(pred) & Counter(gold)).values())
    return 2 * overlap / (len(pred) + len(gold))


def set_f1(pred, gold):
    if not pred or not gold:
        return float(pred == gold)
    return 2 * len(pred & gold) / (len(pred) + len(gold))


def score_prediction(prediction, task, labels, documents):
    answers = {a["target_id"]: a for a in prediction.get("answers", [])}
    reads = {e["id"]: e for e in prediction.get("reads", [])}
    valid = {}
    for eid, evidence in reads.items():
        doc = documents.get(evidence.get("source_id"))
        if not doc:
            continue
        lines = doc["text"].splitlines()
        start, end = evidence["start_line"], evidence["end_line"]
        if 1 <= start <= end <= len(lines) and "\n".join(lines[start-1:end]) == evidence["quote"]:
            valid[eid] = evidence
    per_target = []
    for target in labels["targets"]:
        answer = answers.get(target["id"])
        complete = prediction["status"] == "completed" and answer is not None
        f1 = max((answer_f1(answer, ref) for ref in target["references"]), default=0) if complete else 0
        cited = [valid[eid] for eid in (answer or {}).get("evidence_ids", [])
                 if eid in valid and valid[eid]["source_id"] == target["source_id"]]
        read_windows = [e for e in valid.values() if e["source_id"] == target["source_id"]]
        paragraphs = documents[target["source_id"]]["paragraphs"]

        def hits(windows):
            return {p["id"] for p in paragraphs if any(e["start_line"] <= p["end_line"] and e["end_line"] >= p["start_line"] for e in windows)}

        cited_pids, read_pids = hits(cited), hits(read_windows)
        evidence_refs = [set(ref["evidence_paragraph_ids"]) for ref in target["references"]
                         if ref.get("evidence_paragraph_ids")]
        paragraph_f1 = max((set_f1(cited_pids, ref) for ref in evidence_refs), default=None)
        recall = max((len(read_pids & ref) / len(ref) for ref in evidence_refs), default=None)
        # Missing/failed outputs remain zero, never dropped from answer averages.
        per_target.append(dict(target_id=target["id"], answer_token_f1=f1,
            abstention_agrees_with_any_reference=float(complete and any(answer["unanswerable"] == ref["unanswerable"] for ref in target["references"])),
            cited_paragraph_overlap_f1=paragraph_f1, read_paragraph_recall=recall,
            citation_ids_valid=bool(complete and all(eid in valid and valid[eid]["source_id"] == target["source_id"] for eid in answer["evidence_ids"])),
            citation_semantic_support=None))
    mean = lambda key: sum(t[key] for t in per_target if t[key] is not None) / max(1, sum(t[key] is not None for t in per_target))
    return dict(task_id=task["id"], method=prediction["method"], status=prediction["status"], targets=per_target,
                answer_token_f1=mean("answer_token_f1"),
                read_paragraph_recall=mean("read_paragraph_recall") if any(t["read_paragraph_recall"] is not None for t in per_target) else None,
                cited_paragraph_overlap_f1=mean("cited_paragraph_overlap_f1") if any(t["cited_paragraph_overlap_f1"] is not None for t in per_target) else None,
                tokens_used=prediction.get("tokens_used"), elapsed_seconds=prediction.get("elapsed_seconds"))


def score(inputs_path, gold_path, run_dir):
    inputs = json.loads(inputs_path.read_text("utf-8"))
    gold = json.loads(gold_path.read_text("utf-8"))
    manifest = json.loads((inputs_path.parent / "manifest.json").read_text("utf-8"))
    protocol = json.loads((run_dir / "protocol.json").read_text("utf-8"))
    if sha256(inputs_path) != manifest["inputs_sha256"] or sha256(gold_path) != manifest["gold_sha256"] or protocol["inputs_sha256"] != manifest["inputs_sha256"]:
        raise ValueError("Inputs, gold, and run must match the frozen manifest")
    predictions = [json.loads(line) for line in (run_dir / "predictions.jsonl").read_text("utf-8").splitlines() if line.strip()]
    by_key = {(p["task_id"], p["method"]): p for p in predictions}
    if len(by_key) != len(predictions):
        raise ValueError("Duplicate prediction; do not silently choose the better attempt")
    tasks = {t["id"]: t for t in inputs["tasks"]}
    documents = {d["id"]: d for d in inputs["documents"]}
    records = []
    for method in protocol["methods"]:
        for tid in protocol["task_ids"]:
            prediction = by_key.get((tid, method), dict(task_id=tid, method=method, status="missing", answers=[], reads=[]))
            records.append(score_prediction(prediction, tasks[tid], gold["tasks"][tid], documents))
    summaries = {}
    for method in protocol["methods"]:
        rows = [r for r in records if r["method"] == method]
        summary = dict(tasks=len(rows), completed=sum(r["status"] == "completed" for r in rows))
        for key in ("answer_token_f1", "read_paragraph_recall", "cited_paragraph_overlap_f1", "tokens_used", "elapsed_seconds"):
            values = [r[key] for r in rows if r[key] is not None]
            summary[key] = sum(values) / len(values) if values else None
            summary[key + "_n"] = len(values)
        summaries[method] = summary
    result = dict(dataset=inputs["dataset"], split=inputs["split"], summaries=summaries, records=records,
        limitations=["Task-macro token F1; not a semantic correctness judge or published leaderboard score.",
                     "Paragraph overlap counts a located portion; it does not establish entailment.",
                     "ArxivDIGESTables evidence labels remain pending, so evidence scores are null.",
                     "Missing and failed runs remain in the answer-score denominator."])
    (run_dir / "scores.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(score(args.inputs, args.gold, args.run)["summaries"], indent=2))
