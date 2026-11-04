"""Real filesystem, SQLite, JWT and ASGI boundaries; paid providers stay offline."""
import asyncio
import io
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from research_backend.api import create_app
from research_backend.config import Settings
from research_backend.ingest import parse_upload
from research_backend.schemas import Report, RunCreate, SourceCreate
from research_backend.store import NotFound, Store, Unavailable

SECRET = "test-only-secret-" * 4


def headers(subject="alice"):
    token = jwt.encode({"sub": subject, "exp": datetime.now(timezone.utc) + timedelta(minutes=10)}, SECRET)
    return {"Authorization": "Bearer " + token}


class FakeDiscovery:
    async def papers(self, query, limit):
        return {"results": [{"title": query, "abstract": "fixture only"}], "evidence_level": "metadata_and_abstract"}

    async def web(self, query, limit):
        raise Unavailable("web not configured")


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, jwt_secret=SECRET), discovery=FakeDiscovery())
    with TestClient(app) as client:
        yield client


def workspace(client, subject="alice"):
    response = client.post("/api/v1/workspaces", headers=headers(subject), json={"title": "组会"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def source(client, wid, text="训练设置\nbatch size = 64\nlearning rate = 0.001", **extra):
    body = dict(title="假设论文，仅用于测试", blocks=[dict(text=text, page=3)], **extra)
    response = client.post(f"/api/v1/workspaces/{wid}/sources", headers=headers(), json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_auth_requires_signed_expiring_token(client):
    assert client.get("/api/v1/workspaces").status_code == 401
    assert client.get("/api/v1/workspaces", headers={"Authorization": "Bearer invalid"}).status_code == 401
    token = jwt.encode({"sub": "alice"}, SECRET)
    assert client.get("/api/v1/workspaces", headers={"Authorization": "Bearer " + token}).status_code == 401
    assert client.get("/health").status_code == 200


def test_unconfigured_secret_does_not_enable_anonymous_access(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get("/api/v1/workspaces", headers=headers()).status_code == 503


def test_workspace_isolation_for_child_resources(client):
    wid = workspace(client)
    sid = source(client, wid)
    for path in (f"/workspaces/{wid}", f"/workspaces/{wid}/sources/{sid}",
                 f"/workspaces/{wid}/sources", f"/workspaces/{wid}/tools/calls"):
        assert client.get("/api/v1" + path, headers=headers("bob")).status_code == 404
    response = client.post(f"/api/v1/workspaces/{wid}/retrieval/search", headers=headers("bob"), json={"query": "batch"})
    assert response.status_code == 404
    assert client.get("/api/v1/workspaces", headers=headers("bob")).json()["items"] == []


def test_grep_read_and_stable_evidence(client):
    wid = workspace(client)
    sid = source(client, wid)
    base = f"/api/v1/workspaces/{wid}"
    result = client.post(base + "/retrieval/search", headers=headers(), json={"query": "BATCH SIZE"})
    assert result.status_code == 200, result.text
    hit = result.json()["hits"][0]
    assert hit["source_id"] == sid and hit["start_line"] == 2
    assert hit["locations"][0]["page"] == 3
    assert hit["quote"] == "batch size = 64"
    assert client.post(base + "/retrieval/search", headers=headers(), json={"query": "batch size"}).json()["hits"][0]["id"] == hit["id"]
    read = client.post(base + "/sources/read", headers=headers(), json={"source_id": sid, "line_count": 3})
    assert "learning rate" in read.json()["data"]["quote"]
    assert client.get(base + "/evidence/" + hit["id"], headers=headers()).json()["source_hash"] == hit["source_hash"]


def test_grep_limits_and_literal_query(client):
    wid = workspace(client)
    source(client, wid, "a.b\na.b\na.b\naXb\n--help")
    path = f"/api/v1/workspaces/{wid}/retrieval/search"
    response = client.post(path, headers=headers(), json={"query": "a.b", "limit": 2}).json()
    assert len(response["hits"]) == 2 and response["truncated"]
    assert client.post(path, headers=headers(), json={"query": "--help"}).json()["hits"][0]["quote"] == "--help"
    assert client.post(path, headers=headers(), json={"query": "nonexistent"}).json()["hits"] == []


def test_cross_workspace_source_id_and_path_are_rejected(client):
    first, second = workspace(client), workspace(client)
    sid = source(client, first)
    path = f"/api/v1/workspaces/{second}"
    assert client.post(path + "/sources/read", headers=headers(), json={"source_id": sid}).status_code == 404
    assert client.post(path + "/sources/read", headers=headers(), json={"source_id": "../../.env"}).status_code == 404
    assert client.post(path + "/retrieval/search", headers=headers(), json={"query": "batch", "source_ids": [sid]}).status_code == 404


def test_python_literal_fallback_is_explicit(client):
    wid = workspace(client)
    source(client, wid, "中文检索\nBatch Size")
    client.app.state.tools.grep.binary = None
    call = client.post(f"/api/v1/workspaces/{wid}/tools/calls", headers=headers(),
                       json={"name": "sources_grep", "arguments": {"query": "中文"}})
    assert call.json()["data"]["backend"] == "python_literal"
    assert call.json()["data"]["hits"][0]["start_line"] == 1


def test_rag_disabled_and_combined_partial_are_explicit(client):
    wid = workspace(client)
    source(client, wid)
    path = f"/api/v1/workspaces/{wid}/retrieval/search"
    assert client.post(path, headers=headers(), json={"query": "batch", "strategy": "rag"}).status_code == 503
    result = client.post(path, headers=headers(), json={"query": "batch", "strategy": "combined"}).json()
    assert result["hits"] and result["partial"] and result["warnings"]


@pytest.mark.parametrize("sql", ["DELETE FROM sources", "SELECT * FROM workspaces", "SELECT * FROM sqlite_master",
    "SELECT load_extension('x')", "SELECT randomblob(1000000000)", "SELECT 1; SELECT 2",
    "WITH x AS (SELECT * FROM sqlite_master) SELECT * FROM x"])
def test_database_rejects_out_of_scope_and_mutation(client, sql):
    wid = workspace(client)
    source(client, wid)
    result = client.post(f"/api/v1/workspaces/{wid}/database/query", headers=headers(), json={"sql": sql})
    assert result.status_code == 422, result.text


def test_database_projection_is_read_only_and_workspace_scoped(client):
    first, second = workspace(client), workspace(client)
    source(client, first)
    source(client, second)
    result = client.post(f"/api/v1/workspaces/{first}/database/query", headers=headers(),
                         json={"sql": "SELECT count(*) AS n FROM sources"})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["rows"] == [[1]]


def test_upload_original_and_pptx_page_mapping(client):
    wid = workspace(client)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="urn:test" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>ablation result</a:t></p:sld>')
    content = buffer.getvalue()
    response = client.post(f"/api/v1/workspaces/{wid}/sources/upload", headers=headers(),
                           files={"file": ("talk.pptx", content)})
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    assert client.get(f"/api/v1/workspaces/{wid}/sources/{sid}/original", headers=headers()).content == content
    hit = client.post(f"/api/v1/workspaces/{wid}/retrieval/search", headers=headers(), json={"query": "ablation"}).json()["hits"][0]
    assert hit["locations"][0]["page"] == 1


def test_video_is_not_silently_treated_as_parsed_text(client):
    wid = workspace(client)
    response = client.post(f"/api/v1/workspaces/{wid}/sources/upload", headers=headers(), files={"file": ("talk.mp4", b"video")})
    assert response.status_code == 422


def test_upload_metadata_preserves_attachment_and_retrieval_provenance(client):
    wid = workspace(client)
    base = f"/api/v1/workspaces/{wid}"
    content = b"research evidence\nretrieval budget = 100"
    response = client.post(base + "/sources/upload", headers=headers(), files={"file": ("notes.txt", content)},
                           data={"title": "实验笔记", "uri": "https://example.org/paper", "version": "v2"})
    assert response.status_code == 201, response.text
    item = response.json()
    assert (item["title"], item["uri"], item["version"]) == ("实验笔记", "https://example.org/paper", "v2")
    assert client.get(base + f"/sources/{item['id']}/original", headers=headers()).content == content
    result = client.post(base + "/sources/read", headers=headers(), json={"source_id": item["id"]}).json()
    assert result["data"]["title"] == "实验笔记"
    assert result["data"]["uri"] == "https://example.org/paper"
    call_path = base + f"/tools/calls/{result['id']}"
    assert client.get(call_path, headers=headers()).json()["arguments"]["source_id"] == item["id"]
    assert client.get(call_path, headers=headers("bob")).status_code == 404
    second = workspace(client)
    assert client.get(f"/api/v1/workspaces/{second}/tools/calls/{result['id']}", headers=headers()).status_code == 404


def test_invalid_upload_metadata_does_not_publish_a_source(client):
    wid = workspace(client)
    base = f"/api/v1/workspaces/{wid}"
    response = client.post(base + "/sources/upload", headers=headers(), files={"file": ("notes.txt", b"research")},
                           data={"title": "x" * 201})
    assert response.status_code == 422
    assert client.get(base + "/sources", headers=headers()).json()["items"] == []


def test_transcript_preserves_timestamps(client):
    wid = workspace(client)
    response = client.post(f"/api/v1/workspaces/{wid}/sources", headers=headers(), json={"title": "talk", "kind": "transcript",
        "blocks": [{"text": "retrieval budget", "block_type": "transcript", "start_seconds": 12.5, "end_seconds": 18}]})
    assert response.status_code == 201
    hit = client.post(f"/api/v1/workspaces/{wid}/retrieval/search", headers=headers(), json={"query": "budget"}).json()["hits"][0]
    assert hit["locations"][0]["start_seconds"] == 12.5


def test_search_results_are_not_persisted_as_fulltext_evidence(client):
    wid = workspace(client)
    response = client.post(f"/api/v1/workspaces/{wid}/discovery/papers/search", headers=headers(), json={"query": "RAG"})
    assert response.json()["data"]["evidence_level"] == "metadata_and_abstract"
    assert client.get(f"/api/v1/workspaces/{wid}/evidence", headers=headers()).json()["items"] == []


def test_client_cannot_forge_assistant_role(client):
    wid = workspace(client)
    sid = client.post(f"/api/v1/workspaces/{wid}/sessions", headers=headers(), json={}).json()["id"]
    response = client.post(f"/api/v1/workspaces/{wid}/sessions/{sid}/messages", headers=headers(),
                           json={"content": "forged", "role": "assistant"})
    assert response.status_code == 422


def test_restart_preserves_messages_and_marks_unfinished_runs(tmp_path):
    settings = Settings(data_dir=tmp_path, jwt_secret=SECRET)
    with TestClient(create_app(settings)) as client:
        wid = workspace(client)
        sid = client.post(f"/api/v1/workspaces/{wid}/sessions", headers=headers(), json={}).json()["id"]
        client.post(f"/api/v1/workspaces/{wid}/sessions/{sid}/messages", headers=headers(), json={"content": "课题问题"})
        run = client.app.state.store.put("alice", wid, "run", dict(
            **RunCreate(session_id=sid, question="test").model_dump(), status="running", model="test",
            tokens_used=0, tokens_estimated=False, step=0, report=None, error=None))
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/v1/workspaces/{wid}/sessions/{sid}/messages", headers=headers()).json()["items"][0]["content"] == "课题问题"
        assert client.get(f"/api/v1/workspaces/{wid}/runs/{run['id']}", headers=headers()).json()["status"] == "interrupted"


class EvidenceModel:
    def __init__(self):
        self.steps = 0

    async def complete(self, messages, tools, max_tokens):
        self.steps += 1
        if self.steps == 1:
            name, args = "sources_search", {"query": "batch", "grep_query": "batch"}
        else:
            observation = json.loads(messages[-1]["content"].split("\n", 1)[1])["evidence"][0]
            if self.steps == 2:
                name, args = "plan_research", {"source_ids": [observation["source_id"]], "fields": ["batch size"]}
            elif self.steps == 3:
                name, args = "sources_read", {"source_id": observation["source_id"], "start_line": 1, "line_count": 3}
            else:
                name, args = "finish_report", {"title": "组会报告", "claims": [{"text": "batch size 为 64", "evidence_ids": [observation["id"]]}],
                    "comparison_rows": [], "limitations": ["仅测试一份假设资料，未经科研有效性审核"]}
        return {"tokens": 100, "message": {"role": "assistant", "tool_calls": [{"id": f"call_{self.steps}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}


def test_full_research_run_with_evidence_report_and_sse(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, jwt_secret=SECRET), model=EvidenceModel())) as client:
        wid = workspace(client)
        source(client, wid)
        base = f"/api/v1/workspaces/{wid}"
        sid = client.post(base + "/sessions", headers=headers(), json={}).json()["id"]
        created = client.post(base + "/runs", headers=headers(), json={"session_id": sid, "question": "训练配置是什么？", "max_tokens": 50000, "memory_mode": "evidence"})
        assert created.status_code == 202, created.text
        rid = created.json()["id"]
        for _ in range(100):
            run = client.get(base + f"/runs/{rid}", headers=headers()).json()
            if run["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert run["status"] == "completed", run
        report = client.get(base + f"/runs/{rid}/report", headers=headers()).json()
        assert report["report"]["verification_status"] == "needs_human_review"
        assert "batch size" in report["markdown"]
        stream = client.get(base + f"/runs/{rid}/events/stream", headers=headers())
        assert "event: report_ready" in stream.text and "event: done" in stream.text
        events = client.get(base + f"/runs/{rid}/events", headers=headers()).json()["items"]
        assert client.get(base + f"/runs/{rid}/events", params={"after": events[-1]["id"]}, headers=headers()).json()["items"] == []
        history = client.get(base + f"/sessions/{sid}/messages", headers=headers()).json()["items"]
        assert [m["role"] for m in history] == ["user", "assistant"]


def test_fabricated_evidence_is_rejected(client):
    wid = workspace(client)
    report = Report(title="fake", claims=[{"text": "unsupported", "evidence_ids": ["ev_forged"]}], limitations=["test"]).model_dump()
    with pytest.raises(NotFound):
        client.app.state.runtime.validate_report("alice", wid, report)


def test_report_drafts_preserve_original_arguments_and_workspace_access(client):
    wid, other = workspace(client), workspace(client)
    store = client.app.state.store
    run = store.put("alice", wid, "run", {"status": "completed"})
    draft = store.put("alice", wid, "report_draft", {"arguments": {"paper_count": 1585},
        "validation_status": "rejected", "normalized_columns": []}, parent_id=run["id"])
    path = f"/api/v1/workspaces/{wid}/runs/{run['id']}/drafts"
    assert client.get(path, headers=headers()).json()["items"][0]["arguments"] == draft["arguments"]
    assert client.get(path, headers=headers("bob")).status_code == 404
    assert client.get(f"/api/v1/workspaces/{other}/runs/{run['id']}/drafts", headers=headers()).status_code == 404


def test_tool_registry_respects_run_permissions(client):
    definitions = client.app.state.tools.definitions("grep", False)
    names = {d["function"]["name"] for d in definitions}
    assert "sources_search" in names
    assert not names & {"sources_grep", "sources_rag", "papers_search", "web_search", "papers_collect"}


def test_openapi_generation_does_not_initialize_database(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "not-created"))
    schema = app.openapi()
    assert "/api/v1/workspaces/{wid}/retrieval/search" in schema["paths"]
    assert not (tmp_path / "not-created").exists()


def test_pptx_reordered_slides_keep_presentation_page_numbers():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="r2"/><p:sldId id="2" r:id="r1"/></p:sldIdLst></p:presentation>')
        archive.writestr("ppt/_rels/presentation.xml.rels", '<Relationships><Relationship Id="r1" Target="slides/slide1.xml"/><Relationship Id="r2" Target="slides/slide2.xml"/></Relationships>')
        for index in (1, 2):
            archive.writestr(f"ppt/slides/slide{index}.xml", f'<s xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>slide {index}</a:t></s>')
    document, _ = parse_upload("talk.pptx", buffer.getvalue())
    assert [(b.text, b.page) for b in document.blocks] == [("slide 2", 1), ("slide 1", 2)]




class SlowModel:
    async def complete(self, messages, tools, max_tokens):
        await asyncio.sleep(30)


def test_cancel_stops_running_task_and_persists_event(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, jwt_secret=SECRET), model=SlowModel())) as client:
        wid = workspace(client)
        base = f"/api/v1/workspaces/{wid}"
        sid = client.post(base + "/sessions", headers=headers(), json={}).json()["id"]
        run = client.post(base + "/runs", headers=headers(), json={"session_id": sid, "question": "test"}).json()
        assert client.post(base + f"/runs/{run['id']}/cancel", headers=headers()).json()["status"] == "cancelled"
        assert client.get(base + f"/runs/{run['id']}", headers=headers()).json()["status"] == "cancelled"
        events = client.get(base + f"/runs/{run['id']}/events", headers=headers()).json()["items"]
        assert any(e["type"] == "run_cancelled" for e in events)


async def test_new_evaluation_records_known_paraphrase_failure():
    from research_backend.evaluation import evaluate
    path = Path(__file__).parent / "fixtures/research_retrieval_smoke.json"
    report = await evaluate(path)
    assert report["task"] == "retrieval_only_not_report_accuracy"
    assert report["case_count"] == 3
    assert report["records"][-1]["source_recall"] == 0
    assert len(report["dataset_sha256"]) == 64


async def test_arxiv_provider_parses_atom_and_caches_without_network(monkeypatch):
    import httpx
    from research_backend import providers
    actual_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "export.arxiv.org"
        return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/test</id><title> A paper </title><summary>Abstract only</summary><author><name>Alice</name></author><published>2026-01-01</published><updated>2026-02-01</updated></entry></feed>')

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(handle), **kwargs))
    discovery = providers.Discovery(Settings())
    first = await discovery.papers("retrieval", 5)
    second = await discovery.papers("retrieval", 5)
    assert first == second and len(requests) == 1
    assert first["results"][0]["authors"] == ["Alice"]
    assert first["evidence_level"] == "metadata_and_abstract"


async def test_provider_errors_are_not_empty_success(monkeypatch):
    import httpx
    from research_backend import providers
    actual_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: actual_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503)), **kwargs))
    with pytest.raises(Unavailable):
        await providers.Discovery(Settings()).papers("test", 5)
    with pytest.raises(Unavailable):
        await providers.Discovery(Settings()).web("test", 5)


def test_evidence_preserves_leading_whitespace(client):
    wid = workspace(client)
    sid = source(client, wid, "    indented text\nnext line")
    result = client.post(f"/api/v1/workspaces/{wid}/sources/read", headers=headers(),
                         json={"source_id": sid, "line_count": 1})
    assert result.json()["data"]["quote"] == "    indented text"


def test_recursive_sql_has_execution_budget(client):
    wid = workspace(client)
    result = client.post(f"/api/v1/workspaces/{wid}/database/query", headers=headers(), json={"sql":
        "WITH RECURSIVE t(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM t) SELECT max(x) FROM t"})
    assert result.status_code == 422


def test_grep_agent_regex_and_public_literal_are_distinct(client):
    wid = workspace(client)
    source(client, wid, "alpha\nbeta\na.b\naXb")
    base = f"/api/v1/workspaces/{wid}"
    result = client.post(base + "/tools/calls", headers=headers(),
        json={"name": "sources_grep", "arguments": {"query": "alpha|beta"}})
    assert result.status_code == 200
    assert len(result.json()["data"]["hits"]) == 2
    assert result.json()["data"]["match_mode"] == "regex"
    literal = client.post(base + "/retrieval/search", headers=headers(), json={"query": "a.b"})
    assert len(literal.json()["hits"]) == 1
    assert client.post(base + "/retrieval/search", headers=headers(), json={"query": "[", "regex": True}).status_code == 422


def test_regex_does_not_fall_back_to_unsafe_python_regex(client):
    wid = workspace(client)
    source(client, wid)
    client.app.state.tools.grep.binary = None
    result = client.post(f"/api/v1/workspaces/{wid}/retrieval/search", headers=headers(),
        json={"query": "a|b", "regex": True})
    assert result.status_code == 503


async def test_web_search_falls_back_transparently_to_bocha(monkeypatch):
    import httpx
    from research_backend import providers
    original = httpx.AsyncClient
    hosts = []

    def handle(request):
        hosts.append(request.url.host)
        if request.url.host == "google.serper.dev":
            return httpx.Response(403, json={"message": "Unauthorized"})
        return httpx.Response(200, json={"code": 200, "data": {"webPages": {"value": [
            {"name": "paper", "url": "https://arxiv.org/abs/test", "summary": "test snippet"}]}}})

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    result = await providers.Discovery(Settings(serper_api_key="fixture", bocha_api_key="fixture")).web("query", 3)
    assert hosts == ["google.serper.dev", "api.bochaai.com"]
    assert result["provider"] == "bocha" and result["warnings"] == ["serper: HTTP 403"]


def test_compacted_context_keeps_ids_and_original_evidence_unchanged():
    from research_backend.runtime import archive_old_observations, model_view
    original = {"id": "ev_example", "source_id": "paper", "start_line": 1, "end_line": 20,
                "quote": "evidence " * 200, "locations": [{"page": 2, "block_type": "text"}] * 20}
    view = model_view(original)
    assert len(json.dumps(view)) < len(json.dumps(original))
    messages = [{"role": "tool", "content": json.dumps({"data": view, "tool_call_id": str(i)})} for i in range(5)]
    archive_old_observations(messages)
    archived = json.loads(messages[0]["content"])["data"]
    assert archived["id"] == "ev_example" and archived["pages"] == [2]
    assert archived["quote_truncated"] and len(archived["quote"]) == 200
    assert len(original["quote"]) > 200
    assert not json.loads(messages[-1]["content"])["data"].get("archived")


def test_research_config_has_priority_and_relative_paths_ignore_cwd(tmp_path, monkeypatch):
    from research_backend import config
    monkeypatch.setattr(config, "__file__", str(tmp_path / "app/research_backend/config.py"))
    for key in ("JWT_SECRET_KEY", "DASHSCOPE_API_KEY", "RESEARCH_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env.research").write_text("JWT_SECRET_KEY=research-fixture\nRESEARCH_DATA_DIR=runtime\n", encoding="utf-8")
    (tmp_path / ".env").write_text("JWT_SECRET_KEY=legacy-fixture\nDASHSCOPE_API_KEY=key-fixture\n", encoding="utf-8")
    config.load_environment()
    settings = config.Settings.from_env()
    assert settings.jwt_secret == "research-fixture" and settings.llm_api_key == "key-fixture"
    assert settings.data_dir == tmp_path / "runtime"


def test_english_source_does_not_trigger_a_false_token_budget_failure(tmp_path):
    class LargeObservationModel:
        source_id = None
        steps = 0

        async def complete(self, messages, tools, max_tokens):
            self.steps += 1
            if self.steps == 1:
                name, arguments = "sources_read", {"source_id": self.source_id, "line_count": 1}
                tokens = 10000
            else:
                evidence = json.loads(messages[-1]["content"].split("\n", 1)[1])["evidence"][0]
                name, arguments = "finish_report", {"title": "预算验证", "claims": [{"text": "读取到测试资料", "evidence_ids": [evidence["id"]]}], "limitations": ["人工构造的测试资料"]}
                tokens = 1000
            calls = [{"id": str(self.steps), "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]
            if self.steps == 1:
                calls.insert(0, {"id": "plan", "type": "function", "function": {"name": "plan_research",
                    "arguments": json.dumps({"source_ids": [self.source_id], "fields": ["training"]})}})
            return {"tokens": tokens, "message": {"role": "assistant", "tool_calls": calls}}

    model = LargeObservationModel()
    with TestClient(create_app(Settings(data_dir=tmp_path, jwt_secret=SECRET), model=model)) as client:
        wid = workspace(client)
        model.source_id = source(client, wid, "training " * 1300)
        base = f"/api/v1/workspaces/{wid}"
        sid = client.post(base + "/sessions", headers=headers(), json={}).json()["id"]
        run_id = client.post(base + "/runs", headers=headers(), json={"session_id": sid, "question": "读取资料", "max_tokens": 20000, "memory_mode": "evidence"}).json()["id"]
        for _ in range(100):
            run = client.get(base + "/runs/" + run_id, headers=headers()).json()
            if run["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert run["status"] == "completed", run
        assert run["tokens_used"] == 11000


async def test_finalization_is_forced_in_provider_request(monkeypatch):
    import openai
    from types import SimpleNamespace
    from research_backend.providers import ChatModel
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                model_dump=lambda **kwargs: {"role": "assistant", "content": "fixture"}))],
                usage=SimpleNamespace(total_tokens=20, prompt_tokens=10))

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    response = await ChatModel(Settings(llm_api_key="fixture")).complete([], [{"type": "function", "function": {"name": "finish_report"}}], 1000)
    assert captured["tool_choice"] == {"type": "function", "function": {"name": "finish_report"}}
    assert response["prompt_tokens"] == 10
