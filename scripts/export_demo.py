"""Export an explicit historical run, public paper text and tool addresses only."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def export(store_root, run_id, output, research_evidence):
    database = store_root / "research.sqlite3"
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)

    def record(identifier):
        row = connection.execute("SELECT payload FROM records WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Missing historical record: " + identifier)
        return json.loads(row[0])

    run = record(run_id)
    report = run["report"]
    ids = list(dict.fromkeys(eid for item in report["claims"] + report["comparison_rows"] for eid in item["evidence_ids"]))
    evidence = [record(eid) for eid in ids]
    calls = [json.loads(row[0]) for row in connection.execute(
        "SELECT payload FROM records WHERE kind='tool_call' AND parent_id=? ORDER BY rowid", (run_id,))]
    read_ids = {call["data"]["id"] for call in calls if call["name"] == "sources_read" and call["status"] == "succeeded"}
    if not set(ids).issubset(read_ids):
        raise ValueError("A displayed citation was not read during this run")
    sources, texts = [], {}
    papers = output / "papers"
    papers.mkdir(parents=True, exist_ok=True)
    descriptions = [
        ("qasper", "A Dataset of Information-Seeking Questions and Answers Anchored in Research Papers",
         "Pradeep Dasigi, Kyle Lo, Iz Beltagy, Arman Cohan, Noah A. Smith, Matt Gardner", "2105.03011v1", 17, 35),
        ("digestables", "ArxivDIGESTables: Synthesizing Scientific Literature into Tables using Language Models",
         "Benjamin Newman, Yoonjoo Lee, Aakanksha Naik, Pao Siangliulue, Raymond Fok, Juho Kim, Daniel S. Weld, Joseph Chee Chang, Kyle Lo", "2410.22360v1", 17, 40),
    ]
    if len(evidence) != len(descriptions):
        raise ValueError("This curated export requires exactly the QASPER and ArxivDIGESTables citations")
    for item, description in zip(evidence, descriptions):
        name, title, authors, arxiv, preview_start, preview_end = description
        source = record(item["source_id"])
        if name not in source["title"].lower():
            raise ValueError("Unexpected source order; do not apply this attribution to another paper")
        text = (store_root / run["workspace_id"] / source["id"] / "content.txt").read_text(encoding="utf-8")
        block_hash = hashlib.sha256(json.dumps(source["blocks"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if block_hash != item["source_hash"]:
            raise ValueError("Source hash mismatch")
        if "\n".join(text.splitlines()[item["start_line"]-1:item["end_line"]]) != item["quote"]:
            raise ValueError("Historical quote differs from source")
        (papers / (name + ".txt")).write_text(text, encoding="utf-8", newline="\n")
        (papers / (name + ".blocks.json")).write_text(json.dumps(source["blocks"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        texts[source["id"]] = text
        sources.append(dict(id=source["id"], title=title, short_title=name.upper() if name == "qasper" else "ArxivDIGESTables",
            authors=authors, uri="https://arxiv.org/abs/" + arxiv, license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/", source_hash=source["source_hash"],
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            line_count=source["line_count"], file="/demo/papers/" + name + ".txt", blocks_file="/demo/papers/" + name + ".blocks.json"))
        item.update(preview_start_line=preview_start, preview_end_line=preview_end)

    public_calls = []
    for call in calls:
        data = call.get("data") or {}
        public_calls.append({key: call[key] for key in ("id", "name", "arguments", "status", "elapsed_ms")} | dict(
            evidence_ids=[data["id"]] if call["name"] == "sources_read" else [hit["id"] for hit in data.get("hits", [])]))
    correction_source = sources[0]["id"]
    correction = dict(source_id=correction_source, start_line=453, end_line=470,
                      quote="\n".join(texts[correction_source].splitlines()[452:470]),
                      scope="post_run_manual_review", label="事后核对定位，不属于原运行回读")
    paired = json.loads((research_evidence / "review-reread6/paired.json").read_text(encoding="utf-8"))
    scores = json.loads((research_evidence / "review-reread6/scores.json").read_text(encoding="utf-8"))
    result = dict(schema_version=1, recording=dict(run_id=run_id, recorded_at=run["created_at"], model=run["model"],
        tokens_used=run["tokens_used"], question=run["question"], report=report, tool_calls=public_calls),
        sources=sources, evidence=[{key:item[key] for key in ("id", "source_id", "source_hash", "start_line", "end_line", "quote", "preview_start_line", "preview_end_line")} for item in evidence],
        failure=dict(original_text=report["limitations"][-1], correction=correction,
                     diagnosis="把当前已读片段未找到的信息，扩展成了整篇论文没有报告。引用地址有效，仍需检查结论是否由原文支持。"),
        experiment=dict(label="离线实验结果摘要，非当前工作台自动能力", date="2026-11-05", model="qwen3.5-27b",
                        tasks=6, summaries={method:dict(answer_token_f1=value["answer_token_f1"], tokens_used=value["tokens_used"]) for method,value in scores["summaries"].items()},
                        paired=paired, note="增益来自 1 题，其余 5 题不变；不支持稳定或显著优势结论。"))
    (output / "recording.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    connection.close()
    print("Exported one historical run, two canonical paper texts and a separate research summary.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--research-evidence", type=Path, required=True)
    args = parser.parse_args()
    export(args.store, args.run_id, args.out, args.research_evidence)
