"""Bounded arXiv discovery -> deduplication -> native PDF ingestion -> optional index.

Search snippets are leads. Only downloaded, parsed originals become paper sources.
No arbitrary URL fetch and no automatic inclusion claim based on search relevance.
"""
import asyncio
import hashlib
import re
from urllib.parse import urlparse
import httpx
from .ingest import parse_upload
from .store import Unavailable


def arxiv_id(value):
    value = value.strip()
    if re.fullmatch(r"\d{4}\.\d{4,5}(?:v[1-9]\d*)?", value):
        return value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.username or parsed.password or port not in {None, 80, 443}:
        return None
    match = re.fullmatch(r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v[1-9]\d*)?)(?:\.pdf)?/?", parsed.path)
    return match[1] if match else None


def query_variants(query, english_queries=()):
    # A small transparent vocabulary, not model-generated paper recommendations.
    glossary = {"自反思": '"self-reflective"', "检索增强生成": '"retrieval augmented generation"',
        "纠错": "corrective", "大语言模型": "large language model", "文献综述": "literature review",
        "科研问答": "scientific question answering", "评估": "evaluation"}
    rewritten = query
    for word, english in glossary.items():
        rewritten = rewritten.replace(word, " " + english + " ")
    english = " ".join(re.findall(r'"[^"\u4e00-\u9fff]+"|[A-Za-z0-9][A-Za-z0-9_.-]*', rewritten))
    variants = [q.strip()[:500] for q in english_queries if q.strip()]
    if english:
        variants.append(english)
    variants.append(query)
    return list(dict.fromkeys(variants))[:4]


class LiteratureCollector:
    def __init__(self, store, discovery, rag):
        self.store, self.discovery, self.rag = store, discovery, rag
        self._lock = asyncio.Lock()

    async def download(self, paper_id):
        if arxiv_id(paper_id) != paper_id:
            raise ValueError("仅支持规范化的现代 arXiv ID")
        url = f"https://arxiv.org/pdf/{paper_id}"
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=40, follow_redirects=False,
                        headers={"User-Agent": "ResearchWorkbench/0.2 (bounded academic PDF retrieval)"}) as client:
                    # Validate every redirect before following it.
                    target = url
                    for _ in range(3):
                        async with client.stream("GET", target) as response:
                            if response.is_redirect:
                                target = str(response.url.join(response.headers["location"]))
                                if arxiv_id(target) != paper_id or urlparse(target).path.startswith("/abs/"):
                                    raise ValueError("PDF 重定向不在允许的 arXiv 原文地址内")
                                continue
                            response.raise_for_status()
                            if int(response.headers.get("content-length", 0)) > 20 * 1024 * 1024:
                                raise ValueError("PDF 超过 20 MiB")
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > 20 * 1024 * 1024:
                                    raise ValueError("PDF 超过 20 MiB")
                            if not content.startswith(b"%PDF-"):
                                raise ValueError("下载内容不是 PDF")
                            return bytes(content)
                    raise ValueError("PDF 重定向次数过多")
            except httpx.HTTPError as exc:
                if attempt:
                    raise Unavailable("arXiv PDF 下载失败") from exc
                await asyncio.sleep(1)

    async def collect(self, owner, wid, request):
        self.store.workspace(owner, wid)
        async with self._lock:
            job = self.store.put(owner, wid, "discovery_job", dict(query=request.query,
                variants=query_variants(request.query, request.english_queries), status="running", stages=[], papers=[]))
            stages, candidates, failures, results = [], {}, [], []

            def save(status="running"):
                self.store.update(owner, wid, "discovery_job", job["id"], status=status, stages=stages, papers=results)

            try:
                for query in job["variants"]:
                    try:
                        response = await self.discovery.papers(query, min(10, request.limit * 2))
                        for item in response.get("results", []):
                            pid = arxiv_id(item.get("uri", ""))
                            if pid:
                                candidates.setdefault(pid, dict(item, arxiv_id=pid))
                        stages.append(dict(stage="arxiv_search", query=query, count=len(response.get("results", []))))
                    except Unavailable as exc:
                        failures.append(str(exc))
                        stages.append(dict(stage="arxiv_search", query=query, error=str(exc)))
                    save()
                    if len(candidates) >= request.limit:
                        break
                if len(candidates) < request.limit:
                    try:
                        response = await self.discovery.web(job["variants"][0] + " site:arxiv.org", min(10, request.limit * 2))
                        for item in response.get("results", []):
                            pid = arxiv_id(item.get("uri", ""))
                            if pid:
                                candidates.setdefault(pid, dict(item, arxiv_id=pid))
                        stages.append(dict(stage="web_fallback", count=len(response.get("results", []))))
                    except Unavailable as exc:
                        failures.append(str(exc))
                        stages.append(dict(stage="web_fallback", error=str(exc)))
                existing = self.store.list(owner, wid, "source", limit=1001)
                if len(existing) > 1000:
                    raise ValueError("工作区资料过多，请先缩小范围")
                known = {arxiv_id(s.get("uri") or ""): s for s in existing
                    if s.get("kind") == "paper" and s.get("original_extension") == ".pdf" and arxiv_id(s.get("uri") or "")}
                # Deduplicate paper families within a collection, preserving versions across jobs.
                families = set()
                for pid, item in candidates.items():
                    family = re.sub(r"v\d+$", "", pid)
                    if family in families:
                        continue
                    families.add(family)
                    row = dict(arxiv_id=pid, title=item.get("title", pid), uri=f"https://arxiv.org/abs/{pid}")
                    results.append(row)
                    try:
                        if pid in known:
                            source = known[pid]
                            row["status"] = "already_present"
                        else:
                            content = await self.download(pid)
                            parsed, ext = await asyncio.to_thread(parse_upload, pid + ".pdf", content)
                            parsed.title, parsed.uri = row["title"][:200], row["uri"]
                            parsed.version = pid if "v" in pid else "latest_at_download"
                            source = self.store.add_source(owner, wid, parsed, content, ext)
                            row.update(status="imported", pdf_sha256=hashlib.sha256(content).hexdigest())
                            known[pid] = source
                        row.update(source_id=source["id"], source_hash=source["source_hash"],
                                   evidence_level="downloaded_native_pdf_text", indexed=source["rag_indexed"])
                        if request.index_rag and not source["rag_indexed"]:
                            try:
                                await asyncio.to_thread(self.rag.index, owner, wid, source["id"])
                                row["indexed"] = True
                            except Exception as exc:
                                row["index_error"] = str(exc) if isinstance(exc, (Unavailable, ValueError)) else "索引失败：" + type(exc).__name__
                    except Exception as exc:
                        message = str(exc) if isinstance(exc, (Unavailable, ValueError, OSError)) else "PDF 解析或导入失败：" + type(exc).__name__
                        row.update(status="failed", error=message, evidence_level="search_metadata_only")
                    save()
                    if len(results) >= request.limit:
                        break
                    await asyncio.sleep(3)
                ok = sum(r.get("status") in {"imported", "already_present"} for r in results)
                status = "completed" if ok == request.limit and not any(r.get("index_error") for r in results) else "partial" if ok else "failed"
                save(status)
                return dict(job_id=job["id"], status=status, variants=job["variants"], stages=stages,
                    papers=results, warnings=failures, imported_or_present=ok,
                    note="仅采集候选，不代表完成系统综述筛选；原生解析不包含 OCR/表格结构恢复。")
            except BaseException:
                save("interrupted")
                raise
