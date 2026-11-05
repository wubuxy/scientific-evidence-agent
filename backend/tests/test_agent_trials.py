import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading

import pytest

from research_backend.benchmarks.prepare import freeze
from research_backend.benchmarks import trials


@pytest.fixture
def study(tmp_path):
    documents = {}
    candidates = []
    for tid, sid in [("t1", "p1"), ("t2", "p2")]:
        documents[sid] = dict(id=sid, title=sid, text="alpha\nother", paragraphs=[dict(id="para", start_line=1, end_line=1)])
        task = dict(id=tid, question="Which value?", source_ids=[sid], targets=[dict(id="answer", source_id=sid, field="value")])
        gold = dict(targets=[dict(id="answer", source_id=sid, references=[dict(answer="alpha", unanswerable=False, evidence_paragraph_ids=["para"])])])
        candidates.append((task, gold))
    frozen = tmp_path / "dataset"
    freeze(frozen, "synthetic-fixture-not-real-accuracy", "dev", candidates, documents, 2, 1, {}, {})
    inputs = frozen / "inputs.json"
    plan = trials.make_plan(inputs, ["t1", "t2"], ["grep", "rag"], repeats=2, model="fake")
    root = tmp_path / "study"
    root.mkdir()
    trials.write_new(root / "plan.json", plan)
    trials.write_new(root / "study.json", dict(plan_sha256=trials.digest(root / "plan.json")))
    for repeat in range(2):
        trial = root / f"trial-{repeat:03d}"
        protocol = {k:plan[k] for k in ("dataset", "split", "inputs_sha256", "model", "max_tokens", "max_steps", "methods", "task_ids", "code_sha256")}
        protocol.update(extra_body={"enable_thinking": False}, embedding_model="fake", retrieval="local_bm25_dense_rrf")
        trials.write_new(trial / "protocol.json", protocol)
        predictions = []
        for method in plan["methods"]:
            for tid,sid in [("t1","p1"),("t2","p2")]:
                # t1 succeeds twice; t2 succeeds only on its first complete attempt.
                answer = "alpha" if tid == "t1" or repeat == 0 else "beta"
                predictions.append(dict(task_id=tid, method=method, status="completed", tokens_used=100,
                    elapsed_seconds=1., answers=[dict(target_id="answer", answer=answer, unanswerable=False, evidence_ids=["read"])],
                    reads=[dict(id="read", source_id=sid, start_line=1, end_line=1, quote="alpha")]))
        (trial / "predictions.jsonl").write_text("\n".join(json.dumps(p) for p in predictions), encoding="utf-8")
    return inputs, frozen / "gold.json", root


def test_reliability_separates_one_success_from_consistent_success(study):
    result = trials.analyze_trials(*study)
    summary = result["summaries"]["grep"]
    assert summary["planned_attempts"] == 4
    assert summary["mean_answer_token_f1"] == .75
    assert summary["lexical_reliability"]["1"] == dict(pass_at_k=.75, pass_all_k=.75)
    assert summary["lexical_reliability"]["2"] == dict(pass_at_k=1., pass_all_k=.5)
    assert result["paired_answer_f1"]["rag"]["mean_difference"] == 0
    assert result["paired_answer_f1"]["rag"]["independent_paper_groups"] == 2


def test_missing_attempts_stay_in_quality_denominator_and_usage_is_unknown(study):
    inputs, gold, root = study
    path = root / "trial-001/predictions.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows = [r for r in rows if not (r["task_id"] == "t1" and r["method"] == "grep")]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    result = trials.analyze_trials(inputs, gold, root)["summaries"]["grep"]
    assert result["planned_attempts"] == 4 and result["completed"] == 3
    assert result["mean_answer_token_f1"] == .5
    assert result["mean_tokens"] is None
    assert result["total_known_tokens"] == 300 and result["unknown_token_attempts"] == 1
    assert result["lexical_reliability"]["2"]["pass_all_k"] == 0


def test_correct_words_without_a_valid_read_do_not_pass(study):
    inputs, gold, root = study
    path = root / "trial-000/predictions.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["reads"][0]["quote"] = "invented"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    result = trials.analyze_trials(inputs, gold, root)
    row = next(r for r in result["records"] if r["method"] == "grep" and r["task_id"] == "t1" and r["repeat"] == 0)
    assert row["answer_token_f1"] == 1
    assert not row["lexical_pass"] and row["diagnostic_observation"] == "invalid_citation"


def test_duplicate_predictions_and_protocol_drift_are_rejected(study):
    inputs, gold, root = study
    path = root / "trial-000/predictions.jsonl"
    original = path.read_text()
    path.write_text(original + "\n" + original.splitlines()[0])
    with pytest.raises(ValueError, match="Duplicate prediction"):
        trials.analyze_trials(inputs, gold, root)
    path.write_text(original)
    protocol_path = root / "trial-001/protocol.json"
    protocol = trials.load(protocol_path)
    protocol["max_tokens"] += 1
    protocol_path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="protocol drift"):
        trials.analyze_trials(inputs, gold, root)


def test_shared_papers_form_connected_bootstrap_groups():
    groups = trials.task_clusters([dict(id="a", source_ids=["p1"]), dict(id="b", source_ids=["p1","p2"]), dict(id="c", source_ids=["p2"])])
    assert groups == [["a","b","c"]]
    result = trials.cluster_interval(dict(a=.5,b=0,c=-.5), groups, 1)
    assert result["bootstrap_95"] is None and result["independent_paper_groups"] == 1


def test_plan_rejects_duplicate_tasks_unsafe_paths_and_unapproved_test(study, tmp_path):
    inputs, _, _ = study
    for ids in (["t1","t1"], ["../t1"], "t1"):
        with pytest.raises(ValueError, match="Task IDs"):
            trials.make_plan(inputs, ids, ["grep"])
    with pytest.raises(ValueError, match="Success F1"):
        trials.make_plan(inputs, ["t1"], ["grep"], success_f1=0)
    value = trials.load(inputs)
    value["split"] = "test"
    inputs.write_text(json.dumps(value))
    manifest_path = inputs.parent / "manifest.json"
    manifest = trials.load(manifest_path)
    manifest["inputs_sha256"] = trials.digest(inputs)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Held-out"):
        trials.make_plan(inputs, ["t1"], ["grep"])


def test_runner_is_gold_blind_and_resume_never_retries_a_started_trial(study, tmp_path, monkeypatch):
    inputs, gold, root = study
    plan_path = root / "plan.json"
    destination = tmp_path / "execution"
    calls = []

    def execute(command, **kwargs):
        assert str(gold) not in command and not any("--gold" in part for part in command)
        assert kwargs["timeout"] > 0
        calls.append(command)
        Path(command[command.index("--out")+1]).mkdir(parents=True)

    monkeypatch.setattr(subprocess, "run", execute)
    trials.run_trials(plan_path, inputs, destination)
    assert len(calls) == 2
    trials.run_trials(plan_path, inputs, destination, resume=True)
    assert len(calls) == 2
    result = trials.analyze_trials(inputs, gold, destination)
    assert result["summaries"]["grep"]["planned_attempts"] == 4
    assert result["summaries"]["grep"]["mean_answer_token_f1"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_usage", "exception"])
async def test_failed_provider_call_keeps_partial_cost_but_never_claims_zero_total(tmp_path, failure):
    from research_backend.benchmarks.run import run_case
    from research_backend.config import Settings

    class Model:
        calls = 0

        async def complete(self, messages, tools, max_tokens):
            self.calls += 1
            if self.calls == 1:
                sid = json.loads(messages[1]["content"])["sources"][0]["id"]
                return dict(tokens=300, message=dict(role="assistant", tool_calls=[dict(id="c1", type="function",
                    function=dict(name="sources_read", arguments=json.dumps(dict(source_id=sid, start_line=1, line_count=1))))]))
            if failure == "exception":
                raise RuntimeError("provider failure")
            return dict(tokens=None, message=dict(role="assistant", tool_calls=[]))

    task = dict(id="cost-case", question="Which value?", source_ids=["p"], targets=[dict(id="answer", source_id="p", field="value")])
    result = await run_case(task, {"p":dict(id="p", title="fixture", text="alpha")}, Settings(), "grep", tmp_path,
                            lambda texts: [], 20000, 5, Model())
    assert result["status"] == "failed" and result["tokens_used"] is None
    assert result["known_tokens_used"] == 300
    assert result["model_calls"] == 2 and result["completed_model_calls"] == 1


def test_complete_repeated_trials_through_local_model_endpoint(study, tmp_path, monkeypatch):
    """Actual child processes, SDK requests, source reads and gold-side scoring; no cloud."""
    inputs, gold, _ = study
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests_seen.append(payload)
            observations = [json.loads(m["content"])["data"] for m in payload["messages"] if m["role"] == "tool"]
            if observations:
                name = "finish_report"
                arguments = dict(answers=[dict(target_id="answer", answer="alpha", unanswerable=False, evidence_ids=[observations[-1]["id"]])])
            else:
                name = "sources_read"
                sid = json.loads(payload["messages"][1]["content"])["sources"][0]["id"]
                arguments = dict(source_id=sid, start_line=1, line_count=1)
            response = dict(id="offline-completion", object="chat.completion", created=1, model="offline-fixture",
                choices=[dict(index=0, finish_reason="tool_calls", message=dict(role="assistant", content=None,
                    tool_calls=[dict(id="tool", type="function", function=dict(name=name, arguments=json.dumps(arguments)))]))],
                usage=dict(prompt_tokens=200, completion_tokens=100, total_tokens=300))
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env_file = tmp_path / ".env.research"
        env_file.write_text(f"RESEARCH_LLM_API_KEY=offline-fixture\nRESEARCH_LLM_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n")
        plan = trials.make_plan(inputs, ["t1"], ["grep"], repeats=2, model="offline-fixture")
        plan_path = tmp_path / "integration-plan.json"
        trials.write_new(plan_path, plan)
        root = tmp_path / "integration"
        trials.run_trials(plan_path, inputs, root, env_file=env_file)
        result = trials.analyze_trials(inputs, gold, root)["summaries"]["grep"]
        assert len(requests_seen) == 4
        assert result["completed"] == result["planned_attempts"] == 2
        assert result["mean_answer_token_f1"] == 1 and result["mean_tokens"] == 600
        assert result["lexical_reliability"]["2"]["pass_all_k"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
