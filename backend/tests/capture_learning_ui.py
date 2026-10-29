"""Manual review captures from disposable synthetic fixtures. Never connect to production.

TEST_DATABASE_URL=<explicit loopback test DB> python tests/capture_learning_ui.py --output <folder>
All screenshots carry a synthetic-data label. No real textbook, grades or credentials are exported.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
import psycopg
from psycopg.conninfo import conninfo_to_dict
from playwright.sync_api import sync_playwright
from test_live_training import live_app, login, open_question, confirm_submission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    info = conninfo_to_dict(os.environ.get("TEST_DATABASE_URL", ""))
    if info.get("host") not in {"127.0.0.1", "localhost", "::1"} or not info.get("dbname", "").startswith(("ustc_ui_", "quiz_test")):
        raise SystemExit("Explicit loopback-only disposable UI test database required")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ui-capture-") as tmp:
        fixture = live_app.__wrapped__(Path(tmp))
        client, dsn, _ = next(fixture)
        try:
            base = str(client.base_url).rstrip("/")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("QUIZ_BROWSER_EXECUTABLE", "/usr/bin/chromium"))
                context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
                context.add_init_script("""document.addEventListener('DOMContentLoaded',()=>{const label=document.createElement('div');label.textContent='隔离UI回归样例 · 非正式题库或用户成绩';label.style.cssText='padding:6px 12px;background:#fffbeb;color:#854a0e;font:11px sans-serif;text-align:center';document.body.prepend(label);});""")
                page = context.new_page()
                stems = {
                    "single_choice": "某实验现象只在条件甲与乙同时成立时出现。已知甲成立、乙不成立，下列哪项推断符合题面约束？",
                    "fill_blank": "根据题面给定的限定条件，填写具有明确含义的答案。____（仅用于输入与保存回归，不属于正式题库）",
                    "term": "说明核心定义及其成立条件。请使用自己的语言，区分定义、适用范围与相关概念。（隔离界面回归题）",
                    "short_answer": "请根据给定条件分点阐述机制，说明关键环节与结论之间的逻辑联系。（隔离界面回归题）",
                    "essay": "围绕给定材料，组织核心概念、条件、机制联系与结论，注意论证层次。（隔离界面回归题）",
                }
                def synthetic_copy(route):
                    if route.request.method != "GET" and not route.request.url.endswith("/start"):
                        route.continue_(); return
                    response = route.fetch()
                    try: data = response.json()
                    except ValueError: route.fulfill(response=response); return
                    for q in data.get("questions", data.get("answers", [])):
                        q["stem"] = stems[q["question_type"]]
                        if q.get("options"):
                            q["options"] = {"A": "现有条件不满足该现象出现的必要条件", "B": "只要条件甲成立，现象就一定出现", "C": "现有结果说明条件甲没有作用", "D": "可以忽略条件乙对结果的限制"}
                    route.fulfill(response=response, json=data)
                page.route("**/v2/papers/**", synthetic_copy)
                page.goto(base); page.locator("#password").fill("test-live-login"); page.locator("#login button").click(); page.locator("#login").wait_for(state="hidden"); page.wait_for_function("document.querySelector('#metric-submitted').textContent==='0'")
                page.screenshot(path=str(args.output / "after-home-390.png"), full_page=True)
                page.locator("#prepare").click(); page.locator("#start").wait_for(); page.screenshot(path=str(args.output / "after-prepared-390.png"), full_page=True)
                page.locator("#start").click(); page.locator("#questions article:visible").wait_for(); page.locator("#questions .option:visible").nth(1).click(); page.locator("#toggle-mark").click()
                page.wait_for_function("Object.keys(pending).length===0")
                page.screenshot(path=str(args.output / "after-choice-390.png"), full_page=False)
                page.set_viewport_size({"width": 430, "height": 932}); page.screenshot(path=str(args.output / "after-choice-430.png"), full_page=False)
                page.set_viewport_size({"width": 390, "height": 844}); page.locator("#question-map").click(); page.screenshot(path=str(args.output / "after-question-map-390.png"), full_page=False); page.keyboard.press("Escape")
                questions = page.evaluate("paper.questions")
                term = next(q for q in questions if q["question_type"] == "term")
                open_question(page, term["id"])
                page.locator("#questions textarea:visible").fill("先给出核心定义，再说明必要条件与适用范围。\n\n这里是隔离UI回归样例，不是真实考试作答。")
                page.wait_for_function("Object.keys(pending).length===0"); page.locator("#current-number").click()
                page.screenshot(path=str(args.output / "after-subjective-390.png"), full_page=False)
                page.set_viewport_size({"width": 834, "height": 1194}); page.screenshot(path=str(args.output / "after-subjective-834.png"), full_page=False)
                page.locator("#leave-focus").click(); page.screenshot(path=str(args.output / "after-home-834.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 900}); page.screenshot(path=str(args.output / "after-home-1440.png"), full_page=True)
                page.locator('[data-view="exam"]').first.click(); page.set_viewport_size({"width": 390, "height": 844}); confirm_submission(page); page.locator(".grade-title").first.wait_for()
                page.screenshot(path=str(args.output / "after-result-390.png"), full_page=False)
                page.locator(".result-analytics > section").nth(2).scroll_into_view_if_needed(); page.screenshot(path=str(args.output / "after-result-analysis-390.png"), full_page=False)
                with psycopg.connect(dsn) as conn:
                    conn.execute("UPDATE fsrs_cards SET due=clock_timestamp()")  # Disposable synthetic test rows only.
                page.locator('[data-view="mistakes"]').click(); page.locator("#memory").click(); page.locator("#learning article").first.wait_for(); page.screenshot(path=str(args.output / "after-mistakes-390.png"), full_page=False)
                page.locator('[data-view="profile"]').first.click(); page.screenshot(path=str(args.output / "after-profile-390.png"), full_page=False)
                page.set_viewport_size({"width": 834, "height": 1194}); page.locator('[data-view="exam"]').first.click(); page.screenshot(path=str(args.output / "after-result-834.png"), full_page=False)
                browser.close()
            print("Synthetic UI review captures saved; no production access")
        finally:
            fixture.close()


if __name__ == "__main__":
    main()
