"""What the learner sees while sitting, handing in, reading the result and reviewing mistakes (iPhone width, Chromium)."""
import psycopg
from playwright.sync_api import expect
from psycopg.rows import dict_row

from test_live_training import browser, confirm_submission, live_app, open_question  # noqa: F401
from test_report_ui import fill_all, start_exam


def answers_by_stem(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        return {r["content"]["stem"]: r["content"] for r in c.execute("SELECT content FROM reviewed_questions").fetchall()}


def grade_subjective(dsn, pid):
    from app import quiz_v2
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        for it in c.execute("SELECT question_id,snapshot FROM paper_items_v2 WHERE paper_id=%s", (pid,)).fetchall():
            s = it["snapshot"]
            if s["question_type"] in ("term", "short_answer", "essay"):
                rubric = s["rubric"]
                quiz_v2.grade(c, pid, it["question_id"], [r["points"] if n == 0 else 0 for n, r in enumerate(rubric)], "评语", "AI自动核验",
                              details=dict(criterion_reasons=["答对。"] + ["未涉及。"] * (len(rubric) - 1), blank_points=[], knowledge_tags=[]))
        c.commit()


def test_the_exam_screen_is_centred_and_says_things_once(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    page.evaluate("document.getElementById('a2hs-hint').hidden = false")        # even if the install hint wants to show
    expect(page.locator("#a2hs-hint")).to_be_hidden()
    page.wait_for_function("document.getAnimations().every((a) => a.playState !== 'running')")        # the page has finished sliding in
    centre = page.evaluate("""() => { const b = document.getElementById('clock').getBoundingClientRect(); return [b.left + b.width / 2, innerWidth / 2]; }""")
    assert abs(centre[0] - centre[1]) <= 3, centre                               # the clock is in the middle of the screen
    title = page.locator("#questions article:visible h2").first.inner_text()
    assert title.startswith("第") and "·" not in title.split("FSRS")[0], title    # just the number; subject, type, points are in the tag row
    assert "服务端" not in page.locator(".focus-progress").inner_text()
    assert not errors, errors


def test_while_essays_are_being_graded_there_is_no_big_zero_and_no_conclusion(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    fill_all(page, active)
    confirm_submission(page)
    page.locator("#result article").first.wait_for(timeout=20000)
    total = page.locator("#result .total-score").first
    expect(total).to_contain_text("判分中")
    assert not total.inner_text().strip().startswith("0")
    assert "得分率最低" not in page.locator("#result").inner_text()
    expect(page.locator("#result .key-card")).not_to_contain_text("0 / 75")
    grade_subjective(dsn, pid)
    page.reload()
    page.locator("#result article").first.wait_for(timeout=20000)
    expect(page.locator("#result .total-score").first).not_to_contain_text("判分中")
    expect(page.locator("#result")).to_contain_text("得分率最低")
    expect(page.locator("#grading-activity")).not_to_contain_text("尚未启用")
    expect(page.locator("#grading-activity")).to_contain_text("全部")
    assert not errors, errors


def test_review_goes_one_question_at_a_time_and_the_rating_follows_the_answer(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    for item in active["questions"]:                                               # every choice and blank wrong: they all come back for review
        open_question(page, item["id"])
        article = page.locator(f"article[data-id='{item['id']}']")
        if item["question_type"] == "single_choice":
            article.locator(".option").nth(1).click()
        elif item["question_type"] == "fill_blank":
            article.locator("input").first.fill("完全不对")
        else:
            article.locator("textarea").fill("一段主观题作答")
    page.wait_for_timeout(600)
    confirm_submission(page)
    page.locator("#result article").first.wait_for(timeout=20000)
    grade_subjective(dsn, pid)
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        c.execute("UPDATE fsrs_cards SET due=clock_timestamp()-interval '1 minute'")
        c.commit()
        due = c.execute("SELECT count(*) AS n FROM fsrs_cards").fetchone()["n"]
    assert due >= 4
    by_stem = answers_by_stem(dsn)
    page.evaluate("location.hash = '#/mistakes'")
    page.wait_for_timeout(1500)
    page.locator("#memory, button:has-text('开始复习')").first.click()
    expect(page.locator("#learning")).to_contain_text(f"第 1 / {due} 道")
    assert page.locator("#learning article").count() == 1                         # one question, not the whole list
    seen = {"right": 0, "wrong": 0}
    for index in range(due):
        expect(page.locator("#learning")).to_contain_text(f"第 {index + 1} / {due} 道")
        stem = page.locator("#learning .question-stem").inner_text()
        answer = by_stem[stem]["answer"]
        kind = by_stem[stem]["question_type"]
        reveal = page.locator("#learning button.primary").first
        if kind == "single_choice":
            wrong = index % 2 == 0
            letter = next(k for k in "ABCD" if (k != answer) == wrong)
            page.locator("#learning .review-choice", has_text=f"{letter}. ").click()
        elif kind == "fill_blank":
            wrong = index % 2 == 0
            page.locator("#learning .review-input").fill("完全不对的答案" if wrong else answer)
        else:
            wrong = None
        reveal.click()
        page.locator("#learning .rating-row").wait_for()
        buttons = page.locator("#learning .rating-row button")
        enabled = [buttons.nth(i).is_enabled() for i in range(4)]
        if kind in ("single_choice", "fill_blank"):
            expected = ([True, True, False, False] if kind == "single_choice" else [True, True, True, False]) if wrong else [False, False, True, True]
            assert enabled == expected, (kind, wrong, enabled)
            seen["wrong" if wrong else "right"] += 1
        buttons.nth(next(i for i, ok in enumerate(enabled) if ok)).click()
    expect(page.locator("#learning")).to_contain_text("本次复习完成")
    expect(page.locator("#learning")).to_contain_text(f"共复习 {due} 道")
    assert seen["wrong"] and seen["right"]
    assert not errors, errors
