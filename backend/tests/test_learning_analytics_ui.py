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
    expect(page.locator("#metric-submitted")).to_have_text("0")
    expect(page.locator("#score-trend")).to_contain_text("完成考试后")
    assert page.locator("#score-trend svg").count() == 0
    page.locator("#prepare").click()
    expect(page.locator("#start")).to_be_visible()
    assert page.locator("#questions article").count() == 0
    assert "尚未开始" in page.locator("#paper-state").inner_text()
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
    expect(page.locator(".grade-title").first).to_have_text("1题待批改")
    expect(page.locator(".grade-ring")).to_have_attribute("aria-label", "已判分27/28题，待批1题")
    result = client.get(f"/v2/papers/{pid}/result").json()
    assert result["score"] is None and result["pending"] == 1
    repeated = client.post(f"/v2/papers/{pid}/submit", json={"expected_version": 0}).json()
    assert repeated["attempt_id"] == result["attempt_id"]
    assert page.locator("#result article details").count() == 28
    first = page.locator("#result article details").first
    first.locator("summary").click()
    page.evaluate("showResult()")
    expect(first).to_have_attribute("open", "")
    page.locator('[data-view="stats"]').first.click()
    page.evaluate("refresh()")
    expect(page.locator("#stats-view")).to_be_visible()
    page.close()


def test_dashboard_data_denominators_and_no_fake_trend(live_app, browser):
    client, _, _ = live_app
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    # Explicit synthetic transport fixtures test display arithmetic; never persisted or shipped as product defaults.
    history = {"legacy": [{"score": 60}], "training": [{"paper_date": "2026-10-27", "score": 120}, {"paper_date": "2026-10-26", "score": None}, {"paper_date": "2026-10-25", "score": 90}]}
    page.route("**/v2/history", lambda route: route.fulfill(json=history))
    page.route("**/v2/stats/weekly", lambda route: route.fulfill(json={"chapters": [{"subject": "biochemistry", "chapter": "合成统计章节", "graded_points": 3, "graded_max_points": 6, "pending": 2, "answers": 4}]}))
    page.goto(str(client.base_url).rstrip("/"))
    page.locator("#password").fill("test-live-login"); page.locator("#login button").click()
    expect(page.locator("#metric-submitted")).to_have_text("3")
    expect(page.locator("#metric-graded")).to_have_text("2")
    expect(page.locator("#subject-performance")).to_contain_text("3 / 6分 · 50%")
    expect(page.locator("#subject-performance")).to_contain_text("2题待批")
    expect(page.locator("#score-trend svg")).to_have_count(1)
    expect(page.locator("#score-trend")).to_contain_text("90/150分")
    assert "60/150" not in page.locator("#score-trend").inner_text()
    page.close()


def test_failed_statistics_remain_unknown_and_reduced_motion(live_app, browser):
    client, _, _ = live_app
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    page.route("**/v2/history", lambda route: route.fulfill(status=503, json={"detail": "Synthetic unavailable response"}))
    page.goto(str(client.base_url).rstrip("/"))
    page.locator("#password").fill("test-live-login"); page.locator("#login button").click()
    expect(page.locator("#overview-note")).to_contain_text("未将未知数据当作0")
    expect(page.locator("#metric-submitted")).to_have_text("—")
    assert page.evaluate("getComputedStyle(document.querySelector('#prepare')).transitionDuration") == "0s"
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
    page.reload(); page.locator("#prepare").click()
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
    expect(page.locator("#start")).to_be_visible()
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
    page.reload(); page.locator("#prepare").click()
    expect(page.locator("#questions article:visible")).to_have_count(1)
    assert page.evaluate("Object.keys(localDrafts).length") == 0
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["drafts"] == {}
    page.close()
