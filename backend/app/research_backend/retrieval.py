"""Bounded literal grep and an opt-in adapter to the existing RAG implementation."""
import hashlib
import shutil
import subprocess
import time
import re
from collections import defaultdict
from .schemas import RetrievalRequest
from .store import Unavailable


class GrepSearch:
    def __init__(self, store, binary=""):
        self.store = store
        self.binary = binary or shutil.which("rg")

    def search(self, owner, workspace_id, request):
        if "\n" in request.query or "\r" in request.query:
            raise ValueError("grep 查询必须是单行文本")
        if request.regex and not self.binary:
            raise Unavailable("正则搜索需要 ripgrep；当前可使用 regex=false 的字面匹配")
        sources = self.store.list(owner, workspace_id, "source", limit=101)
        if request.source_ids:
            sources = [self.store.get(owner, workspace_id, "source", sid)
                       for sid in dict.fromkeys(request.source_ids)]
        elif len(sources) > 100:
            raise ValueError("工作区超过 100 份资料，请先指定 source_ids 缩小搜索范围")
        hits, truncated, groups = [], False, []
        started = time.monotonic()
        for source in sources:
            if time.monotonic() - started > 10:
                raise Unavailable("grep 搜索超时，请缩小资料范围")
            if self.binary:
                path = self.store.root / workspace_id / source["id"] / "content.txt"
                # Sources are capped at 10k lines / 500k characters at ingestion.
                # Locate before paginating: early per-file caps hide late sections.
                command = [self.binary, "--json", "--max-count", "10000"]
                if not request.regex:
                    command.append("--fixed-strings")
                if request.ignore_case:
                    command.append("--ignore-case")
                command.extend(["-e", request.query, "--", str(path)])
                try:
                    result = subprocess.run(command, capture_output=True, timeout=3, shell=False)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise Unavailable("ripgrep 无法执行或超时") from exc
                if result.returncode not in (0, 1):
                    if request.regex and b"regex parse error" in result.stderr:
                        raise ValueError("grep 正则表达式无效或使用了不支持的语法")
                    raise Unavailable("ripgrep 搜索失败")
                import json
                matches = [json.loads(line)["data"]["line_number"]
                           for line in result.stdout.decode("utf-8").splitlines()
                           if json.loads(line).get("type") == "match"]
            else:
                text = self.store.source_text(owner, workspace_id, source["id"])
                needle = request.query.casefold() if request.ignore_case else request.query
                matches = []
                for number, line in enumerate(text.splitlines(), 1):
                    if needle in (line.casefold() if request.ignore_case else line):
                        matches.append(number)
            matches = [n for n in matches if request.start_line <= n <= (request.end_line or 10000)]
            lengths = [0]
            if request.context_lines:
                for line in self.store.source_text(owner, workspace_id, source["id"]).splitlines():
                    lengths.append(lengths[-1] + len(line) + 1)
            spans = []
            for number in matches:
                # Adjacent matches represent one reading window, not many slots.
                if (request.context_lines and spans
                    and number - spans[-1][1] <= 2 * request.context_lines
                    and number - spans[-1][0] + 2 * request.context_lines < 80
                    and lengths[number] - lengths[spans[-1][0] - 1] <= 12000):
                    spans[-1] = (spans[-1][0], number)
                else:
                    spans.append((number, number))
            groups.append((source, spans, len(matches)))
        # A frequent term in the first paper must not consume every slot.
        candidates = [(source, spans[rank]) for rank in range(max((len(g[1]) for g in groups), default=0))
                      for source, spans, _ in groups if rank < len(spans)]
        page = candidates[request.offset:request.offset + request.limit]
        truncated = request.offset + len(page) < len(candidates)
        for source, (start, end) in page:
            evidence = self.store.evidence(owner, workspace_id, source["id"], start, end)
            hits.append(dict(evidence, method="grep", score=None))
        return dict(hits=hits, truncated=truncated,
                    backend="ripgrep" if self.binary else "python_literal",
                    match_mode="regex" if request.regex else "literal", total_windows=len(candidates),
                    total_matches=sum(g[2] for g in groups),
                    next_offset=request.offset + len(page) if truncated else None,
                    coverage="指定资料内的文字匹配；不代表语义相关资料已穷尽")


def lexical_pattern(query):
    """Mechanical lexical fallback, never uses evaluation answers or paper names."""
    terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|\d+(?:\.\d+)?|[\u4e00-\u9fff]{2,}", query)
    stop = {"the", "and", "with", "what", "which", "does", "from", "for", "are", "how", "paper"}
    terms = list(dict.fromkeys(t for t in terms if t.lower() not in stop))[:16]
    return "|".join(re.escape(t) for t in terms) or re.escape(query)


def retrieve(store, grep, rag, owner, wid, request):
    """One controlled path for HTTP, agents and experiments; combined always runs both.

    Fuse overlapping evidence windows, rather than concatenating grep before RAG.
    Backend ranks and degradation are public diagnostics, not confidence scores.
    """
    for sid in request.source_ids:
        store.get(owner, wid, "source", sid)
    names = ["grep", "rag"] if request.strategy == "combined" else [request.strategy]
    ranked, diagnostics, warnings = {}, [], []
    fetch = request.limit if request.strategy == "grep" else min(50, max(10, request.limit * 3))
    grep_page = {}
    for name in names:
        started = time.monotonic()
        args = RetrievalRequest(query=request.query, strategy=name, limit=fetch,
            source_ids=request.source_ids, ignore_case=request.ignore_case, regex=request.regex)
        if name == "grep":
            args.query = request.grep_query or request.query
            args.regex = True if request.grep_query else request.regex
            args.start_line, args.end_line, args.offset = request.start_line, request.end_line, request.offset
            args.context_lines = request.context_lines
        try:
            result = (grep if name == "grep" else rag).search(owner, wid, args)
            ranked[name] = result["hits"]
            if name == "grep":
                grep_page = result
            warnings.extend(result.get("warnings", []))
            diagnostics.append(dict(name=name, backend=result.get("backend"), status="succeeded",
                elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                hits=len(result["hits"]), truncated=result.get("truncated", False),
                evidence_ids=[h["id"] for h in result["hits"]],
                **{k: result[k] for k in ("next_offset", "total_windows", "total_matches") if k in result}))
        except Unavailable as exc:
            if request.strategy != "combined":
                raise
            ranked[name] = []
            warnings.append(f"{name}: {exc}")
            diagnostics.append(dict(name=name, status="failed", error=str(exc),
                elapsed_ms=round((time.monotonic() - started) * 1000, 2)))
    clusters = []
    for name, hits in ranked.items():
        for rank, hit in enumerate(hits, 1):
            sid = hit["source_id"]
            a, b = max(1, hit["start_line"] - request.context_lines), hit["end_line"] + request.context_lines
            if name == "grep":
                a, b = max(a, request.start_line), min(b, request.end_line or 10000)
            cluster = next((c for c in clusters if c["source_id"] == sid and
                a <= c["end_line"] and b >= c["start_line"] and
                max(b, c["end_line"]) - min(a, c["start_line"]) < 100), None)
            if cluster is None:
                cluster = dict(source_id=sid, start_line=a, end_line=b, ranks={}, match_lines=[])
                clusters.append(cluster)
            cluster["start_line"], cluster["end_line"] = min(a, cluster["start_line"]), max(b, cluster["end_line"])
            cluster["ranks"].setdefault(name, rank)
            if name == "grep":
                cluster["match_lines"].append(hit["start_line"])
    for c in clusters:
        c["score"] = sum(1 / (60 + r) for r in c["ranks"].values())
    clusters.sort(key=lambda c: (-c["score"], c["source_id"], c["start_line"]))
    # Document balancing is explicit: broad surveys and single-field lookups differ.
    grouped = defaultdict(list)
    for c in clusters:
        grouped[c["source_id"]].append(c)
    balanced = ([items[i] for i in range(max((len(v) for v in grouped.values()), default=0))
                for items in grouped.values() if i < len(items)] if request.balance_sources else clusters)
    hits = []
    for c in balanced[:request.limit]:
        try:
            evidence = store.evidence(owner, wid, c["source_id"], c["start_line"], c["end_line"])
        except ValueError:
            # Oversized source lines: a smaller located window still has a valid back-read address.
            evidence = store.evidence(owner, wid, c["source_id"], c["start_line"], c["start_line"])
        hits.append(dict(evidence, method="rag" if "rag" in c["ranks"] else "grep", score=c["score"],
                         backend_ranks=c["ranks"], match_lines=sorted(set(c["match_lines"]))))
    return dict(hits=hits, warnings=warnings, partial=bool(warnings),
        truncated=len(balanced) > request.limit or any(d.get("truncated") for d in diagnostics),
        backends=[d.get("backend", d["name"]) for d in diagnostics], backend_runs=diagnostics,
        merge="overlap_window_rrf" + ("_document_balanced" if request.balance_sources else ""), strategy=request.strategy,
        grep_next_offset=grep_page.get("next_offset"), grep_total_windows=grep_page.get("total_windows"),
        coverage="仅为检索返回的证据窗口；不得据此声称已读全文或论文未报告")


def create_rag(settings, store):
    if settings.rag_backend == "local":
        from .local_rag import LocalHybridRag
        return LocalHybridRag(settings, store)
    if settings.rag_backend == "legacy":
        try:
            from .legacy_rag import LegacyRag
        except ImportError:
            raise Unavailable("Legacy adapter is not packaged; use RESEARCH_RAG_BACKEND=local") from None
        return LegacyRag(settings, store)
    raise ValueError("RESEARCH_RAG_BACKEND must be local or legacy")
