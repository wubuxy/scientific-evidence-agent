"""Export only the synthetic smoke fixture; never export natural blind-review data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/app"))
from research_backend.store import Store


def export(state_dir):
    store = Store(state_dir)
    workspaces = store.workspaces("field-smoke")
    if len(workspaces) != 1 or workspaces[0]["title"] != "Synthetic integration fixture":
        raise ValueError("Only the synthetic smoke workspace can be exported")
    wid = workspaces[0]["id"]
    runs = store.list("field-smoke", wid, "run")
    sources = store.list("field-smoke", wid, "source")
    if len(runs) != 1 or len(sources) != 1:
        raise ValueError("Expected one synthetic run and one source")
    source = sources[0]
    text = store.source_text("field-smoke", wid, source["id"])
    if source["title"] != "Synthetic Paper A (not a real study)" or not text.startswith(
        "Synthetic fixture. No real experimental result is claimed."):
        raise ValueError("Refusing to publish a non-fixture source")
    run = runs[0]
    if run["status"] != "completed" or run["field_state"]["semantic_success"] is not None:
        raise ValueError("Expected a completed fixture with unconfirmed semantic quality")
    evidence = store.list("field-smoke", wid, "evidence")
    events = store.events("field-smoke", wid, run["id"])
    read_ids = set()
    for call in store.list("field-smoke", wid, "tool_call", parent_id=run["id"]):
        if call["name"] == "sources_read" and call["status"] == "succeeded":
            read_ids.add(call["data"]["id"])
    evidence = [e for e in evidence if e["id"] in read_ids]
    for field in run["field_state"]["fields"]:
        if not set(field["evidence_ids"]) <= read_ids:
            raise ValueError("Field references evidence outside this run")
    def pick(item, keys):
        return {k:item[k] for k in keys.split()}
    return dict(schema_version=1, synthetic=True,
        scope="Actual model call on invented text; fixed replay, not an accuracy evaluation",
        recording=pick(run, "id created_at model tokens_used tokens_estimated step question field_state"),
        source=dict(**pick(source, "id title kind uri version source_hash line_count rag_indexed created_at blocks"),
            text=text, content_sha256=hashlib.sha256(text.encode()).hexdigest()),
        evidence=[pick(e, "id source_id source_hash start_line end_line quote") for e in evidence],
        provenance=dict(script="scripts/smoke_field_state.py", generator="scripts/export_field_demo.py",
            original_run_sha256=hashlib.sha256((state_dir.parent / "run.json").read_bytes()).hexdigest(),
            generation_calls=sum(e["type"] == "model_usage" for e in events),
            review_calls=len(store.list("field-smoke", wid, "field_review", parent_id=run["id"]))),
        human_labels=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("frontend/public/demo/field-recording.json"))
    args = parser.parse_args()
    data = export(args.state_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Exported synthetic run {data['recording']['id']} to {args.out}")
