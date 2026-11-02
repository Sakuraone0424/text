"""Part 2 in a real browser at iPhone width: the progress page after login."""
import os
from pathlib import Path

from playwright.sync_api import expect

from test_live_training import browser, live_app, login  # noqa: F401

PHONE = dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))


def test_the_page_asks_for_login_then_shows_ten_buckets_without_horizontal_scroll(live_app, browser):
    client, dsn, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    page = browser.new_context(**PHONE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/progress")
    expect(page.locator("#notice")).to_contain_text("需要登录")
    login(page, base)
    page.goto(base + "/progress")
    expect(page.locator("#content")).to_be_visible()
    expect(page.locator("#bank .row")).to_have_count(10)
    expect(page.locator("#bank .row:visible")).to_have_count(5)  # one subject at a time
    page.locator("[data-subject=cell_biology]").click()
    expect(page.locator("#bank .row:visible")).to_have_count(5)
    expect(page.locator("#bank .group:visible .name").first).to_have_text("单选")
    expect(page.locator("#paper-main")).to_contain_text("还差 60 题")
    expect(page.locator("#worker")).to_have_text("没有记录")
    expect(page.locator("#worker-note")).to_contain_text("还没有留下运行记录")
    expect(page.locator(".subbar h1")).to_have_text("出题进度")
    expect(page.locator("#period")).not_to_be_empty()
    expect(page.locator("#spend-month")).to_contain_text("¥")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / "part2-progress.png"), full_page=True)
    # the home page links to it
    page.goto(base + "/training")
    expect(page.locator("#open-progress")).to_be_visible()
    assert not errors, errors


def test_history_is_folded_reasons_are_chinese_and_codes_hide_in_details(live_app, browser):
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page = browser.new_context(**PHONE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, base)
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        uid = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
        for n, code in enumerate(["invalid_qa_automation_grant", "OSError", "OSError"]):
            conn.execute(
                "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code) VALUES (%s,%s,'generation','blocked','{}',%s,%s)",
                (f"gpt-{n}", uid, Jsonb({"subject": "biochemistry", "question_type": "term", "max_points": 4}), code))
        conn.execute(
            "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code,result) VALUES ('now-bad',%s,'generation','blocked','{}',%s,'invalid_or_uncertain_result',%s)",
            (uid, Jsonb({"subject": "biochemistry", "question_type": "term", "max_points": 6}),
             Jsonb({"dispatch_failure": {"code": "output_schema_violation", "errors": [{"field": "rubric", "reason": "rubric_points_mismatch"}]}})))
    page.goto(base + "/progress")
    expect(page.locator("#legacy")).to_be_visible()
    expect(page.locator("#legacy-summary")).to_have_text("历史记录（旧规格）3 条，点开查看明细")
    assert not page.locator("#legacy").evaluate("(n) => n.open")  # folded until opened
    page.locator("#legacy-summary").click()
    expect(page.locator("#legacy-list li")).to_have_count(2)
    expect(page.locator("#legacy-list code").first).to_have_text("OSError")
    # the current cell counts only the current-specification task, and its codes sit inside "查看原因"
    term = page.locator("#bank .row", has_text="名词解释").first  # biochemistry is the default switch
    expect(term).to_contain_text("被挡住 1")
    assert "output_schema_violation" not in term.locator(".notes").inner_text()
    expect(term.locator("details.reasons-detail")).to_have_count(1)
    expect(page.locator("#batch-progress")).to_contain_text("已入库")
    expect(page.locator("#review-rate")).to_have_text("—")
    expect(page.locator("#bank .row .count").first).to_contain_text("两天储备")
    # a task row: Chinese inline, the code only inside 详情
    row = page.locator("#jobs li", has_text="出题 · 生化名词解释").first
    expect(row).to_contain_text("模型输出不符合题目格式")
    assert "output_schema_violation" not in row.locator(".kind, .state, .meta:not(.detail)").all_inner_texts().__str__()
    row.locator("details summary").click()
    expect(row.locator("code").first).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert not errors, errors
