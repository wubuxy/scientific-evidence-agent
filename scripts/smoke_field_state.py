"""One bounded real-model integration check on synthetic text, not a quality eval.

Uses the configured provider. Output directories must be new; no retries or gold.
Run from repository root with the backend environment.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/app"))
from research_backend.config import Settings
from research_backend.providers import ChatModel
from research_backend.benchmarks.measurement import MeasuredModel
from research_backend.runtime import Runtime
from research_backend.retrieval import GrepSearch
from research_backend.local_rag import LocalHybridRag
from research_backend.store import Store
from research_backend.tools import ToolBus
from research_backend.schemas import RunCreate, SourceCreate


async def main(args):
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=True)
    load_dotenv(args.env_file.parent / ".env")
    settings = Settings.from_env()
    if not settings.llm_api_key:
        raise ValueError("Configure the provider credential in the selected environment file")
    args.out.mkdir(parents=True, exist_ok=False)
    store = Store(args.out / "state"); store.initialize()
    owner = "field-smoke"
    wid = store.create_workspace(owner, dict(title="Synthetic integration fixture"))["id"]
    source = store.add_source(owner, wid, SourceCreate(title="Synthetic Paper A (not a real study)",
        blocks=[dict(text="Synthetic fixture. No real experimental result is claimed.\n"
            "Method: Atlas is initialized from checkpoint Seed-A before training.\n"
            "Training: Atlas is trained on dataset Train-X.\n"
            "Evaluation: We use dataset Eval-Y and the last training checkpoint.\n"
            "This excerpt contains no statement about use of additional training data.")]))
    session = store.put(owner, wid, "session", dict(title="Field controller integration"))
    body = RunCreate(session_id=session["id"], question="只基于提供的合成资料，回答训练数据、训练初始化、额外训练数据。"
        "使用这三个字段名；无法确认时值严格写本次未核实。请阅读原文后引用。",
        field_mode="bounded", max_steps=12, max_tokens=20000, max_seconds=300,
        required_fields=[dict(source_id=source["id"], name=name) for name in ["训练数据", "训练初始化", "额外训练数据"]])
    run = store.put(owner, wid, "run", dict(**body.model_dump(), status="queued", model=settings.llm_model,
        tokens_used=0, tokens_estimated=False, step=0, report=None, error=None))
    store.put(owner, wid, "message", dict(role="user", content=body.question, run_id=run["id"]), parent_id=session["id"])
    measured = MeasuredModel(ChatModel(settings), body.max_tokens)
    runtime = Runtime(store, ToolBus(store, GrepSearch(store, settings.rg_binary), LocalHybridRag(settings,store), None), measured)
    await runtime.execute(owner, wid, run["id"])
    final = store.get(owner,wid,"run",run["id"])
    events = store.events(owner,wid,run["id"])
    (args.out / "run.json").write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding="utf-8")
    (args.out / "trace.json").write_text(json.dumps(dict(events=events,calls=measured.calls),ensure_ascii=False,indent=2),encoding="utf-8")
    state = final.get("field_state",{})
    summary = dict(purpose="synthetic live integration; not accuracy or human annotation", model=settings.llm_model,
        status=final["status"], model_calls=len(measured.calls), tokens=measured.tokens,
        fields=state.get("total"), present=state.get("present"), unresolved=state.get("unresolved"),
        review_status=state.get("review_status"), repair_rounds=state.get("repair_rounds"),
        stop_reason=state.get("stop_reason"), semantic_success=state.get("semantic_success"))
    (args.out / "summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary))
    assert final["status"]=="completed", final.get("error")
    assert state["total"]==3 and state["semantic_success"] is None
    assert state["repair_rounds"]<=1


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    asyncio.run(main(parser.parse_args()))
