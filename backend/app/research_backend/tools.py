"""Workspace-scoped tool bus, shared by HTTP, agent execution and evaluation."""
import asyncio
import json
import logging
import sqlite3
import time
from contextlib import closing
from pydantic import ValidationError
from .schemas import DatabaseRequest, DiscoveryRequest, GrepToolRequest, ReadRequest, RetrievalRequest, SearchRequest, CollectRequest, ResearchPlanRequest
from .retrieval import retrieve
from .store import NotFound, Unavailable

logger = logging.getLogger(__name__)


def source_card(source):
    return {key: source[key] for key in ("id", "workspace_id", "title", "kind", "uri", "version",
            "source_hash", "line_count", "rag_indexed", "created_at")}


def query_database(store, owner, workspace_id, request):
    """Execute SELECT against a disposable, workspace-only projection, never the app DB."""
    store.workspace(owner, workspace_id)
    if not request.sql.lstrip().upper().startswith(("SELECT", "WITH")):
        raise ValueError("仅支持 SELECT 或 WITH 查询")
    sources = store.list(owner, workspace_id, "source", limit=1001)
    evidence = store.list(owner, workspace_id, "evidence", limit=5001)
    if len(sources) > 1000 or len(evidence) > 5000:
        raise ValueError("工作区数据超过当前查询投影上限，请缩小工作区范围")
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE sources(id TEXT, title TEXT, kind TEXT, uri TEXT, version TEXT, source_hash TEXT)")
        db.execute("CREATE TABLE evidence(id TEXT, source_id TEXT, quote TEXT, start_line INTEGER, end_line INTEGER)")
        db.executemany("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                       [tuple(s.get(k) for k in ("id", "title", "kind", "uri", "version", "source_hash")) for s in sources])
        db.executemany("INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                       [tuple(e.get(k) for k in ("id", "source_id", "quote", "start_line", "end_line")) for e in evidence])
        db.commit()
        functions = {"abs", "avg", "count", "coalesce", "ifnull", "length", "lower", "max", "min", "round", "sum", "upper"}

        def authorize(action, arg1, arg2, database, trigger):
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and arg1 in {"sources", "evidence"}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in functions:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        deadline = time.monotonic() + 0.3
        db.set_authorizer(authorize)
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            cursor = db.execute(request.sql)
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchmany(request.limit + 1)
        except (sqlite3.Error, sqlite3.Warning) as exc:
            raise ValueError("查询被拒绝、执行超时或 SQL 无效；仅可读取当前工作区 sources/evidence") from exc
        finally:
            db.set_authorizer(None)
            db.set_progress_handler(None, 0)
        return dict(columns=columns, rows=[list(row) for row in rows[:request.limit]],
                    truncated=len(rows) > request.limit, scope="current_workspace_only")


class ToolBus:
    SPECS = {
        "sources_list": ("列出当前工作区的资料，不读取全文", None),
        "sources_grep": ("使用 ripgrep 搜索当前资料，query 默认是正则表达式，可用 | 合并关键词；regex=false 按字面匹配。返回证据 ID 与行号，再用 sources_read 读上下文", GrepToolRequest),
        "sources_read": ("按 source_id 和行号阅读原文，保留页码或视频时间戳", ReadRequest),
        "sources_rag": ("使用已建索引资料的 BM25+向量混合检索，返回可回读的证据", RetrievalRequest),
        "sources_search": ("定位证据后用 sources_read 核实。query 为语义查询；Grep/混合必须显式提供 grep_query（1–3 个具体术语或短语正则），不要用整句问题或宽泛 model|paper。先 source_ids 限定论文；可 start_line/end_line 缩小章节，Grep 用 grep_next_offset 继续。混合执行两路。", SearchRequest),
        "plan_research": ("列出用户要求必读的全部 source_ids 和待核实字段。先列资料再规划，随后按论文批量检索、回读，避免只研究第一篇。计划可以扩充，不能删除已列的论文。", ResearchPlanRequest),
        "papers_search": ("查询 arXiv 文献元数据和摘要；不能当作已阅读全文", DiscoveryRequest),
        "web_search": ("查询公共网页搜索摘要；不能当作原文证据", DiscoveryRequest),
        "papers_collect": ("按规则搜索、去重并下载 arXiv 原生 PDF 到当前课题；返回全文覆盖与失败记录。仅联网任务可用", CollectRequest),
        "database_query": ("只读查询当前工作区 sources/evidence 两张投影表", DatabaseRequest),
    }

    def __init__(self, store, grep, rag, discovery):
        self.store, self.grep, self.rag, self.discovery = store, grep, rag, discovery
        from .discovery import LiteratureCollector
        self.collector = LiteratureCollector(store, discovery, rag)

    def definitions(self, strategy="combined", allow_network=True):
        result = []
        for name, (description, schema) in self.SPECS.items():
            if name in {"sources_grep", "sources_rag"}:
                continue  # Legacy HTTP tools remain available; agents use one controlled search path.
            if name == "sources_rag" and strategy == "grep":
                continue
            if name == "sources_grep" and strategy == "rag":
                continue
            if name in {"papers_search", "web_search", "papers_collect"} and not allow_network:
                continue
            parameters = schema.model_json_schema() if schema else {"type": "object", "properties": {}, "additionalProperties": False}
            if schema is not None and issubclass(schema, RetrievalRequest):
                parameters["properties"].pop("strategy", None)
            if name == "sources_search":
                parameters["properties"]["context_lines"]["default"] = 1
                if strategy in {"grep", "combined"}:
                    parameters.setdefault("required", []).append("grep_query")
                if strategy == "rag":
                    for key in ("grep_query", "start_line", "end_line", "offset"):
                        parameters["properties"].pop(key, None)
            if name == "sources_rag":
                for property_name in ("regex", "ignore_case", "source_ids"):
                    parameters["properties"].pop(property_name, None)
            if name == "sources_grep" and not self.grep.binary:
                parameters["properties"]["regex"]["default"] = False
            result.append(dict(type="function", function=dict(name=name, description=description, parameters=parameters)))
        return result

    async def execute(self, owner, workspace_id, name, arguments, run_id=None, *, retrieval_strategy=None):
        self.store.workspace(owner, workspace_id)
        if name not in self.SPECS:
            raise ValueError("未知工具")
        schema = self.SPECS[name][1]
        effective_arguments = dict(arguments)
        if name == "sources_grep" and not self.grep.binary:
            effective_arguments.setdefault("regex", False)
        if name == "sources_search":
            if retrieval_strategy:
                effective_arguments["strategy"] = retrieval_strategy
            effective_arguments.setdefault("context_lines", 1)
        started = time.monotonic()
        try:
            if name == "sources_search" and retrieval_strategy in {"grep", "combined"} and not arguments.get("grep_query"):
                raise ValueError("请显式提供 grep_query：使用原文中的具体术语或短语，先限定 source_ids；不要自动把自然语言问题拆成宽泛 OR")
            args = schema.model_validate(effective_arguments) if schema else None
            if schema is None and arguments:
                raise ValueError("该工具不接受参数")
            if name == "sources_list":
                sources = self.store.list(owner, workspace_id, "source", limit=101)
                data = dict(sources=[source_card(s) for s in sources[:100]], truncated=len(sources) > 100)
            elif name == "plan_research":
                if not run_id:
                    raise ValueError("研究计划只能在研究运行中保存")
                self.store.get(owner, workspace_id, "run", run_id)
                for source_id in args.source_ids:
                    self.store.get(owner, workspace_id, "source", source_id)
                previous = self.store.list(owner, workspace_id, "research_plan", parent_id=run_id, limit=1)
                if previous and not set(previous[0]["source_ids"]).issubset(args.source_ids):
                    raise ValueError("不能移除已计划的论文；证据不足时在报告中标记本次未核实")
                payload = args.model_dump()
                data = (self.store.update(owner, workspace_id, "research_plan", previous[0]["id"], **payload)
                        if previous else self.store.put(owner, workspace_id, "research_plan", payload, parent_id=run_id))
            elif name == "sources_read":
                data = self.store.evidence(owner, workspace_id, args.source_id,
                                           args.start_line, args.start_line + args.line_count - 1)
            elif name == "sources_grep":
                data = await asyncio.to_thread(self.grep.search, owner, workspace_id, args)
            elif name == "sources_rag":
                data = await asyncio.to_thread(self.rag.search, owner, workspace_id, args)
            elif name == "sources_search":
                data = await asyncio.to_thread(retrieve, self.store, self.grep, self.rag, owner, workspace_id, args)
            elif name == "papers_search":
                data = await self.discovery.papers(args.query, args.limit)
            elif name == "web_search":
                data = await self.discovery.web(args.query, args.limit)
            elif name == "papers_collect":
                data = await self.collector.collect(owner, workspace_id, args)
            else:
                data = await asyncio.to_thread(query_database, self.store, owner, workspace_id, args)
            # Persist the full result. Runtime separately bounds the model-facing view;
            # repeated page/location metadata must never delete retrieved evidence.
            call = self.store.put(owner, workspace_id, "tool_call", dict(name=name, arguments=arguments,
                run_id=run_id, status="succeeded", data=data, error=None,
                elapsed_ms=round((time.monotonic() - started) * 1000)), parent_id=run_id)
            return call
        except Exception as exc:
            expected = isinstance(exc, (Unavailable, NotFound, ValueError, ValidationError))
            error = str(exc) if expected else "工具执行失败；请检查服务配置与服务端日志"
            if not expected:
                logger.exception("Research tool failed: %s", name)
            self.store.put(owner, workspace_id, "tool_call", dict(name=name, arguments=arguments,
                run_id=run_id, status="failed", data=None, error=error,
                elapsed_ms=round((time.monotonic() - started) * 1000)), parent_id=run_id)
            if expected:
                raise
            raise Unavailable(error) from exc
