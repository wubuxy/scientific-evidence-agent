"""Exercise the real local Docker gateway, paper imports and persisted reads; no LLM."""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5183")
    parser.add_argument("--state", type=Path, default=Path("test-results/demo-state.json"))
    parser.add_argument("--verify-state", action="store_true")
    args = parser.parse_args()

    def request(path, body=None, headers=None):
        encoded = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(args.url + path, data=encoded, headers={"Content-Type": "application/json", **(headers or {})})
        with urllib.request.urlopen(req, timeout=20) as response:
            content = response.read()
            return json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content

    assert b'root' in request("/demo")
    request("/research-health")
    recording = request("/demo/recording.json")
    if args.verify_state:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        workspace = request("/api/v1/workspaces/" + state["workspace_id"])
        assert workspace["id"] == state["workspace_id"]
        for item in state["sources"]:
            evidence = request("/api/v1/workspaces/" + workspace["id"] + "/sources/read", dict(source_id=item["id"], start_line=1, line_count=20))
            assert evidence["data"]["source_hash"] == item["source_hash"]
        print("PASS: workspace, source versions and authenticated reads survive container restart.")
        return

    workspace = request("/api/v1/workspaces", dict(title="演示 · Docker 启动检查", research_question=recording["recording"]["question"]))
    base = "/api/v1/workspaces/" + workspace["id"]
    imported = []
    for source in recording["sources"]:
        blocks = request(source["blocks_file"])
        item = request(base + "/sources", dict(title=source["title"], kind="paper", uri=source["uri"], blocks=blocks))
        assert item["source_hash"] == source["source_hash"]
        read = request(base + "/sources/read", dict(source_id=item["id"], start_line=1, line_count=80))["data"]
        original = next(e for e in recording["evidence"] if e["source_id"] == source["id"])
        assert read["quote"] == original["quote"]
        imported.append(dict(id=item["id"], source_hash=item["source_hash"]))
    hits = request(base + "/tools/calls", dict(name="sources_grep", arguments=dict(query="BART", source_ids=[imported[0]["id"]])))
    assert hits["data"]["match_mode"] == "regex" and hits["data"]["hits"]
    try:
        request("/api/v1/workspaces", headers={"Origin": "https://example.invalid"})
    except urllib.error.HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("The local gateway accepted an unrelated Origin")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(dict(workspace_id=workspace["id"], sources=imported), indent=2) + "\n", encoding="utf-8")
    print("PASS: replay assets, JWT proxy, canonical imports, real ripgrep and local Origin guard.")


if __name__ == "__main__":
    main()
