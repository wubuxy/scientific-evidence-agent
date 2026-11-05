"""Statistical reference cases, leakage checks, source alignment and authorization."""
import asyncio
import hashlib
import io
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from jose import jwt

from research_backend.api import create_app
from research_backend.config import Settings
from research_backend.datasets import add_dataset, parse_table, read_dataset
from research_backend.lab_runtime import LabRuntime
from research_backend.lab_schemas import AnalysisCreate, TranslationCreate
from research_backend.store import Store
from research_backend.translation import prepare_translation, resolve_target_language, translation_messages

SECRET = "lab-offline-test-secret-" * 3


@pytest.fixture
def compute_analysis():
    pytest.importorskip("scipy", reason="statistical analysis requires requirements-analysis.txt")
    from research_backend.analysis import compute_analysis
    return compute_analysis


def auth(subject="alice"):
    return {"Authorization": "Bearer " + jwt.encode({"sub": subject, "exp": datetime.now(timezone.utc) + timedelta(minutes=10)}, SECRET)}


def params(method, **extra):
    return AnalysisCreate(dataset_id="fixture", method=method, columns=["a", "b"], **extra).model_dump()


def test_welch_reference_statistic_effect_and_confidence_interval(compute_analysis):
    data = dict(columns=["a", "b"], rows=[[1, 4], [2, 5], [3, 6]])
    result = compute_analysis(data, params("welch_t"))
    assert result["statistic"] == pytest.approx(-3.674234614174767)
    assert result["p_value"] == pytest.approx(0.021311641128756727)
    assert result["degrees_of_freedom"] == pytest.approx(4)
    assert result["effect_size"]["value"] == pytest.approx(-3)
    assert result["confidence_interval"]["low"] < -3 < result["confidence_interval"]["high"] < 0


def test_paired_missing_rows_preserve_subject_alignment(compute_analysis):
    data = dict(columns=["a", "b"], rows=[[1, 1], [2, None], [None, 9], [4, 2], [5, 3]])
    result = compute_analysis(data, params("paired_t"))
    assert result["sample_size"] == 3
    assert result["excluded_rows"] == 2
    assert result["statistic"] == pytest.approx(2)
    assert result["p_value"] == pytest.approx(0.1835034190722739)


def test_chi_square_weighted_table_matches_reference(compute_analysis):
    data = dict(columns=["a", "b", "count"], rows=[["X", "u", 10], ["X", "v", 10], ["X", "w", 20], ["Y", "u", 20], ["Y", "v", 20], ["Y", "w", 20]])
    result = compute_analysis(data, params("chi_square", weight_column="count", continuity_correction=False))
    assert result["statistic"] == pytest.approx(2.7777777777777777)
    assert result["p_value"] == pytest.approx(0.24935220877729622)
    assert result["effect_size"]["value"] == pytest.approx(1 / 6)
    assert result["sample_size"] == 100
    assert result["tables"][1]["rows"][0][1:] == [12, 12, 16]


def test_sparse_categories_warn_and_offer_fisher(compute_analysis):
    data = dict(columns=["a", "b", "count"], rows=[["X", "u", 0], ["X", "v", 4], ["Y", "u", 6], ["Y", "v", 0]])
    result = compute_analysis(data, params("chi_square", weight_column="count"))
    assert result["warnings"] and 0 <= result["fisher_exact"]["p_value"] <= 1
    data["rows"][0][2] = 0.4
    with pytest.raises(ValueError, match="频数"):
        compute_analysis(data, params("chi_square", weight_column="count"))


@pytest.mark.parametrize("method", ["welch_t", "paired_t", "correlation", "linear_regression", "hotelling_t2"])
def test_degenerate_numeric_inputs_are_rejected(method, compute_analysis):
    with pytest.raises(ValueError):
        compute_analysis(dict(columns=["a", "b"], rows=[[1, 1]] * 5), params(method))


def test_regression_known_line_and_hotelling_zero_mean(compute_analysis):
    result = compute_analysis(dict(columns=["a", "b"], rows=[[i, 2 * i + 3] for i in range(8)]), params("linear_regression"))
    assert result["slope"] == pytest.approx(2) and result["intercept"] == pytest.approx(3)
    assert result["r_squared"] == pytest.approx(1)
    hotelling = compute_analysis(dict(columns=["a", "b"], rows=[[-1, 0], [1, 0], [0, -1], [0, 1]]), params("hotelling_t2"))
    assert hotelling["statistic"] == 0 and hotelling["p_value"] == 1


def test_parser_preserves_missing_categorical_and_numeric_values():
    data, columns, warnings = parse_table("runs.csv", b"seed,metric,group\n1,3.5,A\n2,,B\n3,NA,A\n")
    assert data["rows"][0] == [1., 3.5, "A"]
    assert columns[1]["missing"] == 2 and columns[2]["kind"] == "categorical"
    assert warnings
    with pytest.raises(ValueError, match="重复"):
        parse_table("bad.csv", b"a,a\n1,2\n3,4")
    with pytest.raises(ValueError, match="列数"):
        parse_table("bad.csv", b"a,b\n1,2,3\n3,4")


def test_xlsx_formulas_are_not_executed():
    pytest.importorskip("openpyxl", reason="XLSX import requires requirements-analysis.txt")
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["index", "formula"])
    book.active.append([1, "=1+1"])
    book.active.append([2, "=3+3"])
    stream = io.BytesIO(); book.save(stream)
    data, columns, warnings = parse_table("data.xlsx", stream.getvalue())
    assert all(row[1] is None for row in data["rows"])
    assert columns[1]["missing"] == 2 and warnings


def test_dataset_integrity_detects_post_import_modification(tmp_path):
    store = Store(tmp_path); store.initialize()
    wid = store.create_workspace("alice", dict(title="data"))["id"]
    dataset = add_dataset(store, "alice", wid, "a.csv", b"a,b\n1,2\n3,4")
    path = tmp_path / wid / "datasets" / dataset["id"] / "data.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == dataset["data_hash"]
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="校验"):
        read_dataset(store, "alice", wid, dataset["id"])


class FakeTranslator:
    def __init__(self):
        self.calls = []

    async def translate(self, original, language, glossary, max_tokens, *, style="direct"):
        self.calls.append(dict(language=language, style=style, original=original))
        await asyncio.sleep(.001)
        return dict(text="测试译文 " + original, tokens=20)


@pytest.fixture
def lab_client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, jwt_secret=SECRET), translator=FakeTranslator())
    with TestClient(app) as client:
        wid = client.post("/api/v1/workspaces", headers=auth(), json={"title": "lab"}).json()["id"]
        yield client, wid


def wait_job(client, base, path):
    for _ in range(300):
        response = client.get(base + path, headers=auth())
        response.raise_for_status()
        value = response.json()
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(.05)
    pytest.fail("Job did not finish")


def test_translation_source_alignment_reuse_and_owner_isolation(lab_client):
    client, wid = lab_client; base = f"/api/v1/workspaces/{wid}"
    source = client.post(base + "/sources", headers=auth(), json={"title": "paper", "kind": "paper", "blocks": [{"text": "Abstract\nThe dataset has 5,049 questions.\nEvidence matters.", "page": 2}]}).json()
    body = dict(source_id=source["id"], start_line=2, end_line=3, glossary={"dataset": "数据集"})
    response = client.post(base + "/translations", headers=auth(), json=body)
    assert response.status_code == 202, response.text
    item = wait_job(client, base, "/translations/" + response.json()["id"])
    assert item["status"] == "completed" and item["tokens_used"] == 20
    segment = item["segments"][0]
    assert segment["start_line"] == 2 and segment["end_line"] == 3 and "5,049" in segment["translated"]
    evidence = client.get(base + "/evidence/" + segment["evidence_id"], headers=auth()).json()
    assert evidence["quote"] == segment["original"] and evidence["locations"][0]["page"] == 2
    reused = client.post(base + "/translations", headers=auth(), json=body).json()
    assert reused["id"] == item["id"]
    assert client.get(base + "/translations/" + item["id"], headers=auth("bob")).status_code == 404
    exported = client.get(base + "/translations/" + item["id"] + "/export", headers=auth())
    assert "5,049" in exported.text and "原文版本" in exported.text


@pytest.mark.parametrize("text,requested,expected", [
    ("The model uses 128 samples.", "auto", "zh-CN"),
    ("我们评估模型的准确率。", "auto", "en"),
    ("我们比较 LLM 与 RAG 的实验结果。", "auto", "en"),
    ("A model is evaluated.", "en", "en"),
])
def test_automatic_translation_direction(text, requested, expected):
    assert resolve_target_language(text, requested) == expected


def test_translation_prompts_apply_style_without_changing_original():
    text = "We evaluate 128 samples with an accuracy of 83.2%."
    direct = translation_messages(text, "zh-CN", {}, "direct")
    academic = translation_messages(text, "zh-CN", {}, "academic")
    assert direct[0]["content"] != academic[0]["content"]
    assert "自然、清晰、简洁" in direct[0]["content"]
    assert "正式、客观、严谨" in academic[0]["content"]
    assert "不能夸大结论" in academic[0]["content"]
    assert json.loads(direct[1]["content"])["original_text"] == text
    assert direct[1] == academic[1]


def test_pasted_translation_modes_reach_provider_and_do_not_share_cache(lab_client):
    client, wid = lab_client; base = f"/api/v1/workspaces/{wid}"
    body = dict(text="我们在 128 个样本上评估模型，准确率为 83.2%。")
    direct = client.post(base + "/translations", headers=auth(), json=body)
    assert direct.status_code == 202, direct.text
    direct = wait_job(client, base, "/translations/" + direct.json()["id"])
    assert direct["status"] == "completed"
    assert direct["target_language"] == "en" and direct["style"] == "direct"
    assert direct["source_id"] is None and direct["segments"][0]["evidence_id"] is None
    assert direct["segments"][0]["start_line"] is None
    assert client.app.state.lab_runtime.translator.calls[-1]["style"] == "direct"
    academic = client.post(base + "/translations", headers=auth(), json={**body, "style": "academic"}).json()
    academic = wait_job(client, base, "/translations/" + academic["id"])
    assert academic["status"] == "completed"
    assert academic["style"] == "academic" and academic["id"] != direct["id"]
    assert client.app.state.lab_runtime.translator.calls[-1] == dict(language="en", style="academic", original=body["text"])
    reused = client.post(base + "/translations", headers=auth(), json={**body, "style": "academic", "target_language": "en"}).json()
    assert reused["id"] == academic["id"]
    exported = client.get(base + "/translations/" + academic["id"] + "/export", headers=auth()).text
    assert "学术翻译" in exported and "English" in exported
    assert client.post(base + "/translations", headers=auth(), json={**body, "style": "unsupported"}).status_code == 422


@pytest.mark.usefixtures("compute_analysis")
def test_analysis_api_exports_and_publishes_traceable_source(lab_client):
    client, wid = lab_client; base = f"/api/v1/workspaces/{wid}"
    response = client.post(base + "/datasets/upload", headers=auth(), files={"file": ("data.csv", b"a,b\n1,4\n2,5\n3,6")})
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert client.get(base + "/datasets/" + dataset["id"], headers=auth("bob")).status_code == 404
    response = client.post(base + "/analyses", headers=auth(), json=dict(dataset_id=dataset["id"], method="welch_t", columns=["a", "b"]))
    assert response.status_code == 202, response.text
    item = wait_job(client, base, "/analyses/" + response.json()["id"])
    assert item["status"] == "completed", item
    assert item["result"]["p_value"] == pytest.approx(.021311641128756727)
    assert client.get(base + "/analyses/" + item["id"] + "/export?format=json", headers=auth()).json()["dataset_hash"] == dataset["data_hash"]
    published = client.post(base + "/analyses/" + item["id"] + "/publish", headers=auth()).json()
    assert published["kind"] == "document"
    assert client.post(base + "/analyses/" + item["id"] + "/publish", headers=auth()).json()["id"] == published["id"]
    read = client.post(base + "/sources/read", headers=auth(), json=dict(source_id=published["id"], line_count=100)).json()
    assert dataset["data_hash"] in read["data"]["quote"]
    assert client.get(base + "/analyses/" + item["id"] + "/artifacts/model", headers=auth("bob")).status_code == 404
    assert client.post(base + "/analyses", headers=auth(), json=dict(dataset_id=dataset["id"], method="paired_t", columns=["a", "a"])).status_code == 422


def test_long_line_translation_offsets_reconstruct_original(tmp_path):
    store = Store(tmp_path); store.initialize()
    from research_backend.schemas import SourceCreate
    wid = store.create_workspace("alice", dict(title="long"))["id"]
    original = "word " * 1400
    source = store.add_source("alice", wid, SourceCreate(title="paper", blocks=[dict(text=original, page=1)]))
    job = prepare_translation(store, "alice", wid, TranslationCreate(source_id=source["id"]), "fake")
    assert "".join(segment["original"] for segment in job["segments"]) == original
    assert all(segment["start_line"] == 1 and segment["start_char"] is not None for segment in job["segments"])


def test_lstm_scaler_and_training_are_not_fitted_to_test_data(tmp_path, compute_analysis):
    pytest.importorskip("torch", reason="LSTM requires requirements-analysis-lstm.txt")
    values = 10 + np.arange(80) * .02 + np.sin(np.arange(80) / 4)
    data = dict(columns=["signal"], rows=[[float(v)] for v in values])
    parameters = AnalysisCreate(dataset_id="fixture", method="lstm", columns=["signal"], window=6, epochs=5).model_dump()
    first = compute_analysis(data, parameters, artifact_dir=tmp_path)
    changed = dict(columns=["signal"], rows=[[float(v + (1000 if i >= 64 else 0))] for i, v in enumerate(values)])
    second = compute_analysis(changed, parameters)
    assert first["split_index"] == second["split_index"] == 64
    assert first["scaler"] == second["scaler"]
    assert first["loss_history"] == second["loss_history"]
    assert first["scaler"]["mean"] == pytest.approx(values[:64].mean())
    assert len(first["forecast"]) == 12 and (tmp_path / "model.pt").exists()
    bad = dict(columns=["signal"], rows=[[None] if i == 30 else row for i, row in enumerate(data["rows"])])
    with pytest.raises(ValueError, match="缺失"):
        compute_analysis(bad, parameters)


@pytest.mark.asyncio
async def test_cancelled_analysis_terminates_worker_process(tmp_path, monkeypatch):
    store = Store(tmp_path); store.initialize()
    wid = store.create_workspace("alice", dict(title="cancel"))["id"]
    dataset = add_dataset(store, "alice", wid, "a.csv", b"a,b\n1,2\n3,4")
    job = store.put("alice", wid, "analysis", dict(dataset_id=dataset["id"], dataset_hash=dataset["data_hash"], parameters=params("paired_t"), status="queued", progress={}))
    stopped, launched = asyncio.Event(), asyncio.Event()
    class Process:
        returncode = None
        async def wait(self):
            await stopped.wait(); return self.returncode
        def terminate(self):
            self.returncode = -15; stopped.set()
    process = Process()
    async def start(*args, **kwargs):
        launched.set(); return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    runtime = LabRuntime(store, FakeTranslator())
    runtime.start("alice", wid, "analysis", job["id"])
    await launched.wait()
    store.update("alice", wid, "analysis", job["id"], status="cancelled")
    task = runtime.tasks[job["id"]]
    runtime.stop(job["id"])
    await asyncio.gather(task, return_exceptions=True)
    assert stopped.is_set()
    assert store.get("alice", wid, "analysis", job["id"])["status"] == "cancelled"
