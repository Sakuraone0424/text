"""Part 4 in a real browser at iPhone width: design system pages, charts, timing, practice, PWA."""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
from playwright.sync_api import expect
from psycopg.rows import dict_row

from stats_helpers import item, paper
from test_live_training import browser, confirm_submission, live_app, login, open_question  # noqa: F401
from test_report_ui import PHONE, fill_all, start_exam

SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))
LEARNER = {"x-api-key": "learner-live"}


def seed(client, dsn):
    """Eleven days of real-looking synthetic papers for the learner (rows inserted directly)."""
    client.get("/v2/stats/overview", headers=LEARNER)  # creates the user row
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        uid = conn.execute("SELECT id FROM users WHERE username='test-owner'").fetchone()["id"]
        today = conn.execute("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS d").fetchone()["d"]
        chapters = {"biochemistry": ["糖代谢", "脂代谢", "核酸代谢", "蛋白质合成"], "cell_biology": ["细胞骨架", "膜转运", "细胞周期"]}
        for k, back in enumerate(range(10, -1, -1)):
            day = today - timedelta(days=back)
            if conn.execute("SELECT 1 FROM papers_v2 WHERE user_id=%s AND paper_date=%s", (uid, day)).fetchone():
                continue  # an earlier test of this module already sat today's paper
            items = []
            for subject, subs in chapters.items():
                for n in range(8):
                    kind = ["single_choice", "fill_blank", "term", "single_choice", "fill_blank", "short_answer", "single_choice", "essay"][n]
                    pts = {"single_choice": 4, "fill_blank": 4, "term": 6, "short_answer": 12, "essay": 18}[kind]
                    # accuracy improves with the days; essays are the weak spot
                    got = pts if (n + k + (3 if subject == "cell_biology" else 0)) % 4 and kind != "essay" else (pts // 2 if kind == "essay" else 0)
                    items.append(item(subject, kind, pts, got, subs[n % len(subs)], seconds=40 + 30 * n))
            paper(conn, uid, day, items, when=datetime(day.year, day.month, day.day, 13, 0, tzinfo=timezone.utc), minutes=48 + k)
        paper(conn, uid, today - timedelta(days=20), [item(points=2, got=1)] * 6, spec="xiaohan-60q-150pt-v2",
              when=datetime.now(timezone.utc) - timedelta(days=20))
        # the rows above went in directly, so the review cards a real grading would have made are made here
        from app import memory

        for row in conn.execute("SELECT a.attempt_id,a.question_id FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id "
                                "JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id WHERE p.user_id=%s AND a.points < (i.snapshot->>'max_points')::int", (uid,)).fetchall():
            memory.record_failure(conn, row["attempt_id"], row["question_id"])
        qid = item  # keep linters quiet
        del qid


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name), full_page=True)


def no_overflow(page):
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), page.url


def test_the_data_pages_use_real_numbers_and_fit_a_phone(live_app, browser):
    client, dsn, blueprint = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page = browser.new_context(**PHONE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/training")
    page.locator("#password").fill("test-live-login")
    page.locator("#login button").click()
    page.locator("#login").wait_for(state="hidden")
    # ---- home
    expect(page.locator("#days-left")).not_to_have_text("—")
    page.wait_for_function("!document.querySelector('#days-left').dataset.rolling")      # the number rolls in for 400 ms
    days = int(page.locator("#days-left").inner_text())
    assert days == max(0, (date(2026, 12, 19) - datetime.now(timezone(timedelta(hours=8))).date()).days)
    assert int(page.locator("#streak-days").inner_text()) >= 10
    expect(page.locator("#stat-avg")).not_to_have_text("—")
    expect(page.locator("#stat-objective")).to_contain_text("%")
    expect(page.locator("#weak-chapters .progress-line")).to_have_count(5)
    expect(page.locator("#weak-practice")).to_be_visible()
    expect(page.locator("#spec-summary")).to_contain_text("300 分")
    expect(page.locator("nav.primary-nav button")).to_have_text(["首页", "练习", "错题", "我的"])
    assert page.locator(".sidebar").bounding_box()["height"] >= 56  # 56 + the bottom safe area
    assert page.locator("#prepare").bounding_box()["height"] >= 48
    no_overflow(page)
    shot(page, "part4-home.png")
    # ---- my learning data
    page.locator('nav.primary-nav [data-view="profile"]').click()
    expect(page.locator("#profile-data > section")).to_have_count(7)
    expect(page.locator("#profile-data")).to_contain_text("完成卷数")
    expect(page.locator("#profile-data .chart-svg")).not_to_have_count(0)
    expect(page.locator("#profile-data .heat-cell")).to_have_count(56)
    expect(page.locator("#profile-data")).not_to_contain_text("即将推出")
    expect(page.locator("#profile-data")).to_contain_text("各题型平均每题用时")
    assert not errors, errors
    no_overflow(page)
    # tapping a point shows its number
    page.locator("#profile-data .chart-dot").last.click()
    expect(page.locator("#profile-data .chart-readout.on").first).to_contain_text("%")
    # the score / rate switch redraws the same chart in points, for the latest specification only
    page.locator("#profile-data .segmented button", has_text="分数").click()
    expect(page.locator("#profile-data .chart-conclusion").first).to_contain_text("分数模式只显示")
    shot(page, "part4-profile.png")
    # a chapter row opens a chapter practice (the synthetic bank has no ready questions: the reason is shown)
    page.locator("#profile-data .chapter-row").first.click()
    expect(page.locator("#practice-panel")).to_contain_text("暂时没有可练的已入库客观题")
    # ---- mistakes with filters
    page.locator('nav.primary-nav [data-view="mistakes"]').click()
    expect(page.locator("#mistake-groups .mistake-card").first).to_be_visible()
    total = page.locator("#mistake-groups .mistake-card").count()
    page.locator("#filter-toggle").click()
    page.locator("#filter-type button", has_text="问答").click()
    assert page.locator("#mistake-groups .mistake-card").count() < total
    page.locator("#filter-type button", has_text="全部题型").click()
    page.locator("#filter-subject button", has_text="细胞").click()
    page.wait_for_function("() => { const l = [...document.querySelectorAll('#mistake-groups .mistake-card')]; return l.length > 0 && l.every((i) => i.dataset.subject === 'cell_biology'); }")
    assert not errors, errors
    no_overflow(page)
    shot(page, "part4-mistakes.png")
    # ---- history
    page.locator('nav.primary-nav [data-view="profile"]').click()
    page.locator("#profile-history").click()
    expect(page.locator("#learning .chart-svg")).not_to_have_count(0)
    expect(page.locator("#learning article").first).to_contain_text("用时")
    no_overflow(page)
    shot(page, "part4-history.png")
    assert not errors, errors


def test_the_exam_page_follows_the_layout_and_records_time_per_question(live_app, browser):
    client, dsn, blueprint = live_app
    page, pid, active, errors = start_exam(client, blueprint, browser)
    questions = active["questions"]
    first = questions[0]
    open_question(page, first["id"])
    expect(page.locator("#current-points")).to_contain_text("分")
    expect(page.locator("#current-subject")).to_be_visible()
    expect(page.locator("#question-tags")).to_be_visible()
    assert page.locator("#focus-actions").evaluate("(n) => getComputedStyle(n).position") == "fixed"
    assert page.locator("#next-question").bounding_box()["height"] >= 48
    assert page.locator("#clock").evaluate("(n) => parseFloat(getComputedStyle(n).fontSize)") == 20
    expect(page.locator("#toggle-mark")).to_contain_text("标记稍后看")
    no_overflow(page)
    shot(page, "part4-exam.png")
    page.wait_for_timeout(3200)  # dwell on the first question
    page.locator("#next-question").click()  # leaving it saves the time spent
    page.wait_for_timeout(1500)
    draft = client.get(f"/v2/papers/{pid}").json()["timing"]["drafts"].get(str(first["id"]), {})
    assert draft.get("seconds", 0) >= 3, draft
    fill_all(page, active)
    confirm_submission(page)
    page.locator("#result article").first.wait_for()
    expect(page.locator("#result .score-split")).to_be_visible()
    expect(page.locator("#result")).to_contain_text("题型得分率")
    page.wait_for_timeout(1500)
    expect(page.locator("#result .compare")).not_to_be_empty()
    expect(page.locator("#result .overtime-note")).not_to_be_empty()
    no_overflow(page)
    shot(page, "part4-result.png")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        timings = conn.execute("SELECT question_id,seconds FROM answer_timings").fetchall()
    assert any(t["question_id"] == first["id"] and t["seconds"] >= 3 for t in timings)
    assert not errors, errors


def test_a_chapter_practice_runs_with_instant_feedback_and_a_summary(live_app, browser):
    client, dsn, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    page = browser.new_context(**PHONE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    sets = {
        "set_id": 7, "mode": "weak", "counts_toward_daily_paper": False,
        "questions": [
            {"id": 11, "subject": "biochemistry", "question_type": "single_choice", "stem": "哪种酶催化糖酵解的限速步骤？", "max_points": 4, "chapter": "糖代谢",
             "options": {"A": "己糖激酶", "B": "磷酸果糖激酶-1", "C": "丙酮酸激酶", "D": "烯醇化酶"}},
            {"id": 12, "subject": "biochemistry", "question_type": "fill_blank", "stem": "糖酵解发生在细胞的____中。", "max_points": 4, "chapter": "糖代谢", "blank_count": 1},
        ],
    }
    seen = []

    def start(route):
        route.fulfill(json=sets)

    def answer(route):
        body = route.request.post_data_json
        seen.append(body)
        ok = body["question_id"] == 11 and body.get("text") == "B"
        route.fulfill(json={"question_id": body["question_id"], "correct": ok, "points": 4 if ok else 0, "max_points": 4, "answer": "B" if body["question_id"] == 11 else "细胞质",
                            "accepted_answers": [] if body["question_id"] == 11 else ["细胞质", "胞质溶胶"], "blank_points": None, "why": ["指出限速酶"]})

    page.route("**/v2/practice", start)
    page.route("**/v2/practice/7/answer", answer)
    login(page, base)
    page.locator("#weak-practice").evaluate("(n) => n.click()") if False else None
    page.goto(base + "/training")
    expect(page.locator("#days-left")).not_to_have_text("—")
    page.evaluate("window.Insights.startPractice({mode: 'weak', size: 2})")
    panel = page.locator("#practice-panel")
    expect(panel).to_contain_text("练习 1 / 2")
    expect(panel).to_contain_text("不计入每日成绩")
    panel.locator(".option", has_text="磷酸果糖激酶").click()
    panel.get_by_role("button", name="提交答案").click()
    expect(panel.locator(".practice-feedback.ok")).to_contain_text("回答正确")
    panel.get_by_role("button", name="下一题").click()
    panel.locator("input").fill("线粒体")
    panel.get_by_role("button", name="提交答案").click()
    expect(panel.locator(".practice-feedback.bad")).to_contain_text("细胞质 / 胞质溶胶")
    panel.get_by_role("button", name="查看结果").click()
    expect(panel).to_contain_text("2 道里答对 1 道")
    expect(panel).to_contain_text("不计入每日成绩")
    assert [b["question_id"] for b in seen] == [11, 12] and all(0 <= b["seconds"] <= 7200 for b in seen)
    no_overflow(page)
    shot(page, "part4-practice.png")
    assert not errors, errors


def test_the_app_can_be_added_to_the_home_screen(live_app, browser):
    client, dsn, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert data["display"] == "standalone" and data["theme_color"] == "#F4F6FA" and data["start_url"] == "/training"
    sizes = {i["sizes"]: i["purpose"] for i in data["icons"]}
    assert "192x192" in sizes and "512x512" in sizes
    for icon in data["icons"] + [{"src": "/static/icons/apple-touch-icon.png"}]:
        response = client.get(icon["src"])
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    html = client.get("/training").text
    for needle in ('rel="manifest"', 'rel="apple-touch-icon"', 'apple-mobile-web-app-capable', "Noto+Sans+SC"):
        assert needle in html
    # the one-time tip shows on an iPhone browser, goes away when dismissed, and stays away
    ios = browser.new_context(**PHONE, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1")
    page = ios.new_page()
    login(page, base)
    page.goto(base + "/training")
    expect(page.locator("#a2hs-hint")).to_be_visible()
    expect(page.locator("#a2hs-hint")).to_contain_text("需要重新登录一次")
    page.locator("#a2hs-dismiss").click()
    expect(page.locator("#a2hs-hint")).to_be_hidden()
    page.reload()
    expect(page.locator("#days-left")).not_to_have_text("—")
    expect(page.locator("#a2hs-hint")).to_be_hidden()
    desktop = browser.new_context(**PHONE).new_page()
    login(desktop, base)
    desktop.goto(base + "/training")
    expect(desktop.locator("#days-left")).not_to_have_text("—")
    expect(desktop.locator("#a2hs-hint")).to_be_hidden()  # not an iPhone
    shot(page, "part4-a2hs.png")
