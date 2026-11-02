"""Step 7 in a real browser at iPhone width: report button, excluded totals, typed answers only."""
import psycopg
import pytest
from playwright.sync_api import expect
from psycopg.rows import dict_row

from test_live_training import browser, confirm_submission, live_app, login, open_question  # noqa: F401

PHONE = dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)


def start_exam(client, blueprint, browser, accept_dialogs=True):
    base = str(client.base_url).rstrip("/")
    page = browser.new_context(**PHONE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if accept_dialogs:
        page.on("dialog", lambda d: d.accept())
    login(page, base)
    page.locator("#start").click()
    page.locator("#questions article:not([hidden])").wait_for()
    pid = client.post("/v2/papers", json={"specification": "xiaohan-150-60-v1", "quotas": blueprint.quotas}).json()["paper_id"]
    return page, pid, client.get(f"/v2/papers/{pid}").json(), errors


def fill_all(page, active):
    for item in active["questions"]:
        open_question(page, item["id"])
        article = page.locator(f"article[data-id='{item['id']}']")
        if item["question_type"] == "single_choice":
            article.locator(".option").first.click()
        elif item["question_type"] == "fill_blank":
            article.locator("input").first.fill("fixture solution")
        else:
            article.locator("textarea").fill("Actual synthetic subjective explanation")
    page.wait_for_timeout(600)


def test_reporting_a_question_in_the_exam_is_recorded_and_excluded_from_the_result(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    term = next(i for i in active["questions"] if i["question_type"] == "term")
    fill_all(page, active)
    open_question(page, term["id"])
    button = page.locator(f"article[data-id='{term['id']}'] .question-report")
    expect(button).to_have_text("这题有问题")
    box = button.bounding_box()
    assert box["height"] >= 40  # a thumb-sized target
    button.click()
    expect(button).to_have_text("已反馈 · 本卷不计分")
    expect(button).to_be_disabled()
    expect(page.locator("#progress")).to_contain_text("已反馈1题不计分")
    assert client.get(f"/v2/papers/{pid}").json()["reported_question_ids"] == [term["id"]]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT question_id FROM question_reports").fetchone()["question_id"] == term["id"]
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    confirm_submission(page)
    page.locator("#result article").first.wait_for()
    expect(page.locator("#result").first).to_contain_text("已扣除 1 道反馈题共 4 分")
    reported_row = page.locator(f'#result article[data-qid="{term["id"]}"]')
    expect(reported_row).to_contain_text("已反馈 · 不计分")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert not errors, errors


def test_a_graded_answer_shows_each_criterion_score_with_its_reason_and_pending_ones_a_reason(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    fill_all(page, active)
    confirm_submission(page)
    page.locator("#result article").first.wait_for()
    graded = next(i for i in active["questions"] if i["question_type"] == "short_answer")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        from app import quiz_v2

        rubric = conn.execute("SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s AND question_id=%s",
                              (pid, graded["id"])).fetchone()["snapshot"]["rubric"]
        scores = [c["points"] if n == 0 else 0 for n, c in enumerate(rubric)]
        quiz_v2.grade(conn, pid, graded["id"], scores, "模拟评语", "AI自动核验",
                      details=dict(criterion_reasons=["要点一答对了。"] + ["该要点未涉及，未得分。"] * (len(rubric) - 1),
                                   blank_points=[], knowledge_tags=[]))
        conn.commit()
    page.locator("#grading-activity button").click()
    row = page.locator(f'#result article[data-qid="{graded["id"]}"]')
    row.locator("summary").click()
    expect(row).to_contain_text("评分点 1")
    expect(row).to_contain_text("理由：要点一答对了。")
    page.wait_for_function("[...document.querySelectorAll('.grading-status')].every(e => e.textContent !== '判分中……')", timeout=15000)
    reasons = page.locator(".grading-status").all_text_contents()
    assert reasons and all(r and r != "判分中……" for r in reasons)  # a plain reason, never a blank or a zero
    assert not any("人工" in r for r in reasons) and "人工" not in page.locator("#grading-activity").inner_text()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert not errors, errors


@pytest.fixture
def photos_off(monkeypatch):
    monkeypatch.delenv("QUIZ_PHOTO_ANSWERS_ENABLED", raising=False)


def test_without_photo_answers_the_exam_offers_typing_only(photos_off, live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    assert active["photo_answers_enabled"] is False
    assert client.get("/v2/training-specification").json()["photo_answers_enabled"] is False
    for item in active["questions"]:
        if item["question_type"] in ("term", "short_answer"):
            open_question(page, item["id"])
            article = page.locator(f"article[data-id='{item['id']}']")
            expect(article.locator("textarea")).to_have_count(1)
            expect(article.locator("input[type=file]")).to_have_count(0)
            expect(article).not_to_contain_text("拍照上传")
    term = next(i for i in active["questions"] if i["question_type"] == "term")
    refused = client.post(f"/v2/papers/{pid}/images/{term['id']}?expected_version=0", content=b"x")
    assert refused.status_code == 410
    assert not errors, errors


def test_the_service_worker_cache_only_holds_static_files_and_was_bumped(live_app):
    client, _, _ = live_app
    worker = client.get("/training-sw.js").text
    assert "v13-small-fixes" in worker
    for private in ("/v2/", "result", "grading", "history"):
        assert private not in worker.split("const ASSETS")[1].split("];")[0]
