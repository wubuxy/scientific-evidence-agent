"""Independently audit published aggregates/intervals, not source-answer scoring."""
from collections import Counter
import json
from pathlib import Path
import random
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]/"docs/results/repair-five-arm-dev20-v1"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def close(actual, expected):
    assert abs(actual-expected) < 1e-12, (actual, expected)


def interval(differences, groups):
    rng = random.Random(20261106)
    values = sorted(mean(differences[t] for g in rng.choices(groups, k=len(groups)) for t in g)
                    for _ in range(5000))
    return [values[125], values[4875]]


def main():
    a = load(ROOT/"run002/analysis.json")
    s = load(ROOT/"run002/summary.json")
    g = load(ROOT/"gate-replay-v1/analysis.json")
    failed = load(ROOT/"failed-run001/summary.json")
    assert failed["planned_policy_outcomes"] == failed["failed_policy_outcomes"] == 200
    assert failed["model_calls"] == 0
    assert len(a["records"]) == 200
    assert len({(r["repeat"], r["task_id"], r["method"]) for r in a["records"]}) == 200
    groups = s["task_clusters"]
    task_ids = [t for group in groups for t in group]
    assert len(groups) == 19 and len(set(task_ids)) == len(task_ids) == 20
    means = {}
    for method in a["summaries"]:
        rows = [r for r in a["records"] if r["method"] == method]
        assert len(rows) == 40 and Counter(r["repeat"] for r in rows) == {0:20, 1:20}
        means[method] = {t:mean(r["answer_token_f1"] for r in rows if r["task_id"] == t) for t in task_ids}
        for t in task_ids:
            close(means[method][t], a["per_task_mean_f1"][method][t])
        for published in (s["summaries"][method], a["summaries"][method]):
            close(published["mean_answer_token_f1"], mean(means[method].values()))
            close(published["mean_tokens"], mean(r["tokens_used"] for r in rows))
        assert all(r["status"] == "completed" and r["tokens_used"] <= 20000 for r in rows)
    for reference, published in s["contrasts"].items():
        differences = {t:means["repair-reread"][t]-means[reference][t] for t in task_ids}
        close(published["mean_difference"], mean(differences.values()))
        for actual, expected in zip(published["bootstrap_95"], interval(differences, groups)):
            close(actual, expected)
    assert s["advance_to_confirmation"] is False
    assert len(g["records"]) == g["planned"] == g["completed"] == 40
    assert len({(r["repeat"],r["task_id"]) for r in g["records"]}) == 40
    gated = {t:mean(r["answer_token_f1"] for r in g["records"] if r["task_id"] == t) for t in task_ids}
    # Original scorer visits input-file order; replay visits frozen task order.
    # Group membership is identical, but finite seeded draws depend on ordering.
    replay_plan = load(ROOT.parents[1]/"experiments/repair-five-arm-dev20-v1/gate-replay-plan.json")
    order = {t:i for i,t in enumerate(replay_plan["task_ids"])}
    replay_groups = sorted((sorted(group, key=order.get) for group in groups), key=lambda group:order[group[0]])
    close(g["mean_answer_token_f1"], mean(gated.values()))
    close(g["mean_logical_tokens"], mean(r["tokens_used"] for r in g["records"]))
    for key, deltas in (("G_minus_E", {t:gated[t]-means["repair-search"][t] for t in task_ids}),
                        ("C_minus_G", {t:means["repair-reread"][t]-gated[t] for t in task_ids})):
        close(g["comparisons"][key]["mean_difference"], mean(deltas.values()))
        for actual, expected in zip(g["comparisons"][key]["bootstrap_95"], interval(deltas, replay_groups)):
            close(actual, expected)
    assert g["additional_physical_calls"] == 0
    assert sum(g["decisions"].values()) == 40
    close(sum(r["tokens_used"] for r in a["records"]), s["known_logical_policy_tokens"])
    close(sum(u["measured_llm_tokens"] for u in s["usage_by_repeat"]), s["known_physical_tokens"])
    print("PASS: 200 original outcomes, 40 cached policies, 6 paper-cluster intervals and retained failed batch. Source-answer scoring is outside this aggregate audit.")


if __name__ == "__main__":
    main()
