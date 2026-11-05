import json
from argparse import Namespace
import pytest
from research_backend.benchmarks.repair_replay import Diagnosis, needs_repair, targeted_read, affordable


def diagnosis(**updates):
    return Diagnosis(**(dict(support="supported", adequacy="complete", role="aligned", stage="aligned",
        gap="", grep_query="adapter", anchor_evidence_id="old") | updates))


def fixture():
    lines = [f"Unrelated line {i}." for i in range(1, 81)]
    lines[59] = "The adapter is the only trainable component."
    doc = dict(id="p", title="Paper", text="\n".join(lines))
    task = dict(id="task", question="What is trained?", source_ids=["p"], targets=[dict(id="answer", source_id="p", field="training")])
    reads = [dict(id="old", source_id="p", start_line=1, end_line=4, quote="\n".join(lines[:4]))]
    return task, doc, reads


def test_repair_gate_distinguishes_support_from_adequacy_and_role():
    assert not needs_repair(diagnosis())
    for update in (dict(support="insufficient"), dict(adequacy="partial"), dict(role="mismatch"), dict(stage="unknown")):
        assert needs_repair(diagnosis(**update))


@pytest.mark.requires_rg
def test_targeted_read_is_one_bounded_canonical_same_paper_window(tmp_path):
    task, doc, reads = fixture()
    extra, audit = targeted_read(task, {"p":doc}, reads, diagnosis(), tmp_path)
    assert extra["source_id"] == "p"
    assert extra["end_line"] - extra["start_line"] + 1 <= 40
    assert extra["quote"] == "\n".join(doc["text"].splitlines()[extra["start_line"]-1:extra["end_line"]])
    assert "only trainable" in extra["quote"] and audit["newly_read_lines"] > 0


def test_targeted_read_rejects_invented_anchor(tmp_path):
    task, doc, reads = fixture()
    with pytest.raises(ValueError, match="Unknown review anchor"):
        targeted_read(task, {"p":doc}, reads, diagnosis(anchor_evidence_id="invented"), tmp_path)


def test_budget_accounts_for_prior_spend_and_reserved_output():
    messages = [dict(role="user", content="small prompt")]
    assert affordable(messages, [], 0, 20000, 900)
    assert not affordable(messages, [], 19500, 20000, 900)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_usage", [False, True])
@pytest.mark.parametrize("control_only", [False, True])
async def test_three_arm_replay_keeps_controls_separate_and_counts_shared_cost_once(tmp_path, monkeypatch, missing_usage, control_only):
    from research_backend.benchmarks import repair_replay as replay
    from research_backend.config import Settings
    task, doc, reads = fixture()
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(dict(dataset="fixture", split="dev", documents=[doc])), encoding="utf-8")
    prior = tmp_path / "prior"; directory = prior / "grep" / "task"; directory.mkdir(parents=True)
    (prior / "protocol.json").write_text(json.dumps(dict(model="fake", max_tokens=20000)), encoding="utf-8")
    base = dict(task_id="task", status="completed", method="grep", reads=reads, tokens_used=300,
        answers=[dict(target_id="answer", answer="", unanswerable=True, evidence_ids=[])])
    (directory / "prediction.json").write_text(json.dumps(base), encoding="utf-8")
    monkeypatch.setattr(replay, "load_cases", lambda *args: [dict(task=task, reads=reads, prediction_sha256="frozen")])
    monkeypatch.setattr(replay, "load_environment", lambda: None)
    monkeypatch.setattr(replay.Settings, "from_env", lambda: Settings(llm_api_key="offline-test"))

    def read_missing_evidence(task, documents, reads, diagnosis, directory, binary=""):
        # Branch accounting is independent of the real-rg integration test above.
        lines = documents["p"]["text"].splitlines()
        return dict(id="new", source_id="p", start_line=50, end_line=70,
                    quote="\n".join(lines[49:70])), dict(reason="read")

    monkeypatch.setattr(replay, "targeted_read", read_missing_evidence)
    class Model:
        def __init__(self, settings): pass
        async def complete(self, messages, definitions, max_tokens):
            name = definitions[0]["function"]["name"]
            if name == "review_answer":
                payload = diagnosis(support="insufficient", adequacy="unknown").model_dump(); tokens = None if missing_usage else 100
            else:
                evidence = json.loads(messages[1]["content"])["evidence"]
                found = next((e for e in evidence if "only trainable" in e["quote"]), None)
                payload = dict(answers=[dict(target_id="answer", answer="adapter" if found else "", unanswerable=not bool(found), evidence_ids=[found["id"]] if found else [])]); tokens = 200
            return dict(tokens=tokens, prompt_tokens=80, completion_tokens=20, response_model="fake",
                message=dict(tool_calls=[dict(function=dict(name=name, arguments=json.dumps(payload)))]))
    monkeypatch.setattr(replay, "ChatModel", Model)
    out = tmp_path / "out"
    await replay.run(Namespace(env_file=None, inputs=inputs, prior=prior, out=out, model="fake", max_tokens=20000, concurrency=1, control_only=control_only))
    rows = {r["method"]:r for r in [json.loads(l) for l in (out/"predictions.jsonl").read_text("utf-8").splitlines()]}
    if control_only:
        assert len(rows) == 2
        assert rows["empty-read-rescue"]["tokens_used"] == 300
        assert rows["empty-read-rescue"]["repair_status"] == "not_empty_or_base_failed"
        assert json.loads((out/"usage.json").read_text("utf-8"))["additional_model_calls"] == 0
        return
    assert len(rows) == 3
    if missing_usage:
        assert rows["review-reread"]["tokens_used"] is None
        assert rows["review-reread"]["repair_status"] == "unknown_usage"
        assert json.loads((out/"usage.json").read_text("utf-8"))["missing_usage_calls"] == 1
    else:
        assert rows["baseline"]["tokens_used"] == 300
        assert rows["review-rewrite"]["tokens_used"] == rows["review-reread"]["tokens_used"] == 600
        assert rows["review-rewrite"]["answers"][0]["unanswerable"]
        assert rows["review-reread"]["answers"][0]["answer"] == "adapter"
        assert len(rows["review-reread"]["reads"]) == 2
        assert json.loads((out/"usage.json").read_text("utf-8"))["additional_measured_tokens"] == 500
