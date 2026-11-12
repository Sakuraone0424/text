"""Isolated PostgreSQL + actual HTTP/browser UI regressions. No production or model calls."""
# ruff: noqa: F811
import json
import re
import pytest
from playwright.sync_api import expect
from test_live_training import live_app, browser, login, open_question, confirm_submission  # noqa: F401


def wait_saved(page):
    page.wait_for_function("Object.keys(JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('quiz:v2:')))).pending).length===0")


@pytest.mark.parametrize("width,height", [(390, 844), (430, 932), (834, 1194), (1440, 900)])
def test_dashboard_focus_navigation_and_two_step_start(live_app, browser, width, height):
    client, _, blueprint = live_app
    page = browser.new_page(viewport={"width": width, "height": height}, has_touch=True)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    base = str(client.base_url).rstrip("/")
    page.goto(base)
    page.locator("#password").fill("test-live-login")
    page.locator("#login button").click()
    expect(page.locator("#stat-avg-objective")).to_have_text("—")  # no paper yet: an empty state, never a made-up number
    expect(page.locator("#stat-avg-objective-note")).to_contain_text("做完首套后显示")
    expect(page.locator("#weak-chapters")).to_contain_text("做完首套后")
    page.wait_for_function("document.getElementById('prepare-objective').dataset.kind && document.getElementById('prepare-objective').dataset.kind !== 'loading'")
    page.evaluate("window.TodayPaper.prepare('objective')")                         # the isolated fixture prepares its old paper
    page.wait_for_function("window.TrainingUI.today().hasPaper")
    page.evaluate("document.getElementById('start-dialog').showModal()")             # the start sheet comes next
    expect(page.locator("#start")).to_be_visible()
    assert page.locator("#questions article").count() == 0
    page.locator("#start").click()
    expect(page.locator("#questions article:visible")).to_have_count(1)
    assert page.locator("#questions article").count() == 28
    page.locator("#questions .option:visible").nth(1).click()
    wait_saved(page)
    expect(page.locator("#questions .option:visible").nth(1)).to_have_attribute("aria-checked", "true")
    page.locator("#questions .option:visible").nth(1).focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#questions .option:visible").nth(2)).to_have_attribute("aria-checked", "true")
    page.keyboard.press("ArrowLeft")
    wait_saved(page)
    page.locator("#toggle-mark").click()
    wait_saved(page)
    page.locator("#question-map").click()
    expect(page.locator("#question-grid button")).to_have_count(28)
    current = page.locator('#question-grid [aria-current="step"]')
    expect(current).to_have_class("answered marked")
    expect(current).to_have_attribute("aria-label", "第1题，已作答，已标记，当前题")
    page.keyboard.press("Escape")
    page.locator("#next-question").click()
    expect(page.locator("#current-number")).to_have_text("2")
    page.locator("#previous-question").click()
    expect(page.locator("#questions .option:visible").nth(1)).to_have_attribute("aria-checked", "true")
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
    page.locator("#leave-focus").click()
    expect(page.locator("#home-view")).to_be_visible()
    page.evaluate("refresh()")
    expect(page.locator("#home-view")).to_be_visible()  # Background refresh must not navigate the user.
    assert not errors
    page.close()


def test_chinese_composition_focus_and_multi_device_conflict(live_app, browser):
    client, _, blueprint = live_app
    page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    login(page, str(client.base_url).rstrip("/")); page.locator("#start").click()
    expect(page.locator("#questions article:visible")).to_have_count(1)
    pid = client.post("/v2/papers", json={"specification": "xiaohan-150-60-v1", "quotas": blueprint.quotas}).json()["paper_id"]
    paper = client.get(f"/v2/papers/{pid}").json()
    term = next(q for q in paper["questions"] if q["question_type"] == "term")
    open_question(page, term["id"])
    field = page.locator(f'#questions article[data-id="{term["id"]}"] textarea')
    requests = []
    page.on("request", lambda request: requests.append(request.url) if request.method == "PUT" and "/drafts/" in request.url else None)
    # A VisualViewport resize models keyboard occlusion; it is not a real iPhone keyboard.
    page.evaluate("Object.defineProperty(visualViewport, 'height', {get: () => innerHeight-320, configurable:true}); visualViewport.dispatchEvent(new Event('resize'))")
    expect(page.locator("body")).to_have_class(re.compile("keyboard-open"))
    assert page.locator("#focus-actions").evaluate("node=>getComputedStyle(node).position") == "static"
    field.scroll_into_view_if_needed()
    field.focus(); field.dispatch_event("compositionstart", {"data": ""}); field.fill("酶催化的必要条件")
    page.evaluate("refresh()")
    assert not requests
    assert field.input_value() == "酶催化的必要条件"
    assert page.evaluate("document.activeElement.tagName") == "TEXTAREA"
    field.dispatch_event("compositionend", {"data": "条件"}); wait_saved(page)
    assert len(requests) == 1
    field.evaluate("node=>node.setSelectionRange(2,2)")
    page.evaluate("refresh()")
    assert field.evaluate("node=>node.selectionStart") == 2
    current = client.get(f"/v2/papers/{pid}").json()
    remote = client.put(f'/v2/papers/{pid}/drafts/{term["id"]}', json={"expected_version": current["timing"]["version"], "value": {"text": "另一设备的新答案"}})
    assert remote.status_code == 200
    field.fill("本机继续输入，不覆盖另一设备")
    expect(page.locator("#conflict")).to_be_visible()
    stored = page.evaluate("JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('quiz:v2:'))))")
    assert stored["localDrafts"][str(term["id"])]["text"] == "本机继续输入，不覆盖另一设备"
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["drafts"][str(term["id"])]["text"] == "另一设备的新答案"
    page.locator("#remote").click()
    expect(field).to_have_value("另一设备的新答案")
    page.close()


def test_pending_grade_custom_confirmation_and_truthful_chart(live_app, browser):
    client, _, blueprint = live_app
    page = browser.new_page(viewport={"width": 430, "height": 932}, is_mobile=True, has_touch=True)
    login(page, str(client.base_url).rstrip("/")); page.locator("#start").click()
    expect(page.locator("#questions article:visible")).to_have_count(1)
    pid = client.post("/v2/papers", json={"specification": "xiaohan-150-60-v1", "quotas": blueprint.quotas}).json()["paper_id"]
    paper = client.get(f"/v2/papers/{pid}").json()
    term = next(q for q in paper["questions"] if q["question_type"] == "term")
    open_question(page, term["id"])
    page.locator("#questions textarea:visible").fill("合成测试作答，不是真实科学审核或用户成绩")
    wait_saved(page)
    page.locator("#submit").click()
    expect(page.locator("#submit-dialog")).to_be_visible()
    page.locator("#cancel-submit").click()
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["status"] == "in_progress"
    confirm_submission(page)
    expect(page.locator(".grade-title").first).to_have_text("1题待判分")
    expect(page.locator(".grade-card")).to_have_attribute("aria-label", "已判分27/28题，待判分1题")
    result = client.get(f"/v2/papers/{pid}/result").json()
    assert result["score"] is None and result["pending"] == 1
    repeated = client.post(f"/v2/papers/{pid}/submit", json={"expected_version": 0}).json()
    assert repeated["attempt_id"] == result["attempt_id"]
    assert page.locator("#result article summary.result-question-summary").count() == 28
    first = page.locator("#result article details:has(> summary.result-question-summary)").first
    first.locator("> summary").click()
    page.evaluate("showResult()")
    expect(first).to_have_attribute("open", "")
    page.evaluate("TrainingUI.showView('stats')")
    page.evaluate("refresh()")
    expect(page.locator("#stats-view")).to_be_visible()
    page.close()


def overview_fixture(**extra):
    base = {
        "today": "2026-11-02", "exam_date": "2026-12-19", "days_left": 47, "streak": {"current": 4, "longest": 9},
        "today_paper": {"state": "none"},
        "last7": {"papers": 5, "avg_rate": 0.7067, "avg_points": 212.0, "max_points": 300, "delta_points": 6.0},
        "today_papers": {}, "pending_by_kind": {},
        "last7_by_kind": {"objective": {"papers": 5, "avg_rate": 0.7067, "avg_points": 212.0, "max_points": 300, "delta_points": 6.0},
                          "subjective": {"papers": 0, "avg_rate": None, "avg_points": None, "max_points": None, "delta_points": None},
                          "mixed": {"papers": 0, "avg_rate": None, "avg_points": None, "max_points": None, "delta_points": None}},
        "objective_rate": 0.72, "subjective_rate": 0.55,
        "weak_chapters": [{"subject": "biochemistry", "chapter": "合成统计章节", "rate": 0.4, "answers": 5},
                          {"subject": "cell_biology", "chapter": "合成膜章节", "rate": 0.6, "answers": 4}],
        "reviews": {"due_now": 3, "next_7_days": [], "mastered": 0}, "tomorrow": {"ready": 42, "needed": 60, "missing": 18, "complete": False, "eta_at": None, "eta_state": "estimating", "eta_hours": None, "per_hour": 0}, "has_data": True,
    }
    return {**base, **extra}


def test_dashboard_numbers_come_straight_from_the_statistics_endpoint(live_app, browser):
    client, _, _ = live_app
    page = browser.new_page(viewport={"width": 390, "height": 844})
    # Explicit synthetic transport fixture tests display arithmetic; never persisted or shipped as a product default.
    page.route("**/v2/stats/overview", lambda route: route.fulfill(json=overview_fixture()))
    page.goto(str(client.base_url).rstrip("/"))
    page.locator("#password").fill("test-live-login"); page.locator("#login button").click()
    expect(page.locator("#days-left")).to_have_text("47")
    expect(page.locator("#streak-days")).to_have_text("4")
    expect(page.locator("#stat-avg-objective")).to_have_text("212")
    expect(page.locator("#stat-avg-objective-note")).to_have_text("较上周 +6")
    expect(page.locator("#stat-avg-objective-note")).to_have_class("up")
    expect(page.locator("#stat-objective")).to_have_text("72%")
    expect(page.locator("#stat-subjective")).to_have_text("55%")
    rows = page.locator("#weak-chapters .progress-line")
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text("合成统计章节")
    expect(rows.first).to_contain_text("40%")
    assert rows.first.locator(".hbar-track i").evaluate("(n) => getComputedStyle(n).backgroundColor") == "rgb(234, 88, 12)"  # below 50%: orange
    assert rows.nth(1).locator(".hbar-track i").evaluate("(n) => getComputedStyle(n).backgroundColor") == "rgb(37, 99, 235)"
    expect(page.locator("#metric-due")).to_have_text("3")
    expect(page.locator("#tomorrow-ready")).to_have_text("42/60")
    expect(page.locator("#weak-practice")).to_be_visible()
    page.close()


def test_the_score_trend_never_mixes_papers_of_different_totals(live_app, browser):
    client, _, _ = live_app
    page = browser.new_page(viewport={"width": 390, "height": 844})
    subjects = lambda a, b, m: {"biochemistry": {"points": a, "max": m, "rate": a / m}, "cell_biology": {"points": b, "max": m, "rate": b / m}}
    trend = [
        {"paper_id": 1, "date": "2026-10-25", "spec": "xiaohan-60q-150pt-v2", "points": 90, "max": 150, "rate": 0.6, "subjects": subjects(45, 45, 75)},
        {"paper_id": 2, "date": "2026-10-30", "spec": "xiaohan-60q-300pt-v4", "points": 180, "max": 300, "rate": 0.6, "subjects": subjects(90, 90, 150)},
        {"paper_id": 3, "date": "2026-11-01", "spec": "xiaohan-60q-300pt-v4", "points": 240, "max": 300, "rate": 0.8, "subjects": subjects(120, 120, 150)},
    ]
    profile = {"today": "2026-11-02", "summary": {"papers": 3, "answered": 150, "total_seconds": 9000, "streak_current": 2, "streak_longest": 2},
               "trend": trend, "type_rates": [], "chapters": [], "calendar": [], "timing": {"papers": [], "by_type": [], "recorded_questions": 0},
               "reviews": {"due_now": 0, "next_7_days": [], "mastered": 0}}
    page.route("**/v2/stats/profile", lambda route: route.fulfill(json=profile))
    page.goto(str(client.base_url).rstrip("/"))
    page.locator("#password").fill("test-live-login"); page.locator("#login button").click()
    page.locator('nav.primary-nav [data-view="profile"]').click()
    chart = page.locator("#profile-data .chart-block").first
    expect(chart.locator(".chart-dot")).to_have_count(9)  # rates: three papers x (two subjects + total), each against its own maximum
    page.locator("#profile-data .segmented button", has_text="分数").click()
    expect(page.locator("#profile-data .chart-block").first.locator(".chart-dot")).to_have_count(6)  # points: only the latest specification
    expect(page.locator("#profile-data .chart-conclusion").first).to_contain_text("分数模式只显示与最近一套同规格")
    labels = page.locator("#profile-data .chart-block").first.locator(".chart-dot").evaluate_all("(n) => n.map((x) => x.getAttribute('aria-label')).join('|')")
    assert "10/25" not in labels and "10/30" in labels  # the older 150-point paper is not on the points chart
    page.close()


def test_failed_statistics_remain_unknown_and_reduced_motion(live_app, browser):
    client, _, _ = live_app
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    page.route("**/v2/stats/overview", lambda route: route.fulfill(status=503, json={"detail": "Synthetic unavailable response"}))
    page.goto(str(client.base_url).rstrip("/"))
    page.locator("#password").fill("test-live-login"); page.locator("#login button").click()
    expect(page.locator("#stat-avg-objective-note")).to_contain_text("未将未知数据当作0")
    expect(page.locator("#stat-avg-objective")).to_have_text("—")
    expect(page.locator("#days-left")).to_have_text("—")
    assert page.evaluate("getComputedStyle(document.querySelector('#prepare-objective')).transitionDuration") == "0s"
    page.close()


def test_prestart_cache_and_injected_questions_cannot_be_used(live_app, browser):
    """Even stale/custom responses and local queued photos cannot become timed answers."""
    client, _, blueprint = live_app
    page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    login(page, str(client.base_url).rstrip("/"))
    expect(page.locator("#start")).to_be_visible()
    pid = page.evaluate("paper.paper_id")
    # Simulate pre-fix local data for this NOT STARTED paper, including a photo job.
    page.evaluate("""async () => {
      const fake = {id: 999, position: 1, subject: 'biochemistry', question_type: 'term', stem: '不得预览的缓存题干', max_points: 4};
      paper.questions = [fake];
      const stored = {paper, pending: {999: {text: '计时前预答'}}, localDrafts: {999: {text: '计时前预答'}}, version: 0};
      localStorage.setItem(key(), JSON.stringify(stored));
      await photoStore('put', {key: key()+':999:prestart', user: username, paper: paper.paper_id, id: 999, file: new Blob(['test'])});
      await restore();
      // Explicitly exercise the presentation layer with a corrupt unstarted response.
      window.TrainingUI.state({paper: {...paper, questions: [fake]}, drafts: {999: {text: '预答'}}, pendingCount: 1});
      change(999, {text: '不应暂存'});
    }""")
    assert page.locator("#questions article").count() == 0
    expect(page.locator("#questions")).to_be_hidden()
    expect(page.locator("#focus-toolbar")).to_be_hidden()
    expect(page.locator("#focus-actions")).to_be_hidden()
    assert page.locator("#questions input, #questions textarea, #questions input[type=file]").count() == 0
    assert page.evaluate("Object.keys(pending).length + Object.keys(localDrafts).length") == 0
    stored = page.evaluate("JSON.parse(localStorage.getItem(key()))")
    assert stored["paper"]["questions"] == [] and stored["pending"] == {} and stored["localDrafts"] == {}
    assert page.evaluate("photoStore('getAll')") == []
    # A rejected start cannot optimistically unlock the paper.
    page.route("**/start", lambda route: route.fulfill(status=503, json={"detail": "start unavailable"}))
    page.locator("#start").click()
    expect(page.locator("#status")).to_contain_text("start unavailable")
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["status"] == "not_started"
    expect(page.locator("#questions")).to_be_hidden()
    page.unroute("**/start")
    page.reload(); page.evaluate("location.hash = '#/home'")
    page.wait_for_function("document.getElementById('prepare-objective').dataset.kind && document.getElementById('prepare-objective').dataset.kind !== 'loading'")
    page.evaluate("window.TodayPaper.prepare('objective').then(() => document.getElementById('start-dialog').showModal())")
    expect(page.locator("#start")).to_be_visible()
    assert page.locator("#questions article").count() == 0
    page.locator("#start").click()
    expect(page.locator("#questions article:visible")).to_have_count(1)
    assert page.evaluate("Object.keys(localDrafts).length") == 0
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["drafts"] == {}
    page.close()


def test_offline_unstarted_cache_is_cleared_without_unlocking(live_app, browser):
    client, _, _ = live_app
    context = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
    page = context.new_page()
    login(page, str(client.base_url).rstrip("/"))
    expect(page.locator("#start")).to_be_visible()
    # Force offline identity failure while serving the same static shell as the SW.
    page.evaluate("""() => {
      const stored = JSON.parse(localStorage.getItem(key()));
      stored.paper.questions = [{id: 999, position: 1, stem: '离线预览不应出现', question_type: 'term'}];
      stored.localDrafts = {999: {text: '预答'}}; stored.pending = stored.localDrafts;
      localStorage.setItem(key(), JSON.stringify(stored));
    }""")
    page.route("**/auth/me", lambda route: route.abort("internetdisconnected"))
    page.evaluate("Object.defineProperty(navigator, 'onLine', {get: () => false})")
    # A full reload injects the offline flag before the shell's scripts initialize.
    page.add_init_script("Object.defineProperty(navigator, 'onLine', {get: () => false})")
    page.reload()
    expect(page.locator("#exam-intro")).to_be_visible()
    expect(page.locator("#questions")).to_be_hidden()
    assert page.locator("#questions article").count() == 0
    assert page.evaluate("Object.keys(localDrafts).length") == 0
    assert page.evaluate("JSON.parse(localStorage.getItem(key())).paper.questions") == []
    context.close()


def test_remote_start_does_not_replay_unstarted_local_answers(live_app, browser):
    client, _, _ = live_app
    page = browser.new_page(viewport={"width": 390, "height": 844})
    login(page, str(client.base_url).rstrip("/"))
    expect(page.locator("#start")).to_be_visible()
    pid = page.evaluate("paper.paper_id")
    started = client.post(f"/v2/papers/{pid}/start").json()
    qid = started["questions"][0]["id"]
    page.evaluate("""qid => {
      const stored = JSON.parse(localStorage.getItem(key()));
      stored.pending = {[qid]: {text:'B'}}; stored.localDrafts = stored.pending;
      localStorage.setItem(key(), JSON.stringify(stored));
    }""", qid)
    page.reload(); page.evaluate("location.hash = '#/home'")
    page.wait_for_function("document.getElementById('prepare-objective').dataset.kind && document.getElementById('prepare-objective').dataset.kind !== 'loading'")
    page.evaluate("window.TodayPaper.prepare('objective').then(() => TrainingUI.showView('exam'))")
    expect(page.locator("#questions article:visible")).to_have_count(1)
    assert page.evaluate("Object.keys(localDrafts).length") == 0
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["drafts"] == {}
    page.close()
