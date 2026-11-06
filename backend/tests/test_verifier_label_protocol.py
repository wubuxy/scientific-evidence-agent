import importlib.metadata
import json
import sys
from types import ModuleType, SimpleNamespace
import weakref

import pytest

from research_backend.benchmarks import verifier_labels as labels
from research_backend.benchmarks.trials import write_new


def prediction(verdict, score=.5, **kwargs):
    return dict(verdict=verdict, support_score=score, status="completed", **kwargs)


def test_proposal_does_not_broadcast_document_mismatch_or_change_unannotated_citation():
    rows = [dict(id="official-dev-597-1", label="supported"),
            dict(id="official-dev-597-2", label="supported"),
            dict(id="official-dev-808-3", label="supported"),
            dict(id="official-dev-808-4", label="insufficient")]
    proposals = [dict(claim_id=597, doc_id=None, error_type="per_document_outcome_mismatch",
                      original_label="SUPPORT", corrected_label="NEI"),
                 dict(claim_id=808, doc_id=None, error_type="polarity",
                      original_label="SUPPORT", corrected_label="CONTRADICT")]
    revised, changed, unresolved = labels.apply_proposals(rows, proposals)
    assert [r["label"] for r in revised] == ["supported", "supported", "contradicted", "insufficient"]
    assert [r["id"] for r in changed] == ["official-dev-808-3"]
    assert unresolved == ["official-dev-597-1", "official-dev-597-2"]
    assert rows[2]["label"] == "supported"
    with pytest.raises(ValueError, match="Duplicate correction"):
        labels.apply_proposals(rows, proposals + [proposals[1]])


def test_only_labels_can_change_not_input_or_order():
    original = [dict(id="a", label="supported", claim="original"), dict(id="b", label="insufficient")]
    labels.assert_label_only(original, [dict(original[0], label="contradicted"), original[1]])
    for changed in [list(reversed(original)), [dict(original[0], claim="modified"), original[1]], original[:1]]:
        with pytest.raises(ValueError):
            labels.assert_label_only(original, changed)


def test_generation_does_not_open_evaluation_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "code_files", lambda: {})
    for arm in ["raw", "proposed"]:
        write_new(tmp_path / f"training-{arm}.json", dict(cases=[]))
    write_new(tmp_path / "plan.json", dict(config=labels.CONFIG, inherited_training_config=labels.v2.CONFIG,
        code_sha256={}, files={"labels-original.json": "intentionally-not-readable"}))
    labels.check_plan(tmp_path, verify_weights=False)
    with pytest.raises(FileNotFoundError):
        labels.check_plan(tmp_path, scoring=True, verify_weights=False)


def test_prediction_identity_and_binary_baseline_are_enforced():
    gold = {s: {"a": "supported"} for s in labels.SPLITS}
    record = prediction("supported", id="a", method="raw-20261106", split="test")
    for bad in [[record, record], [dict(record, id="unknown")], [dict(record, support_score=float("nan"))]]:
        with pytest.raises(ValueError):
            labels.index_records(bad, labels.METHODS, gold)
    mini = dict(record, method="minicheck-bf16", verdict="unsupported")
    labels.index_records([mini], labels.REFERENCES, gold)
    with pytest.raises(ValueError):
        labels.index_records([dict(mini, verdict="contradicted")], labels.REFERENCES, gold)


def test_threshold_can_reject_all_and_cannot_use_incomplete_calibration():
    gold = dict(a="supported", b="insufficient")
    records = dict(a=prediction("supported", 1), b=prediction("supported", 1))
    threshold = labels.select_threshold(gold, records)
    assert threshold > 1
    value = labels.binary_metrics(gold, labels.threshold_records(records, threshold))
    assert value["acceptance_rate"] == 0
    assert value["supported_recall"] == 0
    assert value["supported_precision"] is None
    assert labels.select_threshold(gold, {"a": records["a"]}) is None
    records["b"] = prediction("insufficient", .3)
    assert labels.select_threshold(gold, records) == 1


def test_failed_prediction_cannot_count_as_correct():
    records = dict(a=prediction("supported", .9),
                   b=dict(prediction("contradicted", .1), status="failed"))
    value = labels.binary_metrics(dict(a="supported", b="contradicted"), records)
    assert value["accuracy"] == .5
    assert value["missing_predictions"] == 1
    assert value["auroc"] is None


def test_paired_bootstrap_keeps_repeated_groups_and_averages_all_seeds():
    gold = dict(a="supported", b="contradicted", c="insufficient")
    indexed = {m: dict(test={i: prediction(v) for i, v in gold.items()}) for m in labels.METHODS}
    indexed["proposed-20261106"]["test"]["b"] = prediction("supported")
    indexed["proposed-20261107"]["test"]["b"] = prediction("supported")
    value = labels.paired_delta(["a", "b", "b", "c"], gold, indexed)
    assert value["false_support_rate"] == pytest.approx(4 / 9)
    assert value["macro_f1"] == pytest.approx(-1 / 3)
    assert value["support_recall"] == 0


def test_missing_condition_stays_in_denominator_and_prevents_intervals(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "check_plan", lambda *args, **kwargs: dict(scope="fixture", counts={s: 3 for s in labels.SPLITS}))
    plan_dir, run_dir = tmp_path / "plan", tmp_path / "run"
    gold = dict(a="supported", b="contradicted", c="insufficient")
    write_new(plan_dir / "plan.json", {})
    for view in ["original", "published", "doc597"]:
        write_new(plan_dir / f"labels-{view}.json", {s: gold for s in labels.SPLITS})
    write_new(plan_dir / "test-inputs.json", dict(cases=[dict(id=i, group_keys=[i]) for i in gold]))
    records, references = [], []
    for method in labels.METHODS + labels.REFERENCES:
        if method == labels.METHODS[0]:
            continue
        for split in labels.SPLITS:
            for identity, verdict in gold.items():
                if method == "minicheck-bf16" and verdict != "supported":
                    verdict = "unsupported"
                row = prediction(verdict, .9 if verdict == "supported" else .1,
                                 id=identity, method=method, split=split)
                (references if method in labels.REFERENCES else records).append(row)
    write_new(plan_dir / "reference-predictions.json", dict(records=references))
    write_new(run_dir / "protocol.json", dict(plan_sha256=labels.v2.sha(plan_dir / "plan.json"), methods=labels.METHODS))
    write_new(run_dir / "run-summary.json", dict(complete=False, conditions=[dict(method=m, completed=m != labels.METHODS[0]) for m in labels.METHODS]))
    (run_dir / "predictions.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    result = labels.score(plan_dir, run_dir)
    assert result["complete"] is False
    assert result["bootstrap_draws"] == 0
    assert "intervals" not in result["paired_contrasts"]["original"]
    assert result["results"]["original"][labels.METHODS[0]]["test"]["binary"]["missing_predictions"] == 3
    assert "three_class" not in result["results"]["original"]["minicheck-bf16"]["test"]


def test_six_trainings_release_base_and_adapter_before_every_reload(tmp_path, monkeypatch):
    fake_torch = ModuleType("torch")
    fake_torch.set_num_threads = lambda n: None
    fake_torch.cuda = SimpleNamespace(empty_cache=lambda: None, get_device_name=lambda: "fixture")
    fake_torch.version = SimpleNamespace(cuda="fixture")
    fake_peft = ModuleType("peft")
    class Base:
        pass
    class Adapter:
        def __init__(self, base):
            self.base = base
    fake_peft.PeftModel = SimpleNamespace(from_pretrained=lambda base, *args, **kwargs: Adapter(base))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "peft", fake_peft)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "fixture")
    monkeypatch.setattr(labels, "check_plan", lambda *args, **kwargs: dict(model_dirs=dict(large="fixture")))
    live, loads = weakref.WeakSet(), []
    def loader(*args):
        assert not live, "a previous base model is still referenced"
        model = Base()
        live.add(model)
        loads.append(model.__class__.__name__)
        return model
    monkeypatch.setattr(labels.v2, "load_model", loader)
    monkeypatch.setattr(labels.v2, "tokenizer_for", lambda *args: None)
    monkeypatch.setattr(labels.v2, "train", lambda model, *args, **kwargs: Adapter(model))
    monkeypatch.setattr(labels, "infer", lambda *args, **kwargs: None)
    plan_dir = tmp_path / "plan"
    write_new(plan_dir / "plan.json", {})
    for arm in ["raw", "proposed"]:
        write_new(plan_dir / f"training-{arm}.json", dict(cases=[]))
    write_new(plan_dir / "training-order.json", {str(s): dict(ids=[]) for s in labels.CONFIG["seeds"]})
    for split in labels.SPLITS:
        write_new(plan_dir / (split + "-inputs.json"), dict(cases=[]))
    result = labels.run(plan_dir, tmp_path / "run")
    assert result["complete"]
    assert len(loads) == 12
    assert not live
