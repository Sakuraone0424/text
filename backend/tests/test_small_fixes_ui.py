"""Part 3 small fixes in a real browser at iPhone width: model page, submit reminder, cancel button."""
import psycopg
from playwright.sync_api import expect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from test_live_training import browser, confirm_submission, live_app, login, open_question  # noqa: F401
from test_report_ui import PHONE, fill_all, start_exam  # noqa: F401


def test_the_model_page_has_a_price_button_and_shows_the_task_limits_without_errors(live_app, browser):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page = browser.new_context(**PHONE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/static/model.html")
    page.locator("#password").fill("test-live-login")
    page.locator("#login button").click()
    expect(page.locator("#controls")).to_be_visible()
    expect(page.locator("#price-refresh")).to_be_visible()
    summary = page.locator("details:has(#token-limit) > summary")
    expect(summary).to_contain_text("任务限制：输出长度、重试次数、同时运行数")
    expect(page.locator("#token-limit")).to_be_visible()  # open by default
    expect(page.locator("#retry-limit")).to_be_visible()
    expect(page.locator("#max-concurrency")).to_be_visible()
    expect(page.locator("label:has(#upgrade)")).to_contain_text("需质量对比通过后才会切换")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert not errors, errors  # the old handler on a missing element is gone


def test_confirming_the_submit_names_unanswered_and_marked_questions_and_jumps_to_the_first_open_one(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    questions = active["questions"]
    first, second = questions[0], questions[1]
    open_question(page, first["id"])
    article = page.locator(f"article[data-id='{first['id']}']")
    article.locator(".option").first.click() if first["question_type"] == "single_choice" else article.locator("input,textarea").first.fill("答")
    open_question(page, second["id"])
    page.locator("#toggle-mark").click()  # marked "look again", still unanswered
    page.locator("#submit").click()
    expect(page.locator("#submit-warning")).to_contain_text(f"还有 {len(questions) - 1} 题未答")
    expect(page.locator("#submit-warning")).to_contain_text("1 题标记稍后看")
    page.locator("#jump-unanswered").click()
    expect(page.locator("#submit-dialog")).not_to_be_visible()
    shown = page.locator("#questions article:not([hidden])")
    expect(shown).to_have_attribute("data-id", str(second["id"]))  # first unanswered, in paper order
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["status"] == "in_progress"  # nothing was submitted
    fill_all(page, active)
    page.locator("#submit").click()
    expect(page.locator("#submit-warning")).to_have_text("1 题标记稍后看")  # all answered, one still marked
    expect(page.locator("#jump-unanswered")).to_be_hidden()
    assert not errors, errors


def test_the_progress_page_can_cancel_a_waiting_task(live_app, browser):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        uid = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
        job = conn.execute(
            "INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload) VALUES ('ui-cancel',%s,'generation','{}',%s) RETURNING id",
            (uid, Jsonb({"subject": "biochemistry", "question_type": "term"})),
        ).fetchone()["id"]
    page = browser.new_context(**PHONE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    login(page, base)
    page.goto(base + "/progress")
    button = page.locator("#jobs .cancel")
    expect(button).to_have_count(1)
    assert button.bounding_box()["height"] >= 44
    button.click()
    expect(page.locator("#jobs .cancel")).to_have_count(0)
    expect(page.locator("#jobs li").first).to_contain_text("已取消")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT state,error_code FROM ai_jobs WHERE id=%s", (job,)).fetchone()
    assert (row["state"], row["error_code"]) == ("cancelled", "cancelled_by_owner")
    assert not errors, errors
