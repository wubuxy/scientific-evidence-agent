"""Export an annotation queue; review files are scorer-only, never model inputs."""
import argparse
import json
from pathlib import Path
import re


def export_review(inputs, gold):
    tasks = {t["id"]: t for t in inputs["tasks"]}
    docs = {d["id"]: d for d in inputs["documents"]}
    records = []
    for task_id, labels in gold["tasks"].items():
        fields = {target["id"]: target for target in tasks[task_id]["targets"]}
        for target in labels["targets"]:
            references = [r["answer"] for r in target["references"]]
            flags = []
            if any(re.search(r"\bours\b|\bthis work\b|\bproposed\b", value, re.I) for value in references):
                flags.append("may_describe_source_article_modification_instead_of_cited_paper")
            if fields[target["id"]]["field"].casefold() in {"task", "instruction", "#labels", "backbone", "upsampling"}:
                flags.append("check_scope_and_experimental_setting")
            records.append(dict(task_id=task_id, target_id=target["id"], source_id=target["source_id"],
                paper_title=docs[target["source_id"]]["title"], field=fields[target["id"]]["field"],
                reference_values=references, source_article=labels.get("source_article"), flags=flags,
                review_status="pending", reviewer=None, corrected_answer=None,
                evidence_paragraph_ids=[], exclude_reason=None,
                instruction="Read the cited paper. Check whether the reference is recoverable from this paper and the supplied field alone. Record support paragraphs or exclude the target. Do not treat a string match as proof."))
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = export_review(json.loads(args.inputs.read_text("utf-8")), json.loads(args.gold.read_text("utf-8")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(dict(targets=len(rows), flagged=sum(bool(r["flags"]) for r in rows), status="pending_review")))
