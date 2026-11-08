"""Optional Playwright smoke check of an extracted, offline review packet.

Creates synthetic form entries only in an isolated browser profile. Never use
its exported labels as human experiment data. No network or model is required.
"""
import argparse
import json
from pathlib import Path


def main(args):
    from playwright.sync_api import sync_playwright
    args.out.mkdir(parents=True,exist_ok=False)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=args.browser)
        page=browser.new_page(viewport=dict(width=1440,height=1000),accept_downloads=True)
        errors=[]; external=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:external.append(r.url) if r.url.startswith(('http:','https:')) else None)
        page.goto(args.page.resolve().as_uri())
        assert page.locator('.criterion').count()==6
        assert page.locator('.criterion select').first.input_value()==''
        page.screenshot(path=str(args.out/'review-desktop.png'))
        first=page.locator('#report-id').inner_text()
        page.get_by_role('textbox',name='评审者',exact=True).fill('synthetic-browser-test')
        page.locator('.criterion select').first.select_option('1')
        assert page.locator('.issue').first.inner_text()
        page.locator('.criterion textarea').first.fill('Synthetic UI test only; not a human annotation.')
        assert not page.locator('.issue').first.inner_text()
        page.get_by_role('button',name='下一份').click()
        page.get_by_role('button',name='上一份').click()
        page.reload()
        assert page.locator('#report-id').inner_text()==first
        assert page.locator('.criterion select').first.input_value()=='1'
        with page.expect_download() as info:
            page.get_by_role('button',name='导出 labels.json').click()
        info.value.save_as(args.out/'SYNTHETIC-UI-LABELS-NOT-HUMAN.json')
        labels=json.loads((args.out/'SYNTHETIC-UI-LABELS-NOT-HUMAN.json').read_text(encoding='utf-8'))
        assert labels['annotator']=='synthetic-browser-test'
        assert labels['reports'][0]['items'][0]['score']==1
        assert labels['reports'][0]['items'][1]['score'] is None
        page.set_viewport_size(dict(width=390,height=844))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(args.out/'review-mobile.png'))
        assert not errors and not external,(errors,external)
        browser.close()
    result=dict(status='passed',browser_errors=errors,external_requests=external,human_labels_created=0,
        checks=['blank defaults','score/reason feedback','navigation','reload persistence','JSON export','mobile overflow'])
    (args.out/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--browser',default=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    main(parser.parse_args())
