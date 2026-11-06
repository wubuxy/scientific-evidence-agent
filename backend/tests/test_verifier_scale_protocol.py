import hashlib
import json
import importlib.metadata
import sys
from types import ModuleType, SimpleNamespace
import weakref

import pytest

from research_backend.benchmarks import verifier_scale as scale
from research_backend.benchmarks.trials import write_new


def prediction(verdict, score):
    return dict(verdict=verdict, support_score=score, status="completed")


def test_missing_negative_is_wrong_and_prevents_auc():
    value = scale.binary_metrics({"yes": "supported", "no": "insufficient"},
                                 {"yes": prediction("supported", .9)})
    assert value["accuracy"] == .5
    assert value["balanced_accuracy"] == .5
    assert value["missing_predictions"] == 1
    assert value["auroc"] is None


def test_auc_ties_use_average_ranks():
    gold = {"a": "supported", "b": "contradicted", "c": "insufficient"}
    records = {i: prediction("supported", .5) for i in gold}
    assert scale.binary_metrics(gold, records)["auroc"] == .5


def test_binary_baseline_does_not_invent_contradiction_labels():
    gold = {"a": "supported", "b": "contradicted", "c": "insufficient"}
    records = {"a": prediction("supported", .9),
               "b": prediction("unsupported", .1), "c": prediction("unsupported", .2)}
    value = scale.binary_metrics(gold, records)
    assert value["balanced_accuracy"] == 1
    assert value["auroc"] == 1
    assert records["b"]["verdict"] == "unsupported"


def test_native_minicheck_projection_excludes_gold_and_metadata():
    row = dict(claim="claim text", question="question sentinel", evidence=[dict(quote="raw evidence")],
               label="gold sentinel", group_keys=["secret group"], id="private metadata")
    rendered = str(scale.mini_messages(row))
    assert "claim text" in rendered and "raw evidence" in rendered
    assert all(text not in rendered for text in ["gold sentinel", "question sentinel", "secret group", "private metadata"])


def test_duplicate_abstracts_are_grouped_despite_different_titles_and_ids():
    rows = [dict(claim="One claim", group_keys=["original-a"],
                 evidence=[dict(source_id="1", quote="Title one\n1: The SAME abstract.")]),
            dict(claim="Another claim", group_keys=["original-b"],
                 evidence=[dict(source_id="2", quote="Different title\n1: the same   abstract.")])]
    a, b = scale.augment_groups(rows)
    shared = set(a["group_keys"]) & set(b["group_keys"])
    assert len(shared) == 1 and next(iter(shared)).startswith("normalized-abstract:")
    assert rows[0]["group_keys"] == ["original-a"]


def test_custom_python_is_checked_without_executing(tmp_path):
    path = tmp_path / "modeling.py"
    content = b'raise RuntimeError("must not execute")\n'
    path.write_bytes(content)
    blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    spec = dict(files={"modeling.py": dict(git_blob_id=blob)})
    scale.verify_official(tmp_path, spec, mini=True)
    path.write_bytes(content + b"# changed\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        scale.verify_official(tmp_path, spec, mini=True)


def test_generation_plan_check_does_not_open_test_gold(tmp_path, monkeypatch):
    monkeypatch.setattr(scale, "code_files", lambda: {})
    write_new(tmp_path / "test-inputs.json", dict(cases=[]))
    write_new(tmp_path / "plan.json", dict(config=scale.CONFIG, code_sha256={}, model_dirs={},
        files={"test-inputs.json": scale.sha(tmp_path / "test-inputs.json"), "test-gold.json": "not-readable"}))
    scale.check_plan(tmp_path)
    with pytest.raises(FileNotFoundError):
        scale.check_plan(tmp_path, scoring=True)


def test_failed_matching_baseline_cannot_make_training_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(scale, "CONFIG", dict(scale.CONFIG, bootstrap_draws=12))
    monkeypatch.setattr(scale, "check_plan", lambda *args, **kwargs: {})
    plan_dir, run_dir = tmp_path / "plan", tmp_path / "run"
    plan_dir.mkdir()
    run_dir.mkdir()
    write_new(plan_dir / "plan.json", {})
    labels = dict(a="supported", b="contradicted", c="insufficient")
    cases = [dict(id=i, group_keys=[i], label=label) for i, label in labels.items()]
    for split in ["calibration", "test", "challenge"]:
        write_new(plan_dir / (split + "-inputs.json"), dict(cases=cases))
        write_new(plan_dir / (split + "-gold.json"), dict(labels=labels))
    write_new(plan_dir / "training.json", dict(cases=cases))
    methods = ["small-bf16", "small-nf4", "large-bf16", "large-nf4"]
    methods += ["large-lora-" + str(s) for s in scale.CONFIG["seeds"]]
    methods += ["minicheck-bf16"]
    write_new(run_dir / "protocol.json", dict(plan_sha256=scale.sha(plan_dir / "plan.json"),
        methods=methods, training_precision="bf16"))
    records = []
    for method in methods:
        if method == "large-bf16":
            continue  # Planned matching baseline failed completely.
        for split in ["calibration", "test", "challenge", "empty-evidence"]:
            for i, label in labels.items():
                verdict = "insufficient" if split == "empty-evidence" else label
                if method == "minicheck-bf16" and verdict != "supported":
                    verdict = "unsupported"
                records.append(dict(id=i, method=method, split=split, status="completed",
                                    verdict=verdict, support_score=.9 if verdict == "supported" else .1))
    (run_dir / "predictions.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    value = scale.score(plan_dir, run_dir)
    assert value["absolute_candidate_gate"] is True
    assert value["primary_contrasts"]["training"]["delta"] == 1
    assert value["training_comparison_complete"] is False
    assert value["training_success_gate"] is False


def test_all_model_handles_are_released_before_loading_next_arm(tmp_path, monkeypatch):
    """Exercise the complete arm loop without torch/weights, including reloads."""
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
    monkeypatch.setattr(scale, "check_plan", lambda *args, **kwargs: dict(model_dirs={k: k for k in scale.MODEL_IDS}))
    live, loads = weakref.WeakSet(), []
    def loader(plan, key, precision):
        assert not live, "previous base model is still strongly referenced"
        model = Base()
        live.add(model)
        loads.append((key, precision))
        return model
    monkeypatch.setattr(scale, "load_model", loader)
    monkeypatch.setattr(scale, "tokenizer_for", lambda *args: None)
    monkeypatch.setattr(scale, "train", lambda model, *args, **kwargs: Adapter(model))
    monkeypatch.setattr(scale, "infer", lambda *args, **kwargs: None)
    plan_dir = tmp_path / "plan"
    write_new(plan_dir / "plan.json", {})
    for split in ["calibration", "test", "challenge", "training"]:
        write_new(plan_dir / (split + (".json" if split == "training" else "-inputs.json")), dict(cases=[]))
    preflight = tmp_path / "preflight.json"
    write_new(preflight, dict(complete=True, plan_sha256=scale.sha(plan_dir / "plan.json"),
                             selected_training_precision="bf16"))
    result = scale.run(plan_dir, preflight, tmp_path / "run")
    assert result == dict(complete=True, errors=[])
    assert len(loads) == 11
    assert not live
