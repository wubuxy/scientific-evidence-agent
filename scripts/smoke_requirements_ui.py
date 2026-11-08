"""Exercise the real requirement editor with an intercepted, offline API.

Requires optional Playwright and Chromium, plus a running frontend. No backend
or real model is contacted; the production API-to-model path has backend tests.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def main(args):
    args.out.mkdir(parents=True, exist_ok=False)
    errors, submitted, runs = [], [], {}
    source_sets = {
        "alpha": [dict(id="paper-a", title="Paper A · Training and evaluation"), dict(id="paper-b", title="Paper B · Initialization")],
        "beta": [dict(id="paper-c", title="Paper C · Another workspace")],
    }
    def route_api(route):
        request = route.request
        path = urlparse(request.url).path
        payload = dict(items=[])
        if path == "/research-health": payload = dict(status="ok")
        elif path == "/api/v1/workspaces":
            payload = dict(items=[dict(id=wid, title=wid, research_question="Offline UI fixture", field="AI", created_at="2026-11-08T00:00:00Z") for wid in source_sets])
        else:
            parts = path.split("/")
            wid = parts[4]
            resource = parts[5]
            if resource == "sources":
                payload = dict(items=[dict(**source, kind="paper", uri=None, version="fixture", source_hash="fixture",
                    line_count=10, rag_indexed=False, created_at="2026-11-08T00:00:00Z") for source in source_sets[wid]])
            elif resource == "evidence" and len(parts) == 7:
                payload = dict(id="fixture-e1",source_id="paper-a",source_hash="fixture",title="Paper A",uri=None,
                    start_line=1,end_line=1,quote="Training uses X.",locations=[])
            elif resource == "sessions" and request.method == "POST":
                payload = dict(id="session-"+wid, title="Fixture", created_at="2026-11-08T00:00:00Z")
            elif resource == "runs" and request.method == "POST":
                body = request.post_data_json
                submitted.append(body)
                rid = "run-"+str(len(submitted))
                payload = dict(**body, id=rid, status="completed", model="offline-ui-fixture", step=0,
                    tokens_used=0, tokens_estimated=False, report=dict(title="离线交互样例 · 非实验结果",claims=[dict(
                        text="在相同训练设置下，结论可能适用于此任务。", evidence_ids=["fixture-e1"],kind="inference",qualification="须先确认训练设置一致。")],
                        comparison_rows=[],limitations=["仅用于界面检查；没有调用模型。"],verification_status="needs_human_review",
                        proposals=[dict(text="冻结独立论文任务并检查失败。",basis_claim_indices=[0],assumptions=["两位标注者可独立评审。"],
                            validation_plan="记录成功与失败，分别核对引用与推断。",status="proposed_not_executed")]),
                    error=None, created_at="2026-11-08T00:00:00Z")
                runs[rid] = payload
            elif resource == "runs" and len(parts) == 7:
                payload = runs[parts[6]]
        route.fulfill(status=202 if request.method == "POST" and "/runs" in path else 200,
                      content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({"executable_path": args.browser_path} if args.browser_path else {}))
        page = browser.new_page(viewport={"width":1440,"height":1100}, reduced_motion="reduce")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/v1/**", route_api)
        page.route("**/research-health", route_api)
        page.goto(args.url, wait_until="networkidle")
        page.locator("#workspace-select").select_option("alpha")
        page.get_by_text("运行前确认要求", exact=False).first.click()
        page.get_by_label("自行指定必答论文与字段").check()
        page.get_by_role("button", name="选择全部", exact=True).click()
        page.get_by_label("字段 1 名称").fill("训练数据")
        page.get_by_label("字段 1 完成标准").fill("区分训练与评测，并说明额外数据。")
        page.get_by_role("button", name="添加字段", exact=True).click()
        page.get_by_label("字段 2 名称").fill("训练初始化")
        page.get_by_label("字段 2 完成标准").fill("指出训练起点，不能用评测 checkpoint 替代。")
        page.get_by_label("研究问题", exact=True).fill("比较这两篇论文的训练条件。")
        page.locator(".requirement-editor").screenshot(path=str(args.out/"requirements-desktop.png"))
        page.evaluate("""() => {
          const key = 'research.appearance.v1';
          const value = JSON.stringify({mode:'dark', palette:'cool-teal'});
          localStorage.setItem(key, value);
          dispatchEvent(new StorageEvent('storage', {key, newValue:value}));
        }""")
        page.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--theme-surface').trim()==='#142235'")
        page.locator(".requirement-editor").screenshot(path=str(args.out/"requirements-dark.png"))

        # Invalid duplicate names and disabled tracking must not create a run.
        page.get_by_label("字段 2 名称").fill("训练数据")
        page.get_by_role("button", name="开始研究", exact=False).click()
        assert not submitted and page.locator(".requirement-error").is_visible()
        page.get_by_label("字段 2 名称").fill("训练初始化")
        page.locator(".composer-options .soft-button").click()
        page.get_by_label("字段检查").select_option("off")
        page.get_by_role("button", name="开始研究", exact=False).click()
        assert not submitted and "启用字段检查" in page.locator(".requirement-error").inner_text()
        page.get_by_label("字段检查").select_option("bounded")
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator(".requirement-editor").screenshot(path=str(args.out/"requirements-mobile.png"))
        page.get_by_role("button", name="开始研究", exact=False).click()
        page.wait_for_function("document.querySelector('.run-heading') !== null")
        assert len(submitted) == 1
        fields = submitted[0]["required_fields"]
        assert len(fields) == 4
        assert {f["source_id"] for f in fields} == {"paper-a", "paper-b"}
        assert {f["name"] for f in fields} == {"训练数据", "训练初始化"}
        assert fields[0]["criterion"] == "区分训练与评测，并说明额外数据。"
        page.get_by_role("tab",name="研究报告",exact=True).click()
        proposal = page.get_by_role("region",name="方案建议",exact=True)
        proposal.wait_for(state="visible")
        assert proposal.is_visible() and "尚未执行" in proposal.inner_text()
        assert "假设" in proposal.inner_text() and "验证计划" in proposal.inner_text()
        assert page.get_by_text("基于原文的推断",exact=True).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator(".report").screenshot(path=str(args.out/"report-proposal-mobile.png"))
        proposal.get_by_role("button").first.click()
        page.get_by_text("Training uses X.",exact=False).first.wait_for()
        page.get_by_role("button",name="关闭证据面板",exact=True).click()

        # Switching workspaces cannot carry selected papers or prior requirements.
        page.set_viewport_size({"width":1440,"height":1100})
        page.locator(".report").screenshot(path=str(args.out/"report-proposal-desktop.png"))
        page.locator("#workspace-select").select_option("beta")
        page.wait_for_function("document.querySelector('.workspace-summary')?.textContent.includes('1 份')")
        if not page.get_by_label("自行指定必答论文与字段").is_visible():
            page.locator(".requirement-editor summary").click()
        assert not page.get_by_label("自行指定必答论文与字段").is_checked()
        page.get_by_label("研究问题", exact=True).fill("梳理这篇论文。")
        page.get_by_role("button", name="开始研究", exact=False).click()
        page.wait_for_function("document.querySelector('.run-heading') !== null")
        assert len(submitted) == 2 and submitted[1]["required_fields"] == []
        assert not errors, errors
        browser.close()
    summary = dict(status="passed", mode="real UI with intercepted API; no model calls", submitted_runs=len(submitted),
                   explicit_pairs=4, mobile="390x844", themes=["light","dark"], workspace_reset=True, invalid_submissions_blocked=True,
                   proposal_render_and_basis_citation=True)
    (args.out/"summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5186")
    parser.add_argument("--browser-path")
    parser.add_argument("--out", type=Path, required=True)
    main(parser.parse_args())
