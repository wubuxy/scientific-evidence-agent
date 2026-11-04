"""Separate retrieval evaluation; historical datasets and metrics are never rewritten."""
import argparse
import asyncio
import hashlib
import json
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from .config import Settings, load_environment
from .providers import Discovery
from .retrieval import GrepSearch, create_rag
from .schemas import SourceCreate
from .store import Store
from .tools import ToolBus


@asynccontextmanager
async def temporary_indexes(rag, enabled):
    indexed = []
    try:
        yield indexed
    finally:
        if enabled and indexed:
            await asyncio.to_thread(rag.remove_evaluation_indexes, indexed)


async def evaluate(dataset_path, strategy="grep", limit=10, index_rag=False):
    raw = Path(dataset_path).read_bytes()
    dataset = json.loads(raw)
    with tempfile.TemporaryDirectory(prefix="research-eval-") as directory:
        settings = Settings.from_env()
        store = Store(Path(directory))
        store.initialize()
        workspace = store.create_workspace("evaluation", {"title": "isolated evaluation"})
        wid, ids = workspace["id"], {}
        for document in dataset["documents"]:
            if document["id"] in ids:
                raise ValueError("Duplicate document ID")
            source = SourceCreate.model_validate({k: v for k, v in document.items() if k != "id"})
            ids[document["id"]] = store.add_source("evaluation", wid, source)["id"]
        rag = create_rag(settings, store)
        async with temporary_indexes(rag, strategy != "grep") as indexed:
            if strategy != "grep":
                if not index_rag:
                    raise ValueError("RAG evaluation requires --index-rag; it calls embedding and writes only a research_ collection")
                for source_id in ids.values():
                    indexed.append(source_id)
                    await asyncio.to_thread(rag.index, "evaluation", wid, source_id)
            return await evaluate_cases(dataset, raw, strategy, limit, ids, store, wid, rag, settings)


async def evaluate_cases(dataset, raw, strategy, limit, ids, store, wid, rag, settings):
        bus = ToolBus(store, GrepSearch(store, settings.rg_binary), rag, Discovery(settings))
        records = []
        for case in dataset["cases"]:
            gold = {ids[key] for key in case["gold_source_ids"]}
            if not gold:
                raise ValueError("This evaluator requires at least one gold source per case")
            started = time.monotonic()
            # Use the same controlled route and fusion as HTTP/agents.
            arguments = dict(query=case["query"], strategy=strategy, limit=limit,
                regex=False, context_lines=case.get("context_lines", 0))
            if case.get("grep_query"):
                arguments["grep_query"] = case["grep_query"]
            call = await bus.execute("evaluation", wid, "sources_search", arguments)
            data = call["data"]
            calls = data["backend_runs"]
            unique = data["hits"]
            found = {h["source_id"] for h in unique}
            records.append(dict(id=case["id"], source_recall=len(gold & found) / len(gold),
                source_precision=len(gold & found) / len(found) if found else 0,
                elapsed_ms=round((time.monotonic() - started) * 1000), calls=calls,
                partial=data["partial"], warnings=data["warnings"], merge=data["merge"]))
        if not records:
            raise ValueError("Evaluation cases must not be empty")
        return dict(dataset_sha256=hashlib.sha256(raw).hexdigest(), strategy=strategy, limit=limit,
                    task="retrieval_only_not_report_accuracy", case_count=len(records),
                    mean_source_recall=sum(r["source_recall"] for r in records) / len(records),
                    mean_source_precision=sum(r["source_precision"] for r in records) / len(records), records=records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--strategy", choices=["grep", "rag", "combined"], default="grep")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 21))
    parser.add_argument("--index-rag", action="store_true")
    args = parser.parse_args()
    load_environment()
    report = asyncio.run(evaluate(args.dataset, args.strategy, args.limit, args.index_rag))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "records"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
