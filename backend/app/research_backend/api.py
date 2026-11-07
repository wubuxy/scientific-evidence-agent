"""Versioned scientific API. Create the app without importing legacy app_main."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import ValidationError
from .config import Settings, load_environment
from .ingest import parse_upload
from .providers import ChatModel, Discovery
from .retrieval import GrepSearch, create_rag, retrieve
from .runtime import Runtime, TERMINAL
from .schemas import (DatabaseRequest, DiscoveryRequest, MessageCreate, ReadRequest, RetrievalRequest,
                      RunCreate, SessionCreate, SourceCreate, ToolRequest, WorkspaceCreate,
                      WorkspaceView, SessionView, MessageView, SourceView, EvidenceView, RetrievalView,
                      ToolCallView, RunView, ReportView, Items, SearchRequest, CollectRequest)
from .store import NotFound, Store, Unavailable
from .tools import ToolBus, source_card
from .lab_api import create_lab_router
from .lab_runtime import LabRuntime
from .translation import Translator


def create_app(settings: Optional[Settings] = None, *, model=None, discovery=None, rag=None, translator=None):
    if settings is None:
        load_environment()
        settings = Settings.from_env()
    store = Store(settings.data_dir)
    grep = GrepSearch(store, settings.rg_binary)
    rag = rag or create_rag(settings, store)
    bus = ToolBus(store, grep, rag, discovery or Discovery(settings))
    runtime = Runtime(store, bus, model or ChatModel(settings))
    lab_runtime = LabRuntime(store, translator or Translator(settings))

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        yield
        await runtime.close()
        await lab_runtime.close()

    app = FastAPI(title="科研证据助手 API", version="0.1.0", lifespan=lifespan,
        description="面向 AI/NLP 研究生：组会调研、跨论文实验对齐与结论核验。旧金融 API 与评测独立保留。")
    app.state.store, app.state.tools, app.state.runtime = store, bus, runtime
    app.state.lab_runtime = lab_runtime
    bearer = HTTPBearer(auto_error=False)

    def current_owner(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
        if len(settings.jwt_secret) < 32 or settings.jwt_secret.startswith("your-"):
            raise HTTPException(503, "请配置至少 32 字符的 JWT_SECRET_KEY，不能使用示例密钥")
        if credentials is None:
            raise HTTPException(401, "需要 Bearer JWT", headers={"WWW-Authenticate": "Bearer"})
        try:
            claims = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
                                options={"require_exp": True, "require_sub": True})
            subject = claims["sub"]
            if not isinstance(subject, str) or not subject.strip() or len(subject) > 200:
                raise ValueError("invalid subject")
            return subject
        except (JWTError, ValueError, KeyError):
            raise HTTPException(401, "Token 无效或已过期", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(NotFound)
    async def missing(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Unavailable)
    async def unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def bad_tool_arguments(request, exc):
        return JSONResponse(status_code=422, content={"detail": "工具参数不符合接口定义"})

    @app.get("/health", tags=["服务状态"])
    def health():
        return dict(status="ok", version="0.1.0", scope="scientific_research",
                    grep_backend="ripgrep" if grep.binary else "python_literal",
                    rag_enabled=settings.rag_enabled, model_configured=bool(settings.llm_api_key))

    router = APIRouter(prefix="/api/v1")

    @router.get("/tools", tags=["工具能力"])
    def tools(owner: str = Depends(current_owner)):
        return dict(tools=bus.definitions(), network_opt_in_for_runs=True,
                    database_tables=["sources", "evidence"], raw_shell_exposed=False)

    @router.post("/workspaces", status_code=201, response_model=WorkspaceView, tags=["科研工作区"])
    def create_workspace(body: WorkspaceCreate, owner: str = Depends(current_owner)):
        return store.create_workspace(owner, body.model_dump())

    @router.get("/workspaces", response_model=Items[WorkspaceView], tags=["科研工作区"])
    def list_workspaces(owner: str = Depends(current_owner)):
        return dict(items=store.workspaces(owner))

    @router.get("/workspaces/{wid}", response_model=WorkspaceView, tags=["科研工作区"])
    def get_workspace(wid: str, owner: str = Depends(current_owner)):
        return store.workspace(owner, wid)

    @router.post("/workspaces/{wid}/sessions", status_code=201, response_model=SessionView, tags=["对话记录"])
    def create_session(wid: str, body: SessionCreate, owner: str = Depends(current_owner)):
        return store.put(owner, wid, "session", body.model_dump())

    @router.get("/workspaces/{wid}/sessions", response_model=Items[SessionView], tags=["对话记录"])
    def list_sessions(wid: str, owner: str = Depends(current_owner)):
        return dict(items=store.list(owner, wid, "session"))

    @router.get("/workspaces/{wid}/sessions/{sid}/messages", response_model=Items[MessageView], tags=["对话记录"])
    def list_messages(wid: str, sid: str, limit: int = Query(100, ge=1, le=500),
                      offset: int = Query(0, ge=0), owner: str = Depends(current_owner)):
        store.get(owner, wid, "session", sid)
        return dict(items=store.list(owner, wid, "message", parent_id=sid, limit=limit, offset=offset))

    @router.post("/workspaces/{wid}/sessions/{sid}/messages", status_code=201, response_model=MessageView, tags=["对话记录"])
    def add_message(wid: str, sid: str, body: MessageCreate, owner: str = Depends(current_owner)):
        store.get(owner, wid, "session", sid)
        return store.put(owner, wid, "message", dict(role="user", content=body.content), parent_id=sid)

    @router.post("/workspaces/{wid}/sources", status_code=201, response_model=SourceView, tags=["资料与原文"])
    def add_source(wid: str, body: SourceCreate, owner: str = Depends(current_owner)):
        return source_card(store.add_source(owner, wid, body))

    @router.post("/workspaces/{wid}/sources/upload", status_code=201, response_model=SourceView, tags=["资料与原文"])
    async def upload_source(wid: str, file: UploadFile = File(...),
                            title: Optional[str] = Form(None, max_length=200),
                            uri: Optional[str] = Form(None, max_length=2000),
                            version: Optional[str] = Form(None, max_length=100),
                            owner: str = Depends(current_owner)):
        store.workspace(owner, wid)
        content = await file.read(20 * 1024 * 1024 + 1)
        await file.close()
        try:
            source, extension = await asyncio.to_thread(parse_upload, file.filename or "", content)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("无法解析文件，请检查格式、编码或加密状态") from exc
        metadata = source.model_dump()
        metadata.update({key: value for key, value in {"title": title, "uri": uri, "version": version}.items() if value is not None})
        source = SourceCreate.model_validate(metadata)
        return source_card(store.add_source(owner, wid, source, content, extension))

    @router.get("/workspaces/{wid}/sources", response_model=Items[SourceView], tags=["资料与原文"])
    def list_sources(wid: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                     owner: str = Depends(current_owner)):
        return dict(items=[source_card(s) for s in store.list(owner, wid, "source", limit=limit, offset=offset)])

    @router.get("/workspaces/{wid}/sources/{source_id}", response_model=SourceView, tags=["资料与原文"])
    def get_source(wid: str, source_id: str, owner: str = Depends(current_owner)):
        return source_card(store.get(owner, wid, "source", source_id))

    @router.get("/workspaces/{wid}/sources/{source_id}/original", tags=["资料与原文"])
    def original(wid: str, source_id: str, owner: str = Depends(current_owner)):
        source = store.get(owner, wid, "source", source_id)
        extension = source.get("original_extension")
        if not extension:
            raise NotFound("该资料未上传原始文件")
        return FileResponse(store.root / wid / source_id / ("original" + extension),
                            filename=source["title"], media_type="application/octet-stream")

    @router.post("/workspaces/{wid}/sources/read", response_model=ToolCallView, tags=["资料与原文"])
    async def read_source(wid: str, body: ReadRequest, owner: str = Depends(current_owner)):
        return await bus.execute(owner, wid, "sources_read", body.model_dump())

    @router.post("/workspaces/{wid}/sources/{source_id}/indexes/rag", tags=["检索"])
    async def index_rag(wid: str, source_id: str, owner: str = Depends(current_owner)):
        store.get(owner, wid, "source", source_id)
        try:
            return await asyncio.to_thread(rag.index, owner, wid, source_id)
        except (ValueError, Unavailable):
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("RAG indexing failed")
            raise Unavailable("RAG 索引失败；原文和 grep 不受影响") from exc

    @router.post("/workspaces/{wid}/retrieval/search", response_model=RetrievalView, tags=["检索"])
    async def search(wid: str, body: SearchRequest, owner: str = Depends(current_owner)):
        return (await bus.execute(owner, wid, "sources_search", body.model_dump()))["data"]

    @router.post("/workspaces/{wid}/discovery/papers/search", response_model=ToolCallView, tags=["发现文献与网页"])
    async def papers(wid: str, body: DiscoveryRequest, owner: str = Depends(current_owner)):
        return await bus.execute(owner, wid, "papers_search", body.model_dump())

    @router.post("/workspaces/{wid}/discovery/web/search", response_model=ToolCallView, tags=["发现文献与网页"])
    async def web(wid: str, body: DiscoveryRequest, owner: str = Depends(current_owner)):
        return await bus.execute(owner, wid, "web_search", body.model_dump())

    @router.post("/workspaces/{wid}/discovery/collect", response_model=ToolCallView, tags=["发现文献与网页"])
    async def collect(wid: str, body: CollectRequest, owner: str = Depends(current_owner)):
        return await bus.execute(owner, wid, "papers_collect", body.model_dump())

    @router.get("/workspaces/{wid}/discovery/jobs", response_model=Items, tags=["发现文献与网页"])
    def discovery_jobs(wid: str, owner: str = Depends(current_owner)):
        return dict(items=store.list(owner, wid, "discovery_job"))

    @router.get("/workspaces/{wid}/database/schema", tags=["科研数据查询"])
    def database_schema(wid: str, owner: str = Depends(current_owner)):
        store.workspace(owner, wid)
        return dict(scope="current_workspace_only", tables={
            "sources": ["id", "title", "kind", "uri", "version", "source_hash"],
            "evidence": ["id", "source_id", "quote", "start_line", "end_line"]})

    @router.post("/workspaces/{wid}/database/query", response_model=ToolCallView, tags=["科研数据查询"])
    async def database_query(wid: str, body: DatabaseRequest, owner: str = Depends(current_owner)):
        return await bus.execute(owner, wid, "database_query", body.model_dump())

    @router.post("/workspaces/{wid}/tools/calls", response_model=ToolCallView, tags=["工具能力"])
    async def invoke_tool(wid: str, body: ToolRequest, owner: str = Depends(current_owner)):
        return await bus.execute(owner, wid, body.name, body.arguments)

    @router.get("/workspaces/{wid}/tools/calls", response_model=Items[ToolCallView], tags=["工具能力"])
    def tool_calls(wid: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   owner: str = Depends(current_owner)):
        return dict(items=store.list(owner, wid, "tool_call", limit=limit, offset=offset))

    @router.get("/workspaces/{wid}/tools/calls/{call_id}", response_model=ToolCallView, tags=["工具能力"])
    def tool_call(wid: str, call_id: str, owner: str = Depends(current_owner)):
        return store.get(owner, wid, "tool_call", call_id)

    @router.get("/workspaces/{wid}/evidence", response_model=Items[EvidenceView], tags=["证据"])
    def list_evidence(wid: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                      owner: str = Depends(current_owner)):
        return dict(items=store.list(owner, wid, "evidence", limit=limit, offset=offset))

    @router.get("/workspaces/{wid}/evidence/{evidence_id}", response_model=EvidenceView, tags=["证据"])
    def get_evidence(wid: str, evidence_id: str, owner: str = Depends(current_owner)):
        return store.get(owner, wid, "evidence", evidence_id)

    @router.post("/workspaces/{wid}/runs", status_code=202, response_model=RunView, tags=["研究任务与报告"])
    async def create_run(wid: str, body: RunCreate, owner: str = Depends(current_owner)):
        store.get(owner, wid, "session", body.session_id)
        for field in body.required_fields:
            store.get(owner, wid, "source", field.source_id)
        if model is None and not settings.llm_api_key:
            raise Unavailable("请先配置模型 API key")
        run = store.put(owner, wid, "run", dict(**body.model_dump(), status="queued",
            model=settings.llm_model, tokens_used=0, tokens_estimated=False, step=0, report=None, error=None))
        store.put(owner, wid, "message", dict(role="user", content=body.question, run_id=run["id"]),
                  parent_id=body.session_id)
        runtime.start(owner, wid, run["id"])
        return run

    @router.get("/workspaces/{wid}/runs", response_model=Items[RunView], tags=["研究任务与报告"])
    def list_runs(wid: str, owner: str = Depends(current_owner)):
        return dict(items=store.list(owner, wid, "run"))

    @router.get("/workspaces/{wid}/runs/{run_id}", response_model=RunView, tags=["研究任务与报告"])
    def get_run(wid: str, run_id: str, owner: str = Depends(current_owner)):
        return store.get(owner, wid, "run", run_id)

    @router.get("/workspaces/{wid}/runs/{run_id}/memory", tags=["研究任务与报告"])
    def run_memory(wid: str, run_id: str, owner: str = Depends(current_owner)):
        store.get(owner, wid, "run", run_id)
        records = store.list(owner, wid, "research_memory", parent_id=run_id, limit=1)
        return records[0] if records else dict(mode="unavailable", evidence=[], sources=[])

    @router.get("/workspaces/{wid}/runs/{run_id}/field-reviews", tags=["研究任务与报告"])
    def field_reviews(wid: str, run_id: str, owner: str = Depends(current_owner)):
        store.get(owner, wid, "run", run_id)
        return dict(items=store.list(owner, wid, "field_review", parent_id=run_id, limit=10))

    @router.post("/workspaces/{wid}/runs/{run_id}/cancel", response_model=RunView, tags=["研究任务与报告"])
    async def cancel_run(wid: str, run_id: str, owner: str = Depends(current_owner)):
        run = store.get(owner, wid, "run", run_id)
        if run["status"] in TERMINAL:
            return run
        run = store.update(owner, wid, "run", run_id, status="cancelled")
        task = runtime.tasks.get(run_id)
        if task:
            task.cancel()
        store.event(owner, wid, run_id, "run_cancelled", {})
        return run

    @router.get("/workspaces/{wid}/runs/{run_id}/events", tags=["研究任务与报告"])
    def events(wid: str, run_id: str, after: int = Query(0, ge=0), owner: str = Depends(current_owner)):
        return dict(items=store.events(owner, wid, run_id, after))

    @router.get("/workspaces/{wid}/runs/{run_id}/events/stream", tags=["研究任务与报告"])
    async def stream(wid: str, run_id: str, after: int = Query(0, ge=0),
                     last_event_id: Optional[str] = Header(None), owner: str = Depends(current_owner)):
        store.get(owner, wid, "run", run_id)
        if last_event_id is not None:
            try:
                after = max(after, int(last_event_id))
            except ValueError:
                raise HTTPException(422, "Last-Event-ID 必须为整数")

        async def generate():
            cursor, idle = after, 0
            yield "retry: 1000\n\n"
            while True:
                batch = store.events(owner, wid, run_id, cursor)
                for event in batch:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if len(batch) == 100:
                    continue
                if store.get(owner, wid, "run", run_id)["status"] in TERMINAL:
                    yield "event: done\ndata: {}\n\n"
                    return
                idle += 1
                if idle % 25 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.2)
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/workspaces/{wid}/runs/{run_id}/report", response_model=ReportView, tags=["研究任务与报告"])
    def report(wid: str, run_id: str, owner: str = Depends(current_owner)):
        run = store.get(owner, wid, "run", run_id)
        if run["status"] != "completed":
            raise HTTPException(409, "任务尚未生成报告")
        return dict(report=run["report"], markdown=run["markdown"])

    @router.get("/workspaces/{wid}/runs/{run_id}/drafts", tags=["研究任务与报告"])
    def report_drafts(wid: str, run_id: str, owner: str = Depends(current_owner)):
        store.get(owner, wid, "run", run_id)
        return dict(items=store.list(owner, wid, "report_draft", parent_id=run_id, limit=100))

    app.include_router(router)
    app.include_router(create_lab_router(store, settings, current_owner, lab_runtime))
    return app
