"""Guard against label leakage, cross-paper evidence, and misleading metrics."""
import json
from pathlib import Path
import pytest
from research_backend.benchmarks.prepare import document, freeze
from research_backend.benchmarks.run import validate_answers, record_pins, run_case
from research_backend.benchmarks.score import score_prediction
from research_backend.config import Settings
from research_backend.local_rag import LocalHybridRag
from research_backend.providers import inline_tool_schemas, decode_tool_arguments
from research_backend.retrieval import GrepSearch, retrieve
from research_backend.runtime import model_view, final_context
from research_backend.schemas import SourceCreate, SearchRequest
from research_backend.store import Store, NotFound


def sample():
    doc = document("a", "A study", [("Methods", ["We use a frozen encoder.", "The sample has five documents."])])
    task = dict(id="case", question="Which encoder?", source_ids=["a"], targets=[dict(id="answer", source_id="a", field="encoder")])
    labels = dict(targets=[dict(id="answer", source_id="a", references=[dict(answer="frozen encoder", unanswerable=False, evidence_paragraph_ids=["p0000"])])])
    return doc, task, labels


def test_freeze_separates_labels_and_refuses_overwrite(tmp_path):
    doc, task, labels = sample()
    labels["private_marker"] = "SCORER_ONLY"
    path = tmp_path / "frozen"
    freeze(path, "fixture", "dev", [(task, labels)], {"a": doc}, 1, 1, {}, {})
    assert "SCORER_ONLY" not in (path / "inputs.json").read_text()
    assert "SCORER_ONLY" in (path / "gold.json").read_text()
    with pytest.raises(FileExistsError):
        freeze(path, "fixture", "dev", [(task, labels)], {"a": doc}, 1, 1, {}, {})


def test_citations_and_pins_require_current_read_from_correct_paper():
    _, task, _ = sample()
    reads = {"ev1": dict(source_id="a-local", quote="We use a frozen encoder.")}
    payload = dict(answers=[dict(target_id="answer", answer="frozen encoder", unanswerable=False, evidence_ids=["ev1"])])
    assert validate_answers(payload, task, reads, {"a": "a-local"})
    with pytest.raises(ValueError, match="different"):
        validate_answers(payload, task, reads, {"a": "other"})
    with pytest.raises(ValueError, match="sources_read"):
        validate_answers(payload, task, {}, {"a": "a-local"})
    ledger = {}
    with pytest.raises(ValueError, match="exact"):
        record_pins(dict(entries=[dict(target_id="answer", evidence_id="ev1", quote="invented")]), task, reads, {"a": "a-local"}, ledger)
    assert not ledger
    record_pins(dict(entries=[dict(target_id="answer", evidence_id="ev1", quote="frozen encoder")]), task, reads, {"a": "a-local"}, ledger)
    assert ledger["answer"]["quote"] == "frozen encoder"


def test_failed_output_is_zero_and_missing_evidence_annotations_are_null():
    doc, task, gold = sample()
    failed = dict(status="failed", method="grep", answers=[], reads=[])
    score = score_prediction(failed, task, gold, {"a": doc})
    assert score["answer_token_f1"] == 0
    assert score["read_paragraph_recall"] == 0
    gold["targets"][0]["references"][0]["evidence_paragraph_ids"] = None
    assert score_prediction(failed, task, gold, {"a": doc})["read_paragraph_recall"] is None


def test_scoring_rejects_fabricated_evidence_text():
    doc, task, gold = sample()
    prediction = dict(status="completed", method="grep", answers=[dict(target_id="answer", answer="frozen encoder", unanswerable=False, evidence_ids=["ev"])],
                      reads=[dict(id="ev", source_id="a", start_line=3, end_line=3, quote="fabricated")])
    score = score_prediction(prediction, task, gold, {"a": doc})
    assert score["answer_token_f1"] == 1
    assert score["cited_paragraph_overlap_f1"] == 0
    assert not score["targets"][0]["citation_ids_valid"]


def test_full_grep_page_reaches_model_and_preview_starts_at_match(tmp_path):
    store = Store(tmp_path); store.initialize()
    wid = store.create_workspace("a", {"title": "fixture"})["id"]
    sid = store.add_source("a", wid, SourceCreate(title="A", blocks=[dict(text="\n".join("needle " + str(i) for i in range(25)))]))["id"]
    page = retrieve(store, GrepSearch(store), None, "a", wid, SearchRequest(query="needle", limit=20))
    delivered = model_view(page)
    assert len(delivered["hits"]) == 20
    assert delivered["grep_next_offset"] == 20
    preview = model_view(dict(hits=[dict(id="e", source_id=sid, start_line=1, end_line=2, quote="x" * 500 + "\nneedle target", match_lines=[2])]))
    assert "needle target" in preview["hits"][0]["quote"]
    assert preview["hits"][0]["preview_start_line"] == 2


def test_final_context_preserves_early_read_and_multiple_papers():
    evidence = {str(i): dict(id=str(i), source_id="a" if i < 3 else "b", start_line=i+1, end_line=i+1,
                            quote="critical first fact " + "body " * 1000) for i in range(6)}
    views = []
    for cap in (3500, 6000):
        messages = final_context("system", "question", evidence, cap)
        data = json.loads(messages[-1]["content"].split("\n", 1)[1])
        assert {e["id"] for e in data["evidence"]} == set(evidence)
        assert all(e["quote"].startswith("critical first fact") for e in data["evidence"])
        views.append({e["id"]: len(e["quote"]) for e in data["evidence"]})
    assert all(views[1][eid] >= views[0][eid] for eid in evidence)


def test_provider_schema_inlines_nested_arrays_without_mutating_original():
    from research_backend.benchmarks.run import Result
    tools = [dict(type="function", function=dict(name="finish_report", parameters=Result.model_json_schema()))]
    converted = inline_tool_schemas(tools)
    assert "$defs" in tools[0]["function"]["parameters"]
    assert "$ref" not in json.dumps(converted)
    assert converted[0]["function"]["parameters"]["properties"]["answers"]["items"]["type"] == "object"


def test_provider_double_encoded_containers_are_lossless_and_still_validated():
    from research_backend.benchmarks.run import Result
    tools = [dict(type="function", function=dict(name="finish_report", parameters=Result.model_json_schema()))]
    answers = [dict(target_id="answer", answer="frozen encoder", unanswerable=False, evidence_ids=["unread-id"])]
    original = json.dumps(dict(answers=json.dumps(answers)))
    call = dict(function=dict(name="finish_report", arguments=original))
    decoded, paths = decode_tool_arguments(call, tools)
    assert decoded == dict(answers=answers) and paths == ["$.answers"]
    assert call["function"]["arguments"] == original
    with pytest.raises(ValueError, match="sources_read"):
        validate_answers(decoded, sample()[1], {}, {"a": "a-local"})
    call["function"]["arguments"] = json.dumps(dict(answers="not JSON"))
    bad, paths = decode_tool_arguments(call, tools)
    assert bad["answers"] == "not JSON" and not paths
    with pytest.raises(ValueError):
        Result.model_validate(bad)


def test_local_hybrid_filters_before_ranking_and_checks_ownership(tmp_path):
    store = Store(tmp_path); store.initialize()
    wid = store.create_workspace("a", {"title": "fixture"})["id"]
    other = store.create_workspace("b", {"title": "private"})["id"]
    first = store.add_source("a", wid, SourceCreate(title="A", blocks=[dict(text="alpha method")]))["id"]
    second = store.add_source("a", wid, SourceCreate(title="B", blocks=[dict(text="beta method")]))["id"]
    private = store.add_source("b", other, SourceCreate(title="secret", blocks=[dict(text="private")]))["id"]
    embed = lambda texts: [[1.0, 0.0] if "alpha" in t else [0.0, 1.0] for t in texts]
    rag = LocalHybridRag(Settings(rag_enabled=True), store, embedder=embed)
    rag.index("a", wid, first); rag.index("a", wid, second)
    response = rag.search("a", wid, SearchRequest(query="alpha", strategy="rag", source_ids=[second]))
    assert {h["source_id"] for h in response["hits"]} == {second}
    with pytest.raises(NotFound):
        rag.search("a", wid, SearchRequest(query="private", strategy="rag", source_ids=[private]))


@pytest.mark.parametrize("method", ["grep", "grep-ledger"])
async def test_agent_runner_reads_then_decodes_and_validates_submission(tmp_path, method):
    doc, task, labels = sample()
    class Model:
        calls = 0
        evidence_id = None
        async def complete(self, messages, tools, max_tokens):
            self.calls += 1
            if self.calls == 1:
                source_id = json.loads(messages[1]["content"])["sources"][0]["id"]
                name, arguments = "sources_read", dict(source_id=source_id, start_line=3, line_count=1)
            elif len(tools) == 1 and tools[0]["function"]["name"] == "pin_evidence":
                evidence = json.loads(messages[-1]["content"].split("\n", 1)[1])["evidence"][0]
                self.evidence_id = evidence["id"]
                name, arguments = "pin_evidence", dict(entries=[dict(target_id="answer", evidence_id=self.evidence_id, quote="frozen encoder")])
            else:
                if self.evidence_id is None:
                    evidence = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))["data"]
                    self.evidence_id = evidence["id"]
                name = "finish_report"
                arguments = dict(answers=json.dumps([dict(target_id="answer", answer="frozen encoder", unanswerable=False, evidence_ids=[self.evidence_id])]))
            return dict(message=dict(role="assistant", tool_calls=[dict(id=f"c{self.calls}", type="function", function=dict(name=name, arguments=json.dumps(arguments)))]),
                        tokens=300, prompt_tokens=200, completion_tokens=100, finish_reason="tool_calls")
    result = await run_case(task, {"a": doc}, Settings(), method, tmp_path, lambda texts: [], 20000, 5, Model())
    calls = 2 if method == "grep" else 3
    assert result["status"] == "completed" and result["model_calls"] == calls
    assert result["tokens_used"] == 300 * calls
    assert result["pinned_targets"] == (0 if method == "grep" else 1)
    assert score_prediction(result, task, labels, {"a": doc})["answer_token_f1"] == 1
    trace = json.loads((tmp_path / "trace.json").read_text("utf-8"))
    assert trace[-1]["decoded_containers"][0]["paths"] == ["$.answers"]
