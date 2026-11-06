"""The practice answer page, grading by hand, the chapter picker, field sizes and the keyboard, at iPhone width (WebKit in CI too)."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_motion_ui import FULL, serve  # noqa: F401
from test_pages_ui import BOOK, OVERVIEW, RECENT, REVIEW, REVIEW_ITEM, DETAIL, start, ROUTES  # noqa: F401
from test_ui_screens import engine, open_page  # noqa: F401

SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))

SET = {"set_id": 7, "mode": "type", "counts_toward_daily_paper": False, "time_limit_seconds": None, "questions": [
    {"id": 501, "subject": "biochemistry", "question_type": "single_choice", "stem": "蛋白质二级结构主要靠什么维持？", "max_points": 4, "chapter": "第二章 蛋白质", "options": {"A": "氢键", "B": "二硫键", "C": "疏水作用", "D": "盐键"}},
    {"id": 502, "subject": "biochemistry", "question_type": "fill_blank", "stem": "糖酵解的终产物是____", "max_points": 4, "chapter": "第十二章 糖代谢", "blank_count": 1},
    {"id": 503, "subject": "cell_biology", "question_type": "term", "stem": "请解释：流动镶嵌模型", "max_points": 6, "chapter": "第二章 细胞膜"}]}
ANSWER = {"question_id": 501, "correct": True, "points": 4, "max_points": 4, "answer": "A", "accepted_answers": [], "blank_points": None, "why": ["氢键维持"]}
PENDING = {"question_id": 503, "correct": None, "points": None, "max_points": 6, "pending": True, "queued": True, "answer": "略", "accepted_answers": [], "blank_points": None, "why": ["要点一"]}
DRILL_ROUTES = {**ROUTES, "/v2/practice/7/answer": ANSWER, "/v2/practice/7/review": {**REVIEW, "paper_id": 7, "kind": "practice"}, "/v2/practice": SET}


def begin(page):
    page.goto(page.url.split("#")[0] + "#/practice")
    page.wait_for_selector("#practice-grid .practice-card")
    page.locator("#practice-grid .practice-card").nth(4).click()                # 随机快练
    page.locator(".pick-dialog .pick-option").first.click()                     # 不限时
    page.wait_for_selector("#drill-view .drill-title")


def test_practice_opens_its_own_full_screen_page_and_leaving_returns_to_the_centre(engine, live_app):
    page, errors = start(engine, live_app, DRILL_ROUTES, path="/training#/practice")
    page.wait_for_selector("#practice-grid .practice-card")
    page.locator("#practice-view").evaluate("n => n.closest('.workspace').scrollTop = 120")
    page.locator("#practice-grid .practice-card").nth(4).click()
    page.locator(".pick-dialog .pick-option").first.click()
    page.wait_for_selector("#drill-view .drill-title")
    assert page.evaluate("location.hash") == "#/drill/7"
    expect(page.locator("#drill-view .drill-title b")).to_have_text("专项练习 1 / 3")
    assert not page.locator("nav.primary-nav").is_visible()                    # full screen: no bottom navigation
    assert page.locator("#practice-grid").is_hidden() and page.locator(".drill-actions").is_visible()
    # question 1: choose, submit, feedback, next
    expect(page.locator(".drill-actions .primary")).to_be_disabled()
    page.locator("#drill-view .option").first.click()
    page.locator(".drill-actions .primary").click()
    expect(page.locator("#drill-view .practice-feedback")).to_contain_text("回答正确")
    page.locator(".drill-actions .primary").click()                            # 下一题
    expect(page.locator("#drill-view .drill-title b")).to_have_text("专项练习 2 / 3")
    page.locator(".drill-actions .secondary").click()                          # 上一题: the answer is still there, read only
    expect(page.locator("#drill-view .practice-feedback")).to_contain_text("回答正确")
    assert page.locator("#drill-view .option.chosen").count() == 1 and page.locator("#drill-view .option").first.is_disabled()
    # leaving asks first
    page.locator("#drill-view .icon-button").click()
    expect(page.locator("dialog[open] h2")).to_have_text("退出专项练习？")
    page.locator("dialog[open] .primary").last.click()                         # 继续做题
    assert page.evaluate("location.hash") == "#/drill/7"
    page.locator("#drill-view .icon-button").click()
    page.locator("dialog[open] .secondary").last.click()                       # 退出
    page.wait_for_function("location.hash === '#/practice'")
    page.wait_for_selector("#practice-grid .practice-card")
    assert page.locator("nav.primary-nav").is_visible()
    page.wait_for_function("document.querySelector('.workspace').scrollTop > 60")      # back where it was
    assert not errors, errors
    page.close()


def test_finishing_opens_the_practice_review_and_a_written_answer_waits_for_the_ai(engine, live_app):
    routes = {**DRILL_ROUTES, "/v2/practice/7/answer": [ANSWER, 0]}
    page, _ = start(engine, live_app, routes, path="/training#/practice")
    begin(page)
    page.locator("#drill-view .option").first.click()
    page.locator(".drill-actions .primary").click()
    page.locator(".drill-actions .primary").click()
    page.locator("#drill-view input.drill-answer").fill("丙酮酸")
    page.locator(".drill-actions .primary").click()
    page.locator(".drill-actions .primary").click()
    assert page.locator("#drill-view textarea.drill-answer").count() == 1                      # the third is written: a text area
    page.evaluate("window.__routes['/v2/practice/7/answer'] = [%s, 0]" % __import__("json").dumps(PENDING))
    page.locator("#drill-view textarea.drill-answer").fill("磷脂双分子层中镶嵌蛋白质")
    page.locator(".drill-actions .primary").click()
    expect(page.locator("#drill-view .practice-feedback")).to_contain_text("AI 判分中")
    expect(page.locator(".drill-actions .primary")).to_have_text("查看成绩")
    page.locator(".drill-actions .primary").click()
    page.wait_for_function("location.hash === '#/review/s7'")
    page.wait_for_selector("#review-body .review-head")
    page.close()


def test_the_answer_page_keeps_its_bars_and_the_field_inside_the_visible_part_when_the_keyboard_is_up(engine, live_app):
    page, _ = start(engine, live_app, DRILL_ROUTES, path="/training#/practice")
    begin(page)
    page.locator("#drill-view .option").first.click()
    page.locator(".drill-actions .primary").click()
    page.locator(".drill-actions .primary").click()
    field = page.locator("#drill-view input.drill-answer")
    field.focus()
    page.evaluate("document.documentElement.style.setProperty('--app-h', '430px')")                # the visible part with the keyboard up
    page.wait_for_timeout(400)
    assert "keyboard-open" in page.evaluate("document.body.className")
    boxes = page.evaluate("""() => { const f = document.querySelector('#drill-view input.drill-answer').getBoundingClientRect(),
        a = document.querySelector('.drill-actions').getBoundingClientRect(), t = document.querySelector('.drill-top').getBoundingClientRect();
        const d = document.querySelector('#drill-view').getBoundingClientRect();
        return { fieldTop: f.top, fieldBottom: f.bottom, actionsTop: a.top, actionsBottom: a.bottom, topBottom: t.bottom, drillTop: d.top, drillBottom: d.bottom, vh: 430 }; }""")
    # the page is the visible part (WebKit may offset a fixed page by a pixel or two while the keyboard animates), the bars are inside it, the field is clear of both
    assert abs((boxes["drillBottom"] - boxes["drillTop"]) - boxes["vh"]) <= 3 and boxes["actionsBottom"] <= boxes["drillBottom"] + 1, boxes
    assert boxes["fieldBottom"] <= boxes["actionsTop"] and boxes["fieldTop"] >= boxes["topBottom"], boxes     # nothing covers the field
    assert not page.locator("nav.primary-nav").is_visible()
    page.evaluate("document.activeElement.blur(); document.documentElement.style.setProperty('--app-h', '')")
    page.wait_for_function("!document.body.classList.contains('keyboard-open')")
    page.close()


def test_the_bottom_navigation_hides_while_a_field_has_focus_and_comes_back(engine, live_app):
    page, _ = start(engine, live_app, ROUTES, path="/training#/mistakes")
    page.wait_for_selector("#mistake-groups .mistake-card")
    assert page.locator("nav.primary-nav").is_visible()
    page.locator("#filter-toggle").click()
    page.locator("#filter-chapter").focus()
    page.wait_for_function("document.body.classList.contains('keyboard-open')")
    assert not page.locator("nav.primary-nav").is_visible()
    page.evaluate("document.activeElement.blur()")
    page.wait_for_function("!document.body.classList.contains('keyboard-open')")
    assert page.locator("nav.primary-nav").is_visible()
    page.close()


def test_every_field_is_at_least_16px_so_the_page_never_zooms_on_focus(engine, live_app):
    page, _ = start(engine, live_app, DRILL_ROUTES, path="/training#/practice")
    meta = page.evaluate("document.querySelector('meta[name=viewport]').content")
    assert "maximum-scale" not in meta and "user-scalable" not in meta                          # zooming by hand stays possible

    def small():
        return page.evaluate("""() => [...document.querySelectorAll('input, textarea, select')].map((n) => [n.id || n.className || n.tagName, parseFloat(getComputedStyle(n).fontSize)]).filter((x) => x[1] < 16)""")
    seen = 0
    for route in ("#/home", "#/practice", "#/mistakes", "#/profile", "#/progress", "#/model", "#/stats/history"):
        page.evaluate("(r) => { location.hash = r; }", route)
        page.wait_for_timeout(500)
        seen += page.locator("input, textarea, select").count()
        assert small() == [], (route, small())
    assert seen >= 10                                                                           # (password, model page, filters ... all counted)
    begin(page)                                                                                 # the practice fields: a fill-in box, then a text area
    page.locator("#drill-view .option").first.click()
    page.locator(".drill-actions .primary").click()
    page.locator(".drill-actions .primary").click()
    assert page.locator("#drill-view input.drill-answer").count() == 1 and small() == []
    page.locator("#drill-view input.drill-answer").fill("丙酮酸")
    page.locator(".drill-actions .primary").click()
    page.locator(".drill-actions .primary").click()
    assert page.locator("#drill-view textarea.drill-answer").count() == 1
    assert small() == []
    page.close()


def test_the_manual_grading_sheet_shows_the_question_ticks_every_point_and_saves(engine, live_app):
    form = {"paper_id": 9, "question_id": 101, "question_type": "short_answer", "stem": "简述酶的竞争性抑制。", "options": {}, "your_answer": "抑制剂和底物竞争活性中心", "reference": "抑制剂与底物结构相似，竞争活性位点",
            "criteria": [{"description": "结构相似", "points": 2}, {"description": "竞争活性位点", "points": 3}], "criterion_points": None, "points": None, "max_points": 5,
            "graded_by": None, "label": None, "note": None, "ai_original": None, "released": False}
    routes = {**ROUTES, "/v2/papers/9/manual/101": form, "/v2/papers/9/review": REVIEW, "/v2/papers/9/review/101": {**REVIEW_ITEM, "points": None, "status": "pending", "pending_text": "排队等待 AI 判分"}}
    page, _ = start(engine, live_app, routes, path="/training#/review/p9")
    page.wait_for_selector("#review-body .review-q")
    page.locator("#rq-101 .rq-head").click()
    page.locator("#rq-101 .rq-body button", has_text="手动判分").click()
    dialog = page.locator(".manual-dialog")
    expect(dialog).to_be_visible()
    text = dialog.inner_text()
    assert "简述酶的竞争性抑制" in text and "抑制剂和底物竞争活性中心" in text and "参考答案" in text and "结构相似" in text and "竞争活性位点" in text
    expect(dialog.locator(".manual-total")).to_have_text("合计 0 / 5 分")
    dialog.locator(".manual-criterion").nth(0).get_by_role("button", name="得分", exact=True).click()
    dialog.locator(".manual-criterion").nth(1).get_by_role("button", name="加 1 分").click()
    dialog.locator(".manual-criterion").nth(1).get_by_role("button", name="加 1 分").click()
    expect(dialog.locator(".manual-total")).to_have_text("合计 4 / 5 分")
    dialog.locator(".manual-criterion").nth(1).get_by_role("button", name="加 1 分").click()                  # up to the point's value ...
    expect(dialog.locator(".manual-total")).to_have_text("合计 5 / 5 分")
    assert dialog.locator(".manual-criterion").nth(1).get_by_role("button", name="加 1 分").is_disabled()          # ... and no further
    assert float(dialog.locator(".manual-note").evaluate("n => getComputedStyle(n).fontSize.replace('px', '')")) >= 16
    box = dialog.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= page.viewport_size["height"] + 1               # inside the screen
    dialog.locator(".manual-note").fill("基本答全")
    dialog.get_by_role("button", name="保存").click()
    page.wait_for_function("window.__calls && window.__calls['/v2/papers/9/manual/101'] >= 2")
    page.close()


def test_the_chapter_picker_has_whole_items_a_whole_selected_frame_and_groups_by_subject(engine, live_app):
    over = {**OVERVIEW, "chapter": {**OVERVIEW["chapter"], "chapters": [
        {"subject": "biochemistry", "chapter": "第二章 蛋白质的结构与功能", "available": 5}, {"subject": "biochemistry", "chapter": "第十二章 糖代谢", "available": 31},
        {"subject": "cell_biology", "chapter": "第二章 细胞膜", "available": 5}, {"subject": "cell_biology", "chapter": "第十一章 细胞信号转导", "available": 9}]}}
    page, _ = start(engine, live_app, {**ROUTES, "/v2/practice/overview": over}, path="/training#/practice")
    page.wait_for_selector("#practice-grid .practice-card")
    page.locator("#practice-grid .practice-card").nth(1).click()
    dialog = page.locator(".pick-dialog")
    expect(dialog).to_be_visible()
    groups = dialog.locator(".pick-group").all_inner_texts()
    assert groups == ["生化", "细胞"]
    labels = dialog.locator(".pick-option .pick-label").all_inner_texts()
    assert labels == ["第二章 蛋白质的结构与功能", "第十二章 糖代谢", "第二章 细胞膜", "第十一章 细胞信号转导"]
    sizes = page.evaluate("""() => [...document.querySelectorAll('.pick-option')].map((b) => { const r = b.getBoundingClientRect();
        const kids = [...b.children].map((c) => c.getBoundingClientRect()); return { h: r.height, fits: kids.every((k) => k.top >= r.top - 0.5 && k.bottom <= r.bottom + 0.5), scroll: b.scrollHeight - b.clientHeight }; })""")
    assert all(s["fits"] and s["scroll"] <= 1 and s["h"] >= 54 for s in sizes), sizes              # both lines inside the card, never on its border
    page.evaluate("document.querySelector('.pick-option').setAttribute('aria-pressed', 'true')")
    frame = page.evaluate("""() => { const l = document.querySelector('.pick-list').getBoundingClientRect(), b = document.querySelector('.pick-option').getBoundingClientRect(); return { top: b.top - l.top, left: b.left - l.left, right: l.right - b.right }; }""")
    assert frame["top"] >= 2 and frame["left"] >= 2 and frame["right"] >= 2, frame                  # the selected frame is not cut at the list's edge
    page.close()


AUDIT = """() => {
  const bad = [];
  const visible = (n) => { const r = n.getBoundingClientRect(); const s = getComputedStyle(n); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const boxes = document.querySelectorAll('button, .card, .pick-option, .mistake-card, .practice-card, .history-card, .option, .recent-row, .manual-criterion, .manual-box, dialog[open]');
  for (const box of boxes) {
    if (!visible(box)) continue;
    const r = box.getBoundingClientRect();
    if (r.right > innerWidth + 1 || r.left < -1) { bad.push(['off-screen', box.className || box.tagName, Math.round(r.left), Math.round(r.right)]); continue; }
    const cs = getComputedStyle(box);
    if (cs.overflow !== 'visible' && cs.overflowY !== 'visible') continue;        // a scroller clips on purpose
    for (const child of box.querySelectorAll('*')) {
      if (!child.childNodes.length || !visible(child)) continue;
      if (![...child.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim())) continue;
      const closed = child.closest('details:not([open])');
      if (closed && !child.closest('summary')) continue;                        // folded away: not on screen
      const c = child.getBoundingClientRect();
      const scroller = child.closest('.pick-list, .manual-scroll, .drill-body, .workspace, dialog');
      if (scroller && scroller !== box && scroller.contains(box) === false && getComputedStyle(scroller).overflowY !== 'visible') {
        const sr = scroller.getBoundingClientRect(); if (c.bottom > sr.bottom + 1 || c.top < sr.top - 1) continue;        // scrolled out of view: not a clash
      }
      if (c.bottom > r.bottom + 1.5 || c.top < r.top - 1.5 || c.right > r.right + 1.5 || c.left < r.left - 1.5) bad.push(['text outside its box', (box.className || box.tagName) + ' > ' + (child.className || child.tagName), child.textContent.trim().slice(0, 20)]);
    }
  }
  return bad;
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_text_crosses_the_edge_of_its_card_button_or_sheet(engine, live_app, scheme):
    routes = {**DRILL_ROUTES, "/v2/stats/history": {"papers": []}, "/v2/history": {"training": [], "legacy": []}, "/v2/stats/profile": {"trend": [], "timing": {"papers": []}},
              "/v2/papers/9/manual/101": {"paper_id": 9, "question_id": 101, "question_type": "term", "stem": "名词解释", "options": {}, "your_answer": "略", "reference": "略", "criteria": [{"description": "要点", "points": 2}], "criterion_points": [1], "points": 1,
                                         "max_points": 2, "graded_by": "手动判分", "label": "手动判分", "note": None, "ai_original": {"points": 2, "graded_by": "AI自动核验", "comment": "满分"}, "released": False}}
    page, _ = start(engine, live_app, routes, path="/training#/home", scheme=scheme)
    offenders = []
    for route in ("#/home", "#/practice", "#/mistakes", "#/profile", "#/stats/history", "#/review/p9", "#/mistake/m1"):
        page.evaluate("(r) => { location.hash = r; }", route)
        page.wait_for_timeout(700)
        offenders += [(route, *x) for x in page.evaluate(AUDIT)]
    # the sheets: the chapter picker, the manual grading sheet, the quit confirmation
    page.evaluate("location.hash = '#/practice'")
    page.wait_for_selector("#practice-grid .practice-card")
    page.locator("#practice-grid .practice-card").nth(1).click()
    page.wait_for_timeout(500)
    offenders += [("picker", *x) for x in page.evaluate(AUDIT)]
    page.locator(".pick-dialog button", has_text="取消").click()
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/review/p9'")
    page.wait_for_selector("#review-body .review-q")
    page.locator("#rq-101 .rq-head").click()
    page.locator("#rq-101 .rq-body button", has_text="修改分数").click() if page.locator("#rq-101 .rq-body button", has_text="修改分数").count() else None
    page.wait_for_timeout(500)
    offenders += [("review sheet", *x) for x in page.evaluate(AUDIT)]
    page.screenshot(path=str(SHOTS / f"D-sheets-{scheme}.png"))
    assert offenders == [], offenders[:8]
    page.close()
