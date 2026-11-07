"""Capture real static-replay interactions for README media; makes no model calls.

Optional developer tools: pip install playwright; playwright install chromium;
ffmpeg on PATH. Start the frontend before running this from the repository root.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def main(args):
    from playwright.sync_api import sync_playwright
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Install ffmpeg on PATH to encode the recorded UI frames")
    args.work.mkdir(parents=True, exist_ok=True)
    args.assets.mkdir(parents=True, exist_ok=True)
    errors, api_requests = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({"executable_path":args.browser_path} if args.browser_path else {}))
        page = browser.new_page(viewport={"width":1440,"height":1160}, device_scale_factor=1, reduced_motion="reduce")
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: api_requests.append(r.url) if "/api/v1/" in r.url else None)
        page.goto(args.url.rstrip('/')+"/demo/fields", wait_until="networkidle")
        page.get_by_role("heading", name="每个问题，都有去向").wait_for()
        assert page.locator(".field-card").count() == 3
        assert page.locator(".field-card.has-gap").count() == 1
        page.locator(".replay-main").screenshot(path=str(args.assets/"field-demo-light.png"))
        page.get_by_role("button", name="切换深色").click()
        page.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--theme-surface').trim()==='#142235'")
        page.locator(".replay-main").screenshot(path=str(args.assets/"field-demo-dark.png"))
        frames = []
        bounds = page.locator(".replay-main").bounding_box()
        clip = dict(x=bounds["x"], y=bounds["y"], width=bounds["width"], height=1040)
        def capture(name):
            path=args.work/(name+".png")
            # Identical frame dimensions prevent ffmpeg filter reinitialization
            # from dropping earlier scenes when a panel changes document height.
            page.screenshot(path=str(path), clip=clip)
            frames.append(path.resolve())
        capture("01-fields")
        page.get_by_role("button",name="训练数据的证据 1",exact=True).click()
        assert page.locator(".replay-lines>div").count() == 5
        assert "Train-X" in page.locator(".replay-lines").inner_text()
        capture("02-evidence")
        page.get_by_role("button",name="03 版本记录").click()
        assert page.locator(".replay-current").count() == 3
        capture("03-versions")
        page.get_by_role("button",name="04 停止与成本").click()
        assert "15,814" in page.locator(".replay-metrics").inner_text()
        assert "semantic_success: null" in page.locator(".replay-decisions").inner_text()
        capture("04-stop")
        # Exercise all four narrow-screen views and their natural scrolling.
        page.set_viewport_size({"width":390,"height":844})
        for label in ("01 字段台账","02 原文证据","03 版本记录","04 停止与成本"):
            page.get_by_role("button",name=label).click()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"),label
        page.screenshot(path=str(args.work/"mobile.png"),full_page=True)
        assert not errors,errors
        assert not api_requests,api_requests
        browser.close()
    concat=args.work/"frames.ffconcat"
    concat.write_text("ffconcat version 1.0\n"+"".join(f"file '{p.name}'\nduration 4\n" for p in frames)
        +f"file '{frames[-1].name}'\n",encoding="utf-8")
    output=args.assets/"field-demo.gif"
    subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-y","-f","concat","-safe","0","-i",str(concat),
        "-vf","fps=5,scale=1120:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3",
        "-t",str(len(frames)*4),"-loop","0",str(output)],check=True)
    result=dict(status="passed",real_ui=True,synthetic_source=True,model_calls=0,api_requests=0,
        views=4,mobile="390x844",themes=["light","dark"],gif_bytes=output.stat().st_size,
        animation="four actual UI captures; fixed four-second pauses, not real-time model execution")
    (args.work/"validation.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:5183")
    parser.add_argument("--browser-path")
    parser.add_argument("--work",type=Path,default=Path("test-results/showcase"))
    parser.add_argument("--assets",type=Path,default=Path("docs/assets"))
    main(parser.parse_args())
