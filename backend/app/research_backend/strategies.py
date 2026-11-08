"""Opt-in, deterministic architecture controls. No answer keys or model calls.

Read guidance is navigation, never a sufficiency gate. Writer selection uses
only already-read text; the durable evidence and citation identity stay intact.
"""
import json

from .local_rag import bm25_scores


def read_guidance(reads, leads, source_cards):
    lengths = {s["id"]: s["line_count"] for s in source_cards}
    coverage = {}
    for item in reads.values():
        coverage.setdefault(item["source_id"], []).append((item["start_line"], item["end_line"]))
    merged = {}
    for sid, intervals in coverage.items():
        merged[sid] = []
        for start, end in sorted(intervals):
            if merged[sid] and start <= merged[sid][-1][1] + 1:
                merged[sid][-1][1] = max(end, merged[sid][-1][1])
            else:
                merged[sid].append([start, end])
    candidates, seen = [], set()
    for item in leads:
        sid = item["source_id"]
        if sid not in lengths:
            continue
        # Position near the hit, rather than blindly restarting at the abstract.
        start = max(1, item["start_line"] - 8)
        end = min(lengths[sid], max(start + 39, item["end_line"] + 8), start + 59)
        if start > end or (sid, start, end) in seen:
            continue
        overlap = sum(max(0, min(end, b) - max(start, a) + 1) for a, b in merged.get(sid, []))
        seen.add((sid, start, end))
        candidates.append(dict(source_id=sid, start_line=start, line_count=end-start+1,
                               previously_read_lines=overlap))
    # Balance papers before limiting the number of navigation suggestions.
    groups = {}
    for candidate in sorted(candidates, key=lambda c: c["previously_read_lines"]):
        groups.setdefault(candidate["source_id"], []).append(candidate)
    ordered = [group[i] for i in range(max(map(len, groups.values()), default=0))
               for group in groups.values() if i < len(group)]
    return "\n回读导航（非证据，非完成判定）：" + json.dumps(dict(
        covered_ranges=merged, suggested_reads=ordered[:8],
        instructions="优先围绕字段缺口选择不同位置。已读范围若被上下文省略，可以再次回读；不要仅为去重跳过必要条件。line_count 上限 100。",
        evidence_sufficiency="not_assessed"), ensure_ascii=False)


def lexical_writer_evidence(evidence, question, requirements):
    """Two eight-line verbatim excerpts per read, with local BM25 ranking.

The fixed bilingual vocabulary bridges Chinese requests and English papers.
It is a heuristic, not a learned selector or a claim of semantic relevance.
Full read IDs refer to canonical stored windows; excerpt spans are narrower.
"""
    query = question + " " + " ".join(f["name"] + " " + f.get("criterion", "") for f in requirements)
    vocabulary = {
        "任务": "task input output question answer", "训练": "training fine tuning pretrained",
        "标注": "annotation annotators reference evidence", "指标": "evaluation metric f1 rouge precision recall",
        "条件": "setting oracle non-oracle gold evidence", "划分": "train development test split",
        "成本": "cost budget tokens", "缺口": "limitation future work",
    }
    query += " " + " ".join(value for key, value in vocabulary.items() if key in query)
    result = {}
    for key, item in evidence.items():
        lines = item["quote"].splitlines()
        offsets = list(range(0, len(lines), 4))
        windows = ["\n".join(lines[i:i+8]) for i in offsets]
        scores = bm25_scores(query, windows)
        selected = []
        for index in sorted(range(len(windows)), key=lambda i: (-scores[i], i)):
            offset = offsets[index]
            if any(abs(offset-old) < 8 for old in selected):
                continue
            selected.append(offset)
            if len(selected) == 2:
                break
        for offset in sorted(selected):
            result[f"{key}:{offset}"] = dict(item, start_line=item["start_line"]+offset,
                end_line=item["start_line"]+min(offset+8, len(lines))-1,
                quote="\n".join(lines[offset:offset+8]))
    return result
