"""Build disjoint, frozen inputs and scorer-only labels from official data.

Only inputs.json is consumed by the agent. Gold evidence, answers, annotation
metadata and source comparison tables are never placed in its corpus.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import random
import tarfile
import textwrap
from .download import sha256


def normalized(text):
    return " ".join(text.split())


def document(doc_id, title, sections, uri=None):
    lines, paragraphs = [normalized(title)], []
    for heading, texts in sections:
        if heading:
            lines.append(normalized(heading))
        for text in texts:
            text = normalized(text)
            if not text:
                continue
            start = len(lines) + 1
            lines.extend(textwrap.wrap(text, width=120, break_long_words=False, break_on_hyphens=False))
            paragraphs.append(dict(id=f"p{len(paragraphs):04d}", start_line=start, end_line=len(lines), text=text))
    body = "\n".join(lines)
    if len(body) > 500000 or len(lines) > 10000 or any(len(line) > 16000 for line in lines):
        raise ValueError("document_exceeds_source_limits")
    return dict(id=str(doc_id), title=title[:200], uri=uri, text=body, paragraphs=paragraphs,
                text_sha256=hashlib.sha256(body.encode()).hexdigest())


def tar_json(path, suffix):
    # Read a known member without extracting filesystem paths from an archive.
    with tarfile.open(path) as archive:
        member = next(m for m in archive.getmembers() if m.isfile() and m.name.endswith(suffix))
        return json.load(archive.extractfile(member))


def qasper_candidates(raw, official_split):
    archive = "qasper-test-and-evaluator-v0.3.tgz" if official_split == "test" else "qasper-train-dev-v0.3.tgz"
    records = tar_json(Path(raw) / archive, f"qasper-{official_split}-v0.3.json")
    candidates, documents, excluded = [], {}, Counter()
    for paper_id, paper in sorted(records.items()):
        try:
            doc = document(paper_id, paper["title"], [("Abstract", [paper["abstract"]])] +
                           [(s["section_name"], s["paragraphs"]) for s in paper["full_text"]],
                           "https://arxiv.org/abs/" + paper_id)
        except ValueError:
            excluded["document_exceeds_source_limits"] += len(paper["qas"])
            continue
        lookup = {}
        for paragraph in doc["paragraphs"]:
            lookup.setdefault(normalized(paragraph["text"]), []).append(paragraph["id"])
        for qa in paper["qas"]:
            refs, reason = [], None
            for entry in qa["answers"]:
                answer = entry["answer"]
                evidence = answer.get("evidence", [])
                if any(e.startswith("FLOAT SELECTED") for e in evidence):
                    reason = "requires_figure_or_table"; break
                if any(normalized(e) not in lookup for e in evidence):
                    reason = "unmapped_evidence"; break
                unknown = answer["unanswerable"]
                if not unknown and not evidence:
                    reason = "answer_without_evidence"; break
                text = ("" if unknown else " ".join(answer["extractive_spans"]) if answer["extractive_spans"]
                        else answer["free_form_answer"] if answer["free_form_answer"]
                        else "Yes" if answer["yes_no"] is True else "No" if answer["yes_no"] is False else "")
                if not unknown and not text:
                    reason = "empty_answer"; break
                refs.append(dict(answer=text, unanswerable=unknown,
                    evidence_paragraph_ids=sorted({pid for e in evidence for pid in lookup[normalized(e)]})))
            if reason or not refs:
                excluded[reason or "no_annotations"] += 1
                continue
            task_id = "qasper-" + qa["question_id"]
            task = dict(id=task_id, question=qa["question"], source_ids=[paper_id],
                        targets=[dict(id="answer", source_id=paper_id, field=qa["question"])])
            gold = dict(targets=[dict(id="answer", source_id=paper_id, references=refs)],
                        evidence_status="official_paragraph_annotations")
            candidates.append((task, gold))
            documents[paper_id] = doc
    return candidates, documents, dict(excluded)


def freeze(output, name, split, candidates, documents, count, seed, provenance, excluded):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    if count > len(candidates):
        raise ValueError(f"Requested {count} tasks, only {len(candidates)} eligible")
    selected = sorted(candidates, key=lambda pair: pair[0]["id"])
    random.Random(seed).shuffle(selected)
    selected = selected[:count]
    ids = sorted({sid for task, _ in selected for sid in task["source_ids"]})
    inputs = dict(schema_version=1, dataset=name, split=split,
                  documents=[documents[sid] for sid in ids], tasks=[t for t, _ in selected])
    gold = dict(schema_version=1, dataset=name, split=split, tasks={t["id"]: g for t, g in selected})
    for filename, value in (("inputs.json", inputs), ("gold.json", gold)):
        (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = dict(schema_version=1, dataset=name, split=split, seed=seed, task_count=count,
                    document_count=len(ids), eligible_count=len(candidates), exclusions=excluded,
                    task_ids=[t["id"] for t, _ in selected], document_ids=ids,
                    inputs_sha256=sha256(output / "inputs.json"), gold_sha256=sha256(output / "gold.json"),
                    preprocessing="Whitespace-normalized paragraphs wrapped at 120 characters; exact paragraph-to-line map",
                    provenance=provenance)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def prepare_qasper(raw, output, dev_count=50, test_count=200, seed=20261104):
    manifests = {}
    for split, original, count in (("dev", "dev", dev_count), ("test", "test", test_count)):
        candidates, docs, excluded = qasper_candidates(raw, original)
        manifests[split] = freeze(Path(output) / split, "qasper-text-v0.3", split, candidates, docs, count, seed,
            dict(official_split=original, source="https://huggingface.co/datasets/allenai/qasper",
                 downloads=json.loads((Path(raw) / "downloads.json").read_text("utf-8"))), excluded)
    if set(manifests["dev"]["document_ids"]) & set(manifests["test"]["document_ids"]):
        raise ValueError("Development/test paper overlap")
    return manifests


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dev-count", type=int, default=50)
    parser.add_argument("--test-count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261104)
    args = parser.parse_args()
    result = prepare_qasper(args.raw, args.out, args.dev_count, args.test_count, args.seed)
    print(json.dumps({s: {k: v[k] for k in ("task_count", "document_count", "exclusions")} for s, v in result.items()}))
