"""Workspace-isolated data laboratory and translation job resources."""
import asyncio
import importlib.util
import json
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from .datasets import add_dataset, read_dataset
from .lab_runtime import TERMINAL, analysis_markdown
from .lab_schemas import AnalysisCreate, AnalysisView, DatasetView, TranslationCreate, TranslationView
from .schemas import Items, SourceBlock, SourceCreate, SourceView
from .tools import source_card
from .translation import export_translation, prepare_translation


def create_lab_router(store, settings, current_owner, runtime):
    router = APIRouter(prefix="/api/v1/workspaces/{wid}")

    def capacity(owner, wid):
        store.workspace(owner, wid)
        if len(runtime.tasks) >= 10:
            raise HTTPException(429, "分析和翻译任务较多，请等待已有任务完成")
        active = [item for kind in ("analysis", "translation") for item in store.list(owner, wid, kind, descending=True, limit=100) if item["status"] not in TERMINAL]
        if len(active) >= 3:
            raise HTTPException(429, "当前课题已有三个分析或翻译任务，请先等待或取消")

    @router.get("/lab/capabilities", tags=["数据分析与翻译"])
    def capabilities(wid: str, owner: str = Depends(current_owner)):
        store.workspace(owner, wid)
        return dict(statistics=importlib.util.find_spec("scipy") is not None, lstm=importlib.util.find_spec("torch") is not None,
                    translation=bool(settings.llm_api_key), dataset_formats=["csv", "tsv", "xlsx"], max_rows=50000, max_columns=100, lstm_max_points=5000)

    @router.post("/datasets/upload", status_code=201, response_model=DatasetView, tags=["科研数据"])
    async def upload_dataset(wid: str, file: UploadFile = File(...), title: str | None = Form(None, max_length=200),
                             source_id: str | None = Form(None, max_length=64), page: int | None = Form(None, ge=1, le=10000),
                             note: str = Form("", max_length=1000), owner: str = Depends(current_owner)):
        store.workspace(owner, wid)
        content = await file.read(20 * 1024 * 1024 + 1)
        await file.close()
        return await asyncio.to_thread(add_dataset, store, owner, wid, file.filename or "data.csv", content,
                                       title.strip() if title else None, source_id, page, note)

    @router.get("/datasets", response_model=Items[DatasetView], tags=["科研数据"])
    def datasets(wid: str, owner: str = Depends(current_owner)):
        return dict(items=store.list(owner, wid, "dataset", descending=True))

    @router.get("/datasets/{dataset_id}", response_model=DatasetView, tags=["科研数据"])
    def dataset(wid: str, dataset_id: str, owner: str = Depends(current_owner)):
        return store.get(owner, wid, "dataset", dataset_id)

    @router.get("/datasets/{dataset_id}/data", tags=["科研数据"])
    def dataset_data(wid: str, dataset_id: str, owner: str = Depends(current_owner)):
        return read_dataset(store, owner, wid, dataset_id)[1]

    @router.get("/datasets/{dataset_id}/original", tags=["科研数据"])
    def original(wid: str, dataset_id: str, owner: str = Depends(current_owner)):
        item = store.get(owner, wid, "dataset", dataset_id)
        suffix = Path(item["filename"]).suffix.lower()
        return FileResponse(store.root / wid / "datasets" / item["id"] / ("original" + suffix), filename=item["filename"])

    @router.post("/analyses", response_model=AnalysisView, status_code=202, tags=["数据分析"])
    async def create_analysis(wid: str, body: AnalysisCreate, owner: str = Depends(current_owner)):
        capacity(owner, wid)
        item = store.get(owner, wid, "dataset", body.dataset_id)
        available = {column["name"]: column for column in item["columns"]}
        if any(column not in available for column in body.columns):
            raise ValueError("分析列不存在")
        if body.method != "chi_square" and any(available[column]["kind"] != "numeric" for column in body.columns):
            raise ValueError("该方法需要数值列，请先清理文本单元格")
        if body.time_column and body.time_column not in available:
            raise ValueError("时间列不存在")
        if body.weight_column and body.weight_column not in available:
            raise ValueError("频数列不存在")
        job = store.put(owner, wid, "analysis", dict(dataset_id=item["id"], dataset_hash=item["data_hash"], method=body.method,
            parameters=body.model_dump(), status="queued", progress={}, result=None, error=None, published_source_id=None))
        runtime.start(owner, wid, "analysis", job["id"])
        return job

    @router.get("/analyses", response_model=Items[AnalysisView], tags=["数据分析"])
    def analyses(wid: str, owner: str = Depends(current_owner)):
        return dict(items=store.list(owner, wid, "analysis", descending=True))

    @router.get("/analyses/{job_id}", response_model=AnalysisView, tags=["数据分析"])
    def analysis(wid: str, job_id: str, owner: str = Depends(current_owner)):
        return store.get(owner, wid, "analysis", job_id)

    @router.post("/analyses/{job_id}/cancel", response_model=AnalysisView, tags=["数据分析"])
    async def cancel_analysis(wid: str, job_id: str, owner: str = Depends(current_owner)):
        item = store.get(owner, wid, "analysis", job_id)
        if item["status"] not in TERMINAL:
            item = store.update(owner, wid, "analysis", job_id, status="cancelled", error="用户取消任务")
            runtime.stop(job_id)
        return item

    @router.get("/analyses/{job_id}/export", tags=["数据分析"])
    def export_analysis(wid: str, job_id: str, format: str = Query("markdown", pattern="^(markdown|json)$"), owner: str = Depends(current_owner)):
        item = store.get(owner, wid, "analysis", job_id)
        if item["status"] != "completed":
            raise HTTPException(409, "分析尚未完成")
        dataset = store.get(owner, wid, "dataset", item["dataset_id"])
        content = analysis_markdown(item, dataset) if format == "markdown" else json.dumps(item, ensure_ascii=False, indent=2)
        return Response(content, media_type="text/markdown" if format == "markdown" else "application/json")

    @router.get("/analyses/{job_id}/artifacts/model", tags=["数据分析"])
    def model_artifact(wid: str, job_id: str, owner: str = Depends(current_owner)):
        item = store.get(owner, wid, "analysis", job_id)
        path = store.root / wid / "analyses" / item["id"] / "model.pt"
        if item["status"] != "completed" or item["method"] != "lstm" or not path.exists():
            raise HTTPException(404, "没有可下载的模型权重")
        return FileResponse(path, filename=f"lstm-{job_id}.pt")

    @router.post("/analyses/{job_id}/publish", response_model=SourceView, tags=["数据分析"])
    async def publish_analysis(wid: str, job_id: str, owner: str = Depends(current_owner)):
        item = store.get(owner, wid, "analysis", job_id)
        if item["status"] != "completed":
            raise HTTPException(409, "请等待分析完成后再添加到课题资料")
        if item.get("published_source_id"):
            return source_card(store.get(owner, wid, "source", item["published_source_id"]))
        dataset = store.get(owner, wid, "dataset", item["dataset_id"])
        text = analysis_markdown(item, dataset)
        blocks = [SourceBlock(text=text[start:start+45000]) for start in range(0, len(text), 45000)]
        source = store.add_source(owner, wid, SourceCreate(title=f"【数据分析】{dataset['title']} · {item['result']['title']}"[:200],
            kind="document", uri=f"urn:research-analysis:{job_id}", blocks=blocks))
        store.update(owner, wid, "analysis", job_id, published_source_id=source["id"])
        return source_card(source)

    @router.post("/translations", response_model=TranslationView, status_code=202, tags=["文献翻译"])
    async def create_translation(wid: str, body: TranslationCreate, owner: str = Depends(current_owner)):
        capacity(owner, wid)
        job_data = prepare_translation(store, owner, wid, body, settings.llm_model)
        previous = next((item for item in store.list(owner, wid, "translation", descending=True, limit=500)
                         if item["request_hash"] == job_data["request_hash"] and item["status"] in {"completed", "running", "queued"}), None)
        if previous:
            return previous
        job = store.put(owner, wid, "translation", job_data)
        runtime.start(owner, wid, "translation", job["id"])
        return job

    @router.get("/translations", response_model=Items[TranslationView], tags=["文献翻译"])
    def translations(wid: str, owner: str = Depends(current_owner)):
        return dict(items=store.list(owner, wid, "translation", descending=True))

    @router.get("/translations/{job_id}", response_model=TranslationView, tags=["文献翻译"])
    def translation(wid: str, job_id: str, owner: str = Depends(current_owner)):
        return store.get(owner, wid, "translation", job_id)

    @router.post("/translations/{job_id}/cancel", response_model=TranslationView, tags=["文献翻译"])
    async def cancel_translation(wid: str, job_id: str, owner: str = Depends(current_owner)):
        item = store.get(owner, wid, "translation", job_id)
        if item["status"] not in TERMINAL:
            item = store.update(owner, wid, "translation", job_id, status="cancelled", error="用户取消任务；已完成译文保留")
            runtime.stop(job_id)
        return item

    @router.get("/translations/{job_id}/export", tags=["文献翻译"])
    def export(wid: str, job_id: str, owner: str = Depends(current_owner)):
        item = store.get(owner, wid, "translation", job_id)
        if not any(segment["translated"] for segment in item["segments"]):
            raise HTTPException(409, "尚无已完成的译文段落")
        return Response(export_translation(item), media_type="text/markdown")

    return router
