"""Exam-page behaviour from the third-party review, in a real browser (Chromium; the exam flow itself is engine-neutral)."""
from playwright.sync_api import expect

from test_live_training import browser, confirm_submission, live_app, open_question  # noqa: F401
from test_report_ui import fill_all, start_exam


def test_the_subject_label_follows_the_question_on_screen(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    bio = next(q for q in active["questions"] if q["subject"] == "biochemistry")
    cell = next(q for q in active["questions"] if q["subject"] == "cell_biology")
    open_question(page, cell["id"])
    expect(page.locator("#current-subject")).to_have_text("细胞生物学")
    expect(page.locator("#question-tags")).not_to_contain_text("生物化学")
    # answering only cell-biology questions never makes the exam header say 生物化学
    field = page.locator("#questions article:visible textarea, #questions article:visible input[type=text]").first
    if field.count():
        field.fill("x")
    page.wait_for_timeout(600)
    expect(page.locator("#current-subject")).to_have_text("细胞生物学")
    open_question(page, bio["id"])
    expect(page.locator("#current-subject")).to_have_text("生物化学")
    assert not errors, errors


def test_submitting_freezes_the_paper_and_a_failure_gives_it_back(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    fill_all(page, active)
    calls = []
    release = []

    def submit_route(route):
        calls.append(route.request.method)
        if len(calls) == 1:                       # the first try: held, then refused by the server
            release.append(route)
            return
        route.continue_()

    page.route("**/v2/papers/*/submit", submit_route)
    confirm_submission(page)
    expect(page.locator("#submit")).to_have_text("提交中…")
    expect(page.locator("#submit")).to_be_disabled()
    assert page.evaluate("document.getElementById('questions').inert") is True
    assert "is-submitting" in page.evaluate("document.body.className")
    assert len(release) == 1
    release[0].fulfill(status=500, content_type="application/json", body='{"detail":"服务器出错了"}')
    expect(page.locator("#submit")).to_have_text("交卷")
    expect(page.locator("#submit")).to_be_enabled()
    assert page.evaluate("document.getElementById('questions').inert") is False
    expect(page.locator("#status")).to_contain_text("交卷失败")
    expect(page.locator("#status")).to_contain_text("服务器出错了")
    # the retry goes through and the paper is submitted for good
    confirm_submission(page)
    page.locator("#result article").first.wait_for()
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["status"] == "submitted"
    assert not [e for e in errors if "500" not in e], errors
