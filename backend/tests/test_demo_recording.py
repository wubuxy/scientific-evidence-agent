"""Published recordings must retain canonical paper text and real read provenance."""
import hashlib
import json
from pathlib import Path

from research_backend.schemas import SourceCreate
from research_backend.store import Store
from research_backend.field_state import FieldLedger


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


def test_field_demo_preserves_source_lines_and_version_bound_candidates(tmp_path):
    data = json.loads((DEMO / "field-recording.json").read_text(encoding="utf-8"))
    source, state = data["source"], data["recording"]["field_state"]
    store = Store(tmp_path); store.initialize()
    wid = store.create_workspace("offline", {"title":"synthetic replay integrity"})["id"]
    imported = store.add_source("offline", wid, SourceCreate(title=source["title"], blocks=source["blocks"]))
    assert imported["source_hash"] == source["source_hash"]
    assert store.source_text("offline", wid, imported["id"]) == source["text"]
    assert hashlib.sha256(source["text"].encode()).hexdigest() == source["content_sha256"]
    reads = {e["id"]:e for e in data["evidence"]}
    ledger = FieldLedger([{k:f[k] for k in ("source_id","name","criterion")} for f in state["fields"]])
    for e in reads.values():
        assert e["source_id"] == source["id"] and e["source_hash"] == source["source_hash"]
        assert e["quote"] == "\n".join(source["text"].splitlines()[e["start_line"]-1:e["end_line"]])
    for field in state["fields"]:
        ledger.record({k:field[k] for k in ("source_id","name","answer","evidence_ids")}, reads)
        assert ledger.fields[field["source_id"],field["name"]]["candidate_hash"] == field["candidate_hash"]
        if field["review"]:
            assert field["review"]["candidate_hash"] == field["candidate_hash"]
            assert field["review"]["authority"] == "model_candidate" and field["review"]["needs_human_review"]
        assert all(v["revision"] < field["revision"] for v in state["previous_versions"] if v["field_id"] == field["id"])


def test_field_demo_costs_and_boundaries_match_published_smoke():
    data = json.loads((DEMO / "field-recording.json").read_text(encoding="utf-8"))
    smoke = json.loads((DEMO.parents[2] / "docs/results/field-state-v1/smoke-summary.json").read_text(encoding="utf-8"))
    run, state = data["recording"], data["recording"]["field_state"]
    assert data["synthetic"] and data["human_labels"] == 0
    assert data["source"]["text"].startswith("Synthetic fixture. No real experimental result is claimed.")
    assert run["step"] == smoke["model_calls"] == data["provenance"]["generation_calls"] + data["provenance"]["review_calls"]
    assert run["tokens_used"] == smoke["tokens"] and not run["tokens_estimated"]
    assert state["present"] == sum(f["coverage"] == "present" for f in state["fields"]) == smoke["present"]
    assert state["total"] == len(state["fields"]) == smoke["fields"]
    assert state["unresolved"] == state["total"] - state["present"] == smoke["unresolved"]
    assert state["semantic_success"] is None and state["repair_rounds"] == 0
    assert state["stop_reason"] == smoke["stop_reason"] == "final_reserve"
