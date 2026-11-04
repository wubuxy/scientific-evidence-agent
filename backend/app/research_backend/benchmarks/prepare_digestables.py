"""Column-names-only value generation, with disjoint papers and source tables.

Gold table values are references, not verified paragraph evidence. This adapter
explicitly marks evidence annotation as pending; no citation-support score is
invented from string matches.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import random
from .prepare import document, freeze
from .download import DIGEST_REVISION, sha256


def table_candidate(table):
    rows = {str(r["corpus_id"]): r for r in table["row_bib_map"] if r.get("corpus_id")}
    columns = table["table"]
    if not (2 <= len(rows) <= 5 and 2 <= len(columns) <= 4):
        return None
    if any(r.get("type") != "ref" for r in rows.values()):
        return None
    if any(set(values) != set(rows) for values in columns.values()):
        return None
    targets, gold_targets = [], []
    for index, (field, values) in enumerate(columns.items()):
        if not field.strip() or len(field) > 180:
            return None
        for sid, row in rows.items():
            value = values[sid]
            if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str):
                return None
            value = value[0].strip()
            if not value or value.lower() in {"-", "n/a", "na", "unknown", "none"} or len(value) > 500:
                return None
            target_id = f"{sid}:c{index}"
            targets.append(dict(id=target_id, source_id=sid, field=field))
            gold_targets.append(dict(id=target_id, source_id=sid,
                                     references=[dict(answer=value, unanswerable=False,
                                                      evidence_paragraph_ids=None)]))
    task = dict(id="digest-" + table["tabid"],
                question="Compare the supplied papers using the requested fields. Give concise values with evidence for each field. Mark anything you cannot verify as unknown.",
                source_ids=sorted(rows), targets=targets)
    gold = dict(targets=gold_targets, evidence_status="pending_independent_annotation",
                original_table_id=table["tabid"], source_article=table["arxiv_id"])
    return task, gold, rows


def prepare(raw, output, dev_count=10, test_count=40, seed=20261104):
    raw, output = Path(raw), Path(output)
    candidates, rows, excluded = [], {}, Counter()
    with (raw / "tables.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            candidate = table_candidate(json.loads(line))
            if candidate:
                task, gold, metadata = candidate
                candidates.append((task, gold))
                rows.update(metadata)
            else:
                excluded["outside_registered_table_shape_or_values"] += 1
    # One streaming pass over the large official extraction, no boxes/tokens in
    # the corpus. Cache is a derived artifact and is never published to GitHub.
    cache = raw / f"digest-documents-{DIGEST_REVISION}.json"
    if cache.exists():
        docs = json.loads(cache.read_text("utf-8"))
    else:
        docs = {}
        with gzip.open(raw / "full_texts.jsonl.gz", "rt", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                sid = str(record["metadata"]["corpusId"])
                if sid not in rows or sid in docs:
                    continue
                paragraphs = record.get("entities", {}).get("paragraphs", [])
                texts = [p.get("metadata", {}).get("text", "") for p in sorted(
                    paragraphs, key=lambda p: p.get("metadata", {}).get("reading_order", 0))]
                texts = [t for t in texts if t.strip()]
                if not texts:
                    excluded["missing_paragraph_text"] += 1
                    continue
                try:
                    docs[sid] = document(sid, rows[sid]["title"], [("Full text", texts)],
                                         "https://www.semanticscholar.org/paper/" + sid)
                except ValueError:
                    excluded["document_exceeds_source_limits"] += 1
        cache.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    eligible = [c for c in candidates if set(c[0]["source_ids"]).issubset(docs)]
    excluded["table_missing_fulltext"] = len(candidates) - len(eligible)
    random.Random(seed).shuffle(eligible)
    # Development is selected first; test never reuses a paper or source article
    # from development. Tables within a split may share papers: resampling must
    # therefore use paper/table groups, not treat cells as independent examples.
    dev = eligible[:dev_count]
    dev_docs = {sid for task, _ in dev for sid in task["source_ids"]}
    dev_articles = {gold["source_article"] for _, gold in dev}
    test = [c for c in eligible[dev_count:] if not (set(c[0]["source_ids"]) & dev_docs)
            and c[1]["source_article"] not in dev_articles]
    provenance = dict(source="https://huggingface.co/datasets/blnewman/arxivDIGESTables",
                      revision=DIGEST_REVISION, setting="adapted_column_names_only_value_generation",
                      evidence_annotations="pending; reference values are not evidence labels",
                      table_sha256=sha256(raw / "tables.jsonl"),
                      fulltext_sha256=sha256(raw / "full_texts.jsonl.gz"))
    result = {}
    for split, pool, count in (("dev", dev, dev_count), ("test", test, test_count)):
        result[split] = freeze(output / split, "arxivdigestables-values-adapted", split, pool,
                               docs, count, seed, provenance, dict(excluded))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dev-count", type=int, default=10)
    parser.add_argument("--test-count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20261104)
    args = parser.parse_args()
    result = prepare(args.raw, args.out, args.dev_count, args.test_count, args.seed)
    print(json.dumps({s: {k: v[k] for k in ("task_count", "document_count", "eligible_count", "exclusions")} for s, v in result.items()}))
