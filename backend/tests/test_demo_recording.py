"""Published recordings must retain canonical paper text and real read provenance."""
import hashlib
import json
from pathlib import Path

from research_backend.schemas import SourceCreate
from research_backend.store import Store


DEMO = Path(__file__).resolve().parents[2] / "frontend/public/demo"


def load_recording():
    return json.loads((DEMO / "recording.json").read_text(encoding="utf-8"))


def test_demo_papers_reimport_with_original_versions_and_line_addresses(tmp_path):
    data = load_recording()
    store = Store(tmp_path)
    store.initialize()
    workspace = store.create_workspace("offline", {"title": "demo integrity"})
    for source in data["sources"]:
        text = (DEMO / source["file"].removeprefix("/demo/")).read_text(encoding="utf-8")
        blocks = json.loads((DEMO / source["blocks_file"].removeprefix("/demo/")).read_text(encoding="utf-8"))
        imported = store.add_source("offline", workspace["id"], SourceCreate(title=source["title"], kind="paper", blocks=blocks))
        assert imported["source_hash"] == source["source_hash"]
        assert store.source_text("offline", workspace["id"], imported["id"]) == text
        assert hashlib.sha256(text.encode()).hexdigest() == source["content_sha256"]
        assert len(text.splitlines()) == source["line_count"]


def test_demo_citations_were_read_and_post_review_window_is_kept_separate():
    data = load_recording()
    evidence = {item["id"]: item for item in data["evidence"]}
    read_ids = {eid for call in data["recording"]["tool_calls"] if call["name"] == "sources_read" and call["status"] == "succeeded" for eid in call["evidence_ids"]}
    sources = {item["id"]: item for item in data["sources"]}
    for claim in data["recording"]["report"]["claims"]:
        assert claim["evidence_ids"]
        for identifier in claim["evidence_ids"]:
            assert identifier in read_ids
            item = evidence[identifier]
            text = (DEMO / sources[item["source_id"]]["file"].removeprefix("/demo/")).read_text(encoding="utf-8")
            assert item["quote"] == "\n".join(text.splitlines()[item["start_line"]-1:item["end_line"]])
    correction = data["failure"]["correction"]
    assert correction["scope"] == "post_run_manual_review"
    assert "evidence_id" not in correction
    assert all(not (item["source_id"] == correction["source_id"] and item["start_line"] <= correction["start_line"] <= item["end_line"]) for item in evidence.values())
    text = (DEMO / sources[correction["source_id"]]["file"].removeprefix("/demo/")).read_text(encoding="utf-8")
    assert correction["quote"] == "\n".join(text.splitlines()[correction["start_line"]-1:correction["end_line"]])
