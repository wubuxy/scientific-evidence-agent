"""Prepare and run one real-paper research-judgment development case.

Uses two already-public demo papers, not a held-out benchmark. Preparation makes
no model call. Run consumes the existing configured provider, once, with bounded
tokens/time; rubric scores remain null until independently reviewed.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"backend/app"))

QUESTION = """为一个论文研究 Agent 设计评测方案。产品需要回答单篇论文问题、跨论文比较，并提出有依据的研究建议。
仅根据提供的 QASPER 与 ArxivDIGESTables 两篇论文，先分别按指定字段比较，再在 claims 中给出综合判断：
各适合验证哪类能力？为什么两个任务的指标不能直接横向排名？哪些产品能力仍缺评测依据？
提出一个最小可执行的评测组合，说明数据划分、人工检查和失败计数。把论文事实与自己的方案建议明确区分。
重要事实必须引用本次回读的原文。没有证据时明确保留未知，不用论文发表时的结果推断当前最强模型。
不要读取论文外的数据集或声称已经完成新评测。"""
FIELDS = [
    dict(name="任务与输入输出", criterion="说明研究问题、单篇或跨篇范围、输入与输出，避免仅给数据集名称"),
    dict(name="参考答案与标注", criterion="说明参考来自谁、如何收集，以及证据或表格标注的性质"),
    dict(name="指标与比较条件", criterion="说明指标衡量的对象与参考依赖，不跨任务直接比较分数"),
    dict(name="适用范围与缺口", criterion="区分论文已验证的范围与对我们产品的推断，给出原文依据或明确未知"),
]
RUBRIC = [
    "准确区分单篇信息寻求问答与跨论文表格综合的任务、输入和输出。",
    "正确说明各自参考与标注来源，并为关键事实提供支持它的原文。",
    "解释指标对象和评测条件的差异，没有直接用分数给两个系统排名。",
    "识别至少一项现有基准未直接验证的产品能力，并将此判断标为分析而非论文实验结果。",
    "提出包含数据划分、失败计数和人工检查的可执行评测组合。",
    "建议与事实分开，结论有适用条件，未将缺失信息或模型意见当成确定事实。",
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def prepare(directory, memory_mode="legacy", *, task_id="research-judgment-dev-001", question=QUESTION,
            fields=None, rubric=None, strategies=None):
    if memory_mode not in {"legacy", "evidence"}:
        raise ValueError("Unsupported memory mode")
    directory.mkdir(parents=True, exist_ok=False)
    inputs = directory/"inputs"
    inputs.mkdir()
    sources = []
    for name, title, uri in [
        ("qasper", "A Dataset of Information-Seeking Questions and Answers Anchored in Research Papers", "https://arxiv.org/abs/2105.03011v1"),
        ("digestables", "ArxivDIGESTables: Synthesizing Scientific Literature into Tables using Language Models", "https://arxiv.org/abs/2410.22360v1"),
    ]:
        for suffix in (".txt", ".blocks.json"):
            (inputs/(name+suffix)).write_bytes((ROOT/"frontend/public/demo/papers"/(name+suffix)).read_bytes())
        sources.append(dict(id=name, title=title, uri=uri))
    write(inputs/"task.json", dict(id=task_id, question=question, sources=sources, fields=FIELDS if fields is None else fields,
        provenance="Previously observed public demo papers; one development case, not held-out evaluation"))
    write(directory/"rubric.json", dict(status="pending_independent_human_review", scale="0 or 1 after evidence review; null means unreviewed",
        items=[dict(id=f"r{i+1}", criterion=text, score=None, rationale=None) for i,text in enumerate(RUBRIC if rubric is None else rubric)]))
    code = [ROOT/"scripts/run_research_case.py"] + sorted((ROOT/"backend/app/research_backend").glob("*.py")) + [ROOT/"backend/app/research_backend/benchmarks/measurement.py"]
    write(directory/"manifest.json", dict(purpose="single real-paper development case; no accuracy claim",
        model="qwen3.5-27b", max_tokens=30000, max_steps=18, max_seconds=600, field_mode="bounded", max_field_repairs=1,
        memory_mode=memory_mode, retrieval_strategy="grep", allow_network=False,
        **({"strategies": strategies} if strategies is not None else {}),
        temperature=0.1, extra_body=dict(enable_thinking=False), sdk_transport_retries=1, application_result_retries=0,
        input_sha256={str(p.relative_to(directory)).replace("\\","/"):sha(p) for p in sorted(inputs.iterdir())},
        rubric_sha256=sha(directory/"rubric.json"),
        code_sha256={str(p.relative_to(ROOT)).replace("\\","/"):sha(p) for p in code}))
    print(json.dumps(dict(status="prepared", task_count=1, papers=2, required_fields=8, model_calls=0)))


async def run_case(directory, env_file):
    from dotenv import load_dotenv
    from research_backend.config import Settings
    from research_backend.providers import ChatModel
    from research_backend.benchmarks.measurement import MeasuredModel
    from research_backend.runtime import Runtime
    from research_backend.retrieval import GrepSearch
    from research_backend.local_rag import LocalHybridRag
    from research_backend.store import Store
    from research_backend.tools import ToolBus
    from research_backend.schemas import RunCreate, SourceCreate

    manifest = json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    for root, entries in [(directory, manifest["input_sha256"]), (ROOT, manifest["code_sha256"])]:
        if any(sha(root/name) != expected for name,expected in entries.items()):
            raise ValueError("Frozen input or code changed; create a new case directory")
    if sha(directory/"rubric.json") != manifest["rubric_sha256"]:
        raise ValueError("Rubric changed after preparation")
    load_dotenv(env_file, override=True)
    load_dotenv(env_file.parent/".env")
    settings = Settings.from_env()
    if not settings.llm_api_key or settings.llm_model != manifest["model"] or settings.llm_extra_body != manifest["extra_body"]:
        raise ValueError("Provider credential, model or extra-body configuration does not match the prepared case")
    task = json.loads((directory/"inputs/task.json").read_text(encoding="utf-8"))
    out = directory/"run"
    out.mkdir(exist_ok=False)
    store = Store(out/"state"); store.initialize()
    owner = "research-case"
    wid = store.create_workspace(owner, dict(title=task["id"]))["id"]
    sources = []
    for source in task["sources"]:
        blocks = json.loads((directory/"inputs"/(source["id"]+".blocks.json")).read_text(encoding="utf-8"))
        imported = store.add_source(owner, wid, SourceCreate(title=source["title"], uri=source["uri"], kind="paper", blocks=blocks))
        if store.source_text(owner,wid,imported["id"]) != (directory/"inputs"/(source["id"]+".txt")).read_text(encoding="utf-8"):
            raise ValueError("Canonical text changed during import")
        sources.append(imported)
    session = store.put(owner,wid,"session",dict(title=task["id"]))
    body = RunCreate(session_id=session["id"], question=task["question"],
        memory_mode=manifest.get("memory_mode", "legacy"),
        **manifest.get("strategies", {}),
        retrieval_strategy=manifest.get("retrieval_strategy", "grep"), allow_network=manifest.get("allow_network", False),
        **{k:manifest[k] for k in ("max_tokens","max_steps","max_seconds","field_mode","max_field_repairs")},
        required_fields=[dict(source_id=source["id"], **field) for source in sources for field in task["fields"]])
    run = store.put(owner,wid,"run",dict(**body.model_dump(),status="queued",model=settings.llm_model,
        tokens_used=0,tokens_estimated=False,step=0,report=None,error=None))
    store.put(owner,wid,"message",dict(role="user",content=task["question"],run_id=run["id"]),parent_id=session["id"])
    measured = MeasuredModel(ChatModel(settings), body.max_tokens)
    runtime = Runtime(store,ToolBus(store,GrepSearch(store,settings.rg_binary),LocalHybridRag(settings,store),None),measured)
    started=time.monotonic()
    await runtime.execute(owner,wid,run["id"])
    final=store.get(owner,wid,"run",run["id"])
    events, cursor=[],0
    while batch := store.events(owner,wid,run["id"],cursor):
        events.extend(batch); cursor=batch[-1]["id"]
    state=final.get("field_state",{})
    summary=dict(task_id=task["id"],purpose=manifest["purpose"],status=final["status"],configured_model=settings.llm_model,
        model_calls=len(measured.calls),tokens_used=measured.tokens,known_tokens_used=measured.known_tokens,
        elapsed_seconds=round(time.monotonic()-started,2),memory_mode=body.memory_mode,
        fields=state.get("total"),present=state.get("present"),
        unresolved=state.get("unresolved"),repair_rounds=state.get("repair_rounds"),stop_reason=state.get("stop_reason"),
        review_status=state.get("review_status"),semantic_accuracy=None,human_rubric_score=None,
        usage_note="Returned logical usage; failed SDK transport attempts cannot be independently billed from this trace")
    write(out/"run.json",final)
    write(out/"trace.json",dict(calls=measured.calls,events=events))
    tool_calls = store.list(owner,wid,"tool_call",parent_id=run["id"],limit=2000)
    write(out/"tool-calls.json",tool_calls)
    write(out/"report-drafts.json",store.list(owner,wid,"report_draft",parent_id=run["id"],limit=100))
    write(out/"reads.json",[call["data"] for call in tool_calls
        if call["name"] == "sources_read" and call["status"] == "succeeded"])
    write(out/"sources.json",[{k:source[k] for k in ("id","title","kind","uri","version","source_hash","line_count")} for source in sources])
    write(out/"summary.json",summary)
    (out/"report.md").write_text(final.get("markdown") or "No valid report was delivered.\n",encoding="utf-8")
    print(json.dumps(summary))
    return 0 if final["status"]=="completed" else 1


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["prepare","run"])
    parser.add_argument("--case-dir",type=Path,required=True)
    parser.add_argument("--env-file",type=Path)
    parser.add_argument("--memory-mode",choices=["legacy", "evidence"],default="legacy",help="Preparation only; run uses the frozen manifest")
    args=parser.parse_args()
    if args.action=="prepare": prepare(args.case_dir.resolve(), args.memory_mode)
    elif args.env_file is None: parser.error("run requires --env-file")
    else: raise SystemExit(asyncio.run(run_case(args.case_dir.resolve(),args.env_file.resolve())))
