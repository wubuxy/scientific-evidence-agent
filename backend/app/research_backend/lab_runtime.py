"""Persistent lab jobs. Numerical work runs in terminable worker processes."""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from .datasets import read_dataset
from .translation import token_estimate

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class LabRuntime:
    def __init__(self, store, translator):
        self.store, self.translator = store, translator
        self.tasks = {}
        self.semaphore = asyncio.Semaphore(2)

    def start(self, owner, wid, kind, job_id):
        task = asyncio.create_task(self._run(owner, wid, kind, job_id))
        self.tasks[job_id] = task
        task.add_done_callback(lambda value: self.tasks.pop(job_id, None))

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self, job_id):
        task = self.tasks.get(job_id)
        if task:
            task.cancel()

    async def _run(self, owner, wid, kind, job_id):
        try:
            async with self.semaphore:
                job = self.store.get(owner, wid, kind, job_id)
                if job["status"] in TERMINAL:
                    return
                self.store.update(owner, wid, kind, job_id, status="running")
                if kind == "analysis":
                    await self._analysis(owner, wid, job)
                else:
                    await asyncio.wait_for(self._translation(owner, wid, job), timeout=600)
        except asyncio.CancelledError:
            job = self.store.get(owner, wid, kind, job_id)
            if job["status"] not in TERMINAL:
                self.store.update(owner, wid, kind, job_id, status="interrupted", error="执行进程停止，请重新发起")
            raise
        except Exception as exc:
            logger.exception("Lab job failed: %s", job_id)
            job = self.store.get(owner, wid, kind, job_id)
            if job["status"] not in TERMINAL:
                from .store import Unavailable
                message = str(exc) if isinstance(exc, (ValueError, Unavailable)) else "任务执行失败或超过时间限制，请检查日志后重试"
                self.store.update(owner, wid, kind, job_id, status="failed", error=message)

    async def _analysis(self, owner, wid, job):
        dataset, data = await asyncio.to_thread(read_dataset, self.store, owner, wid, job["dataset_id"])
        if dataset["data_hash"] != job["dataset_hash"]:
            raise ValueError("分析数据版本与任务不一致")
        directory = self.store.root / wid / "analyses" / job["id"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "input.json").write_text(json.dumps(dict(data=data, parameters=job["parameters"]), ensure_ascii=False, allow_nan=False), encoding="utf-8")
        process = None
        with (directory / "worker.log").open("wb") as log:
            try:
                process = await asyncio.create_subprocess_exec(sys.executable, "-m", "research_backend.analysis_worker", str(directory),
                    cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=log)
                waiter = asyncio.create_task(process.wait())
                deadline, previous = time.monotonic() + 180, None
                while process.returncode is None:
                    try:
                        await asyncio.wait_for(asyncio.shield(waiter), timeout=.3)
                    except asyncio.TimeoutError:
                        pass
                    if time.monotonic() > deadline:
                        raise ValueError("分析超过 180 秒限制，请减少数据量、窗口或训练轮数")
                    progress_path = directory / "progress.json"
                    if progress_path.exists():
                        try:
                            progress = json.loads(progress_path.read_text(encoding="utf-8"))
                            if progress != previous:
                                self.store.update(owner, wid, "analysis", job["id"], progress=progress)
                                previous = progress
                        except (OSError, json.JSONDecodeError):
                            pass
                if not (directory / "output.json").exists():
                    raise ValueError("分析进程未返回结果，请检查分析依赖或服务端日志")
                output = json.loads((directory / "output.json").read_text(encoding="utf-8"))
                if output.get("error"):
                    raise ValueError(output["error"])
                if self.store.get(owner, wid, "analysis", job["id"])["status"] not in TERMINAL:
                    self.store.update(owner, wid, "analysis", job["id"], status="completed", result=output["result"], progress=dict(completed=True))
            finally:
                if process and process.returncode is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    await process.wait()

    async def _translation(self, owner, wid, job):
        used, estimated = 0, False
        segments = job["segments"]
        for index, segment in enumerate(segments):
            original = segment["original"]
            prompt_estimate = token_estimate(original + json.dumps(job["glossary"], ensure_ascii=False)) + 500
            available = min(4096, job["max_tokens"] - used - prompt_estimate)
            if available < max(500, len(original)):
                raise ValueError("翻译达到 Token 预算；已完成段落仍保留，可缩小范围后重新发起")
            response = await self.translator.translate(original, job["target_language"], job["glossary"], available,
                                                       style=job.get("style", "academic"))
            if not response.get("text"):
                raise ValueError("模型未返回有效译文")
            segment["translated"] = response["text"]
            cost = response.get("tokens")
            if cost is None:
                cost = prompt_estimate + token_estimate(response["text"])
                estimated = True
            used += int(cost)
            current = self.store.get(owner, wid, "translation", job["id"])
            if current["status"] in TERMINAL:
                return
            self.store.update(owner, wid, "translation", job["id"], segments=segments, tokens_used=used, tokens_estimated=estimated,
                              progress=dict(completed=index + 1, total=len(segments)))
            if used > job["max_tokens"]:
                raise ValueError("实际 Token 使用超过预算，已停止后续翻译；已完成段落仍保留")
        self.store.update(owner, wid, "translation", job["id"], status="completed")


def analysis_markdown(job, dataset):
    result = job.get("result") or {}
    lines = [f"# 数据分析记录：{result.get('title', job['method'])}", "", f"数据：{dataset['title']}（{dataset['row_count']} 行）",
             f"规范化数据 SHA-256：{job['dataset_hash']}", f"原始文件 SHA-256：{dataset['original_hash']}",
             f"任务 ID：{job['id']}", f"文献关联：{dataset.get('source_id') or '独立上传'}；页码：{dataset.get('page') or '未指定'}",
             "文献关联由上传者指定，未自动验证表格提取是否正确。", "", "## 分析说明", result.get("interpretation", ""),
             "", "## 参数", "```json", json.dumps(job["parameters"], ensure_ascii=False, indent=2), "```", "", "## 完整数值结果", "```json",
             json.dumps(result, ensure_ascii=False, indent=2), "```", "", "分析是基于上传数据的计算结果，不代表已复现论文或证明因果关系。"]
    return "\n".join(lines)
