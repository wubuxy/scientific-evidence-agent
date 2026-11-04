"""Regression cases for late evidence, pagination, lossless storage and reading gates."""
import asyncio
import json
import pytest
from research_backend.store import Store
from research_backend.schemas import SourceCreate, SearchRequest, RetrievalRequest, Report
from research_backend.retrieval import GrepSearch, retrieve
from research_backend.tools import ToolBus
from research_backend.runtime import Runtime, model_view, final_context, normalize_report_columns, render_report


@pytest.fixture
def corpus(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    wid = store.create_workspace("alice", {"title": "regression"})["id"]
    def add(text):
        return store.add_source("alice", wid, SourceCreate(title="fixture", blocks=[{"text": text, "page": 1}]))["id"]
    return store, wid, add


def test_grep_pages_reach_late_matches_without_early_file_cap(corpus):
    store, wid, add = corpus
    sid = add("\n".join(f"model setting {i}" for i in range(35)))
    grep = GrepSearch(store)
    found, offset = [], 0
    while True:
        data = grep.search("alice", wid, RetrievalRequest(query="model", source_ids=[sid], limit=3, offset=offset))
        found.extend(h["start_line"] for h in data["hits"])
        if data["next_offset"] is None:
            break
        assert data["next_offset"] > offset
        offset = data["next_offset"]
    assert found == list(range(1, 36))
    assert data["total_matches"] == data["total_windows"] == 35


def test_grep_groups_neighbors_before_limit_and_honors_line_scope(corpus):
    store, wid, add = corpus
    sid = add("hit\n" * 20 + "other\n" * 150 + "hit late evidence")
    args = SearchRequest(query="hit", source_ids=[sid], limit=2, context_lines=2)
    result = retrieve(store, GrepSearch(store), None, "alice", wid, args)
    assert any("late evidence" in h["quote"] for h in result["hits"])
    scoped = retrieve(store, GrepSearch(store), None, "alice", wid,
        SearchRequest(query="hit", source_ids=[sid], start_line=150, end_line=171, context_lines=2))
    assert len(scoped["hits"]) == 1
    assert scoped["hits"][0]["start_line"] >= 150
    assert scoped["hits"][0]["end_line"] <= 171


def test_grep_grouping_does_not_create_oversized_evidence(corpus):
    store, wid, add = corpus
    sid = add(("hit " + "x" * 3000 + "\n") * 15)
    result = GrepSearch(store).search("alice", wid, SearchRequest(query="hit", source_ids=[sid], context_lines=5))
    assert result["hits"] and all(len(h["quote"]) <= 16000 for h in result["hits"])


def test_relevance_order_and_explicit_document_balance_are_distinct(corpus):
    store, wid, add = corpus
    a, b = add("one\nother\nthree"), add("unrelated")
    class Rag:
        def search(self, *args):
            return dict(backend="fixture", hits=[dict(store.evidence("alice", wid, sid, line, line), score=1, method="rag")
                for sid, line in [(a, 1), (a, 3), (b, 1)]])
    for balance, expected in [(False, [a, a]), (True, [a, b])]:
        result = retrieve(store, None, Rag(), "alice", wid,
            SearchRequest(query="one", strategy="rag", limit=2, balance_sources=balance))
        assert [h["source_id"] for h in result["hits"]] == expected


async def test_tool_storage_keeps_hits_despite_large_location_metadata(corpus):
    store, wid, add = corpus
    sid = add("evidence\n" * 80)
    ev = store.evidence("alice", wid, sid, 1, 80)
    class Grep:
        binary = True
        def search(self, *args):
            return dict(hits=[dict(ev, method="grep", score=None)] * 5, backend="fixture", truncated=False)
    bus = ToolBus(store, Grep(), None, None)
    call = await bus.execute("alice", wid, "sources_grep", {"query": "evidence"})
    assert len(json.dumps(call["data"])) > 24000
    assert len(store.get("alice", wid, "tool_call", call["id"])["data"]["hits"]) == 5
    projected = model_view(call["data"])
    assert len(projected["hits"]) == 5 and "locations" not in projected["hits"][0]
    assert len(json.dumps(projected)) < 9000


async def test_agent_grep_requires_explicit_pattern_instead_of_natural_question_or(corpus):
    store, wid, add = corpus
    add("model introduction")
    bus = ToolBus(store, GrepSearch(store), None, None)
    with pytest.raises(ValueError, match="grep_query"):
        await bus.execute("alice", wid, "sources_search", {"query": "What model does this paper use?"}, retrieval_strategy="grep")
    assert store.list("alice", wid, "tool_call")[0]["status"] == "failed"
    definition = next(t for t in bus.definitions("grep", False) if t["function"]["name"] == "sources_search")
    assert "grep_query" in definition["function"]["parameters"]["required"]


async def test_plan_cannot_silently_drop_a_required_paper(corpus):
    store, wid, add = corpus
    a, b = add("first"), add("second")
    run = store.put("alice", wid, "run", {"status": "running"})
    bus = ToolBus(store, GrepSearch(store), None, None)
    await bus.execute("alice", wid, "plan_research", {"source_ids": [a, b], "fields": ["training"]}, run["id"])
    with pytest.raises(ValueError, match="不能移除"):
        await bus.execute("alice", wid, "plan_research", {"source_ids": [a], "fields": ["training"]}, run["id"])


def test_report_cannot_cite_old_or_search_only_evidence(corpus):
    store, wid, add = corpus
    sid = add("Fine-tuned evaluator, frozen generator.")
    ev = store.evidence("alice", wid, sid, 1, 1)
    report = Report(title="fixture", claims=[dict(text="Evaluator is trained", evidence_ids=[ev["id"]])], limitations=["fixture"]).model_dump()
    runtime = Runtime(store, None, None)
    with pytest.raises(ValueError, match="本次运行回读"):
        runtime.validate_report("alice", wid, report, read_ids=set())
    runtime.validate_report("alice", wid, report, read_ids={ev["id"]})


async def test_default_budget_keeps_research_tools_and_marks_missing_paper(corpus):
    store, wid, add = corpus
    a, b = add("training evidence"), add("another paper")
    session = store.put("alice", wid, "session", {"title": "test"})
    run = store.put("alice", wid, "run", dict(status="queued", session_id=session["id"], question="Compare both papers",
        model="fixture", memory_mode="legacy", retrieval_strategy="grep", allow_network=False,
        max_tokens=20000, max_steps=10, max_seconds=30, tokens_used=0, step=0))
    class Model:
        step = 0
        async def complete(self, messages, tools, max_tokens):
            self.step += 1
            if self.step == 1:
                name, args = "plan_research", dict(source_ids=[a, b], fields=["training"])
            elif self.step == 2:
                name, args = "sources_read", dict(source_id=a, line_count=1)
            else:
                assert "sources_search" in {t["function"]["name"] for t in tools}
                ev = store.evidence("alice", wid, a, 1, 1)
                name, args = "finish_report", dict(title="Partial", claims=[dict(text="training evidence", evidence_ids=[ev["id"]])], limitations=["fixture"])
            return dict(tokens=2500, prompt_tokens=2000, completion_tokens=500,
                message=dict(role="assistant", tool_calls=[dict(id=str(self.step), type="function",
                    function=dict(name=name, arguments=json.dumps(args)))]))
    runtime = Runtime(store, ToolBus(store, GrepSearch(store), None, None), Model())
    await runtime.execute("alice", wid, run["id"])
    finished = store.get("alice", wid, "run", run["id"])
    assert finished["status"] == "completed", finished
    assert finished["source_coverage"]["status"] == "partial"
    assert finished["source_coverage"]["unread_source_ids"] == [b]
    assert any(b in note for note in finished["report"]["limitations"])


async def test_smoke_evaluator_uses_current_http_fusion_path():
    from pathlib import Path
    from research_backend.evaluation import evaluate
    result = await evaluate(Path(__file__).parent / "fixtures/research_retrieval_smoke.json")
    assert all(row["merge"] == "overlap_window_rrf" for row in result["records"])
    assert result["records"][-1]["source_recall"] == 0


def test_final_context_keeps_actual_read_windows_across_papers():
    evidence = {}
    for paper in ("A", "B", "C"):
        for n in range(4):
            evidence[f"{paper}{n}"] = dict(id=f"{paper}{n}", source_id=paper,
                start_line=n*40+1, end_line=n*40+40,
                quote=f"{paper} measured count 5,049, frozen generator.\n" + "verbatim material\n"*90)
    messages = final_context("system", "compare three papers", evidence, char_budget=6000)
    data = json.loads(messages[-1]["content"].split("\n", 1)[1])
    assert {e["source_id"] for e in data["evidence"]} == {"A", "B", "C"}
    assert all(e["quote"] in evidence[e["id"]]["quote"] for e in data["evidence"])
    assert data["omitted_windows"] == 0
    assert all(e.get("quote_truncated") for e in data["evidence"])
    assert "5,049" in messages[-1]["content"]


def test_user_requested_columns_survive_strict_report_validation(corpus):
    store, wid, add = corpus
    sid = add("The dataset has 1,585 papers and 5,049 questions.")
    ev = store.evidence("alice", wid, sid, 1, 1)
    raw = dict(title="Counts", claims=[], limitations=["fixture"], comparison_rows=[dict(
        source_id=sid, method="Fixture", paper_count=1585, question_count="5,049", evidence_ids=[ev["id"]])])
    normalized, moved = normalize_report_columns(raw)
    report = Report.model_validate(normalized).model_dump()
    assert raw["comparison_rows"][0]["paper_count"] == 1585
    assert "attributes" not in raw["comparison_rows"][0]
    assert len(moved) == 2
    assert report["comparison_rows"][0]["attributes"] == {"paper_count": "1585", "question_count": "5,049"}
    Runtime(store, None, None).validate_report("alice", wid, report, read_ids={ev["id"]})
    assert "paper_count" in render_report(report) and "5,049" in render_report(report)
    report["comparison_rows"][0]["attributes"]["question_count"] = "5,447"
    with pytest.raises(ValueError, match="5,447"):
        Runtime(store, None, None).validate_report("alice", wid, report, read_ids={ev["id"]})


def test_column_adapter_rejects_nested_data_and_conflicting_values():
    base = dict(title="test", claims=[], limitations=["fixture"], comparison_rows=[dict(
        source_id="p", method="test", evidence_ids=["ev"], training_required={"unexpected": "nested"})])
    normalized, _ = normalize_report_columns(base)
    with pytest.raises(ValueError):
        Report.model_validate(normalized)
    base["comparison_rows"][0].update(training_required="yes", attributes={"training_required": "no"})
    with pytest.raises(ValueError, match="重复"):
        normalize_report_columns(base)
