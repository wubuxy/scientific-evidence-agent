from copy import deepcopy
import json

import pytest

from research_backend.benchmarks import gate_replay as gate
from research_backend.benchmarks import repair_arms
from research_backend.config import Settings


def fixture():
    base = dict(task_id="t", status="completed", tokens_used=300, model_calls=2,
                answers=[{"answer": "original"}], reads=[])
    review = dict(support="insufficient", adequacy="partial", role="aligned", stage="aligned",
                  gap="missing evidence", grep_query="x", anchor_evidence_id="")
    audit = dict(review=review, calls=[dict(tokens_used=100)])
    search = dict(base, answers=[{"answer": "candidate"}], tokens_used=800, model_calls=4)
    calls = [dict(tokens_used=n, messages=[dict(role="user", content="fixture")]) for n in (200, 300)]
    return base, {"intervention_status": "rewritten"}, audit, search, {"calls": calls}


def test_gate_selects_on_review_and_charges_shared_review_once():
    args = fixture()
    result = gate.replay(*args, 20000)
    assert result["answers"] == args[3]["answers"]
    assert result["tokens_used"] == 900
    assert result["model_calls"] == 5 and result["intervention_calls"] == 3
    assert args[0]["answers"] == [{"answer": "original"}]
    args[2]["review"].update(support="supported", adequacy="complete")
    # A candidate's hypothetical score is irrelevant to routing.
    args[3]["answer_token_f1"] = 1
    result = gate.replay(*args, 20000)
    assert result["answers"] == args[0]["answers"] and result["tokens_used"] == 400


def test_gate_retains_original_on_review_failure_but_counts_cost():
    args = fixture()
    args[1]["intervention_status"] = "review_failed:ValueError"
    result = gate.replay(*args, 20000)
    assert result["answers"] == args[0]["answers"]
    assert result["tokens_used"] == 400


@pytest.mark.parametrize("unknown_review", [False, True])
def test_gate_cannot_reuse_cached_answer_without_budget_or_usage(unknown_review):
    args = fixture()
    if unknown_review:
        args[2]["calls"][0]["tokens_used"] = None
    result = gate.replay(*args, 500)
    assert result["answers"] == args[0]["answers"]
    assert result["tokens_used"] is None and result["known_tokens_used"] >= 300
    assert result["status"] == ("completed" if unknown_review else "failed")


def test_replay_plan_rejects_source_drift_and_missing_rows_remain(tmp_path):
    prior = tmp_path/"source"
    prior.mkdir()
    (prior/"plan.json").write_text(json.dumps(dict(task_ids=["t"], repeats=1, max_tokens=20000,
        analysis_seed=1, inputs_sha256="fixture")))
    plan = tmp_path/"plan.json"
    gate.write_new(plan, gate.make_plan(prior))
    gate.generate(plan, prior, tmp_path/"out")
    row = gate.load(tmp_path/"out/trial-000/t/prediction.json")
    assert row["status"] == "failed" and row["tokens_used"] is None
    gate.write_new(prior/"trial-000/repair-baseline/t/prediction.json", fixture()[0])
    with pytest.raises(ValueError, match="changed after plan"):
        gate.generate(plan, prior, tmp_path/"changed")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,tokens,expected", [
    ("failed", 300, "review_skipped:prefix_not_completed"),
    ("completed", None, "review_skipped:unknown_prefix_usage")])
async def test_unreviewed_prefix_is_never_reported_as_review_passed(tmp_path, status, tokens, expected):
    class NoModel:
        async def complete(self, *args):
            raise AssertionError("Ineligible prefixes must not call model")
    task = dict(id="t", question="q", source_ids=["p"], targets=[{"id":"answer"}])
    base = dict(task_id="t", status=status, tokens_used=tokens, answers=[], reads=[])
    rows = await repair_arms.run_branches(task, {}, Settings(), base, ["repair-reread"], tmp_path,
                                         20000, NoModel())
    assert rows[0]["intervention_status"] == expected


@pytest.mark.asyncio
async def test_invalid_review_anchor_falls_back_instead_of_writing(tmp_path):
    class Model:
        calls = 0
        async def complete(self, messages, definitions, limit):
            self.calls += 1
            assert self.calls == 1, "Invalid diagnosis must not reach writer"
            payload = deepcopy(fixture()[2]["review"])
            payload["anchor_evidence_id"] = "invented"
            return dict(tokens=100, message=dict(tool_calls=[dict(function=dict(
                name="review_answer", arguments=json.dumps(payload)))]))
    task = dict(id="t", question="q", source_ids=["p"], targets=[{"id":"answer"}])
    base = dict(task_id="t", status="completed", tokens_used=300, answers=[], reads=[])
    row = (await repair_arms.run_branches(task, {}, Settings(), base, ["repair-rewrite"], tmp_path,
                                        20000, Model()))[0]
    assert row["intervention_status"] == "review_failed:ValueError"
    assert row["tokens_used"] == 400
