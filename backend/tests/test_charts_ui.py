"""My-page charts at 390px (iPhone 15 profile; WebKit in CI, Chromium here), light and dark: the practice calendar and the
per-type score bars. Nothing leaves its card, every bar says whose it is, 0% and "no data" differ, and the conclusion is
drawn only from enough answers. Screenshots go to SHOT_DIR."""
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_ui_screens import engine, open_page  # noqa: F401

SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))
TODAY = date(2026, 11, 5)


def profile(type_rates, answered_by_day=None):
    start = TODAY - timedelta(days=55)
    answered_by_day = answered_by_day or {}
    calendar = [dict(day=(start + timedelta(days=k)).isoformat(), answered=answered_by_day.get(k, 0), rate=0.5 if answered_by_day.get(k) else None) for k in range(56)]
    subjects = lambda a, b, m: {"biochemistry": {"points": a, "max": m, "rate": a / m}, "cell_biology": {"points": b, "max": m, "rate": b / m}}
    trend = [{"paper_id": i + 1, "date": (TODAY - timedelta(days=9 - 3 * i)).isoformat(), "spec": "xiaohan-60q-300pt-v4", "points": 150 + 20 * i, "max": 300,
              "rate": (150 + 20 * i) / 300, "subjects": subjects(75 + 10 * i, 75 + 10 * i, 150)} for i in range(3)]
    return {"today": TODAY.isoformat(), "summary": {"papers": 3, "answered": 150, "total_seconds": 9000, "streak_current": 3, "streak_longest": 5},
            "trend": trend, "type_rates": type_rates, "chapters": [], "calendar": calendar,
            "timing": {"papers": [], "by_type": [], "recorded_questions": 0}, "reviews": {"due_now": 0, "next_7_days": [], "mastered": 0}}


RATES = [
    {"subject": "biochemistry", "question_type": "essay", "rate": 0.0, "max": 36, "answers": 2},
    {"subject": "biochemistry", "question_type": "term", "rate": 0.5, "max": 48, "answers": 8},
    {"subject": "cell_biology", "question_type": "term", "rate": 0.33, "max": 12, "answers": 1},
    {"subject": "biochemistry", "question_type": "single_choice", "rate": 0.9, "max": 40, "answers": 10},
    {"subject": "cell_biology", "question_type": "single_choice", "rate": 0.8, "max": 40, "answers": 10},
]
DAYS = {0: 20, 1: 12, 5: 56, 9: 3, 20: 60, 30: 40, 41: 15, 54: 33, 55: 56}      # includes the first and the last day


def show(engine, live_app, scheme, data):
    client, _, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, scheme=scheme)
    # WebKit's route() cannot intercept requests the page's service worker handles: answer in the page itself
    page.context.add_init_script("""(() => { const data = %s, real = window.fetch.bind(window);
        window.fetch = (input, init) => String(input && input.url || input).includes('/v2/stats/profile')
            ? Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'content-type': 'application/json' } }))
            : real(input, init); })();""" % json.dumps(data))
    page.goto(base + "/training")
    page.wait_for_selector('nav.primary-nav [data-view="profile"]')
    page.locator('nav.primary-nav [data-view="profile"]').click()
    expect(page.locator("#profile-data .heat-cell")).to_have_count(56)
    return page, errors


def inside(page, selector, card_selector="section.card, .card"):
    """Every matched element lies within its card's horizontal bounds."""
    return page.evaluate("""([sel, cardSel]) => [...document.querySelectorAll(sel)].filter((n) => {
        const c = n.closest(cardSel).getBoundingClientRect(), r = n.getBoundingClientRect();
        return r.right > c.right + 0.5 || r.left < c.left - 0.5; }).length""", [selector, card_selector])


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_calendar_fits_its_card_is_labelled_by_week_and_month_and_shows_a_tip(engine, live_app, scheme):
    page, errors = show(engine, live_app, scheme, profile(RATES, DAYS))
    heat = page.locator("#profile-data .heat-grid")
    assert inside(page, ".heat-cell, .heat-month, .heat-day, .heat-grid") == 0           # nothing is cut off at 390px
    cols = page.evaluate("[...new Set([...document.querySelectorAll('.heat-cell')].map((c) => Math.round(c.getBoundingClientRect().left)))].length")
    assert cols == 9                                                                      # 8 weeks plus the partial week: nine columns
    sizes = page.evaluate("[...document.querySelectorAll('.heat-cell')].map((c) => Math.round(c.getBoundingClientRect().width))")
    heights = page.evaluate("[...document.querySelectorAll('.heat-cell')].map((c) => Math.round(c.getBoundingClientRect().height))")
    assert sizes == heights                                                               # squares
    assert min(sizes) == max(sizes) and min(sizes) >= 18                                  # square cells sized by the card, not tiny
    months = page.locator("#profile-data .heat-month").all_inner_texts()
    assert months and all(m.endswith("月") for m in months) and "11月" in months
    expect(page.locator("#profile-data .heat-day")).to_have_text(["一", "二", "三", "四", "五", "六", "日"])
    # weekday labels are centred on their rows
    off = page.evaluate("""() => Math.max(...[...document.querySelectorAll('.heat-day')].map((l) => {
        const cell = [...document.querySelectorAll('.heat-cell')].find((c) => c.style.gridRow === l.style.gridRow);
        const a = l.getBoundingClientRect(), b = cell.getBoundingClientRect();
        return Math.abs((a.top + a.height / 2) - (b.top + b.height / 2)); }))""")
    assert off < 1.5                                                                     # label and row share a centre line
    # the five levels and the empty day are told apart, and the empty day is not the card colour
    colours = page.evaluate("[...new Set([...document.querySelectorAll('.heat-cell')].map((c) => getComputedStyle(c).backgroundColor))]")
    card = page.evaluate("getComputedStyle(document.querySelector('#profile-data section.card, #profile-data .card')).backgroundColor")
    assert len(colours) == 5 and card not in colours
    # tapping a day shows 「11月4日 · N 道」, tapping elsewhere closes it
    today_cell = page.locator("#profile-data .heat-cell.today")
    expect(today_cell).to_have_count(1)
    page.locator("#profile-data .heat-cell").nth(55 - 1).click()
    expect(page.locator("#profile-data .heat-tip")).to_have_text("11月4日 · 33 道")
    page.locator("#profile-data .heat-cell").nth(0).click()
    expect(page.locator("#profile-data .heat-tip")).to_have_text("9月11日 · 20 道")
    assert inside(page, ".heat-tip") == 0
    page.locator("#profile-data h2").first.click()
    expect(page.locator("#profile-data .heat-tip")).to_have_count(0)
    # the summary: the active days, the total, and the current streak
    expect(page.locator("#profile-data")).to_contain_text("最近 8 周有 9 天做了题，共 295 道。当前连续 3 天。")
    assert not errors, errors
    page.locator("#profile-data .heat-grid").scroll_into_view_if_needed()
    page.screenshot(path=str(SHOTS / f"charts-calendar-{engine[0]}-{scheme}.png"))
    page.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_type_bars_name_their_subject_and_tell_zero_from_no_data(engine, live_app, scheme):
    page, errors = show(engine, live_app, scheme, profile(RATES, DAYS))
    card = page.locator("#profile-data .chart-block", has_text="题型得分率")
    rows = card.locator(".hbar-row")
    expect(rows).to_have_count(3)
    essay = rows.filter(has_text="问答")
    expect(essay.locator(".hbar-name")).to_have_text(["生化", "细胞"])
    expect(essay.locator(".hbar-num")).to_have_text(["0%", "暂无"])                       # a real 0% versus no answers at all
    assert essay.locator(".hbar-track.none").count() == 1                                  # no answer: a dashed track, not a bare empty bar
    names = card.locator(".hbar-name").evaluate_all("(n) => n.map((x) => getComputedStyle(x, '::before').backgroundColor)")
    assert names[0] != names[1]                                                            # two subject colours (blue, orange)
    # one useful sentence; the 33% cell has a single answer behind it so it says nothing
    expect(card.locator(".chart-conclusion")).to_have_text("问答题得分率最低：生化 0%，建议优先复习。")
    term = rows.filter(has_text="名词解释")
    term.locator(".hbar-track").nth(1).click()
    expect(card.locator(".chart-readout")).to_have_text("名词解释 · 细胞得分率 33%，共 1 题")
    assert inside(page, ".hbar-row, .hbar-track, .hbar-num", ".chart-block") == 0
    assert not errors, errors
    card.scroll_into_view_if_needed()
    page.screenshot(path=str(SHOTS / f"charts-types-{engine[0]}-{scheme}.png"))
    page.close()


def test_no_conclusion_without_enough_answers(engine, live_app):
    thin = [dict(r, answers=1) for r in RATES]
    page, _ = show(engine, live_app, "light", profile(thin, DAYS))
    expect(page.locator("#profile-data .chart-block", has_text="题型得分率").locator(".chart-conclusion")).to_have_text("")
    page.close()


def rich(type_rates=RATES):
    data = profile(type_rates, DAYS)
    data["chapters"] = [dict(subject="biochemistry", chapter=c, rate=r, answers=a) for c, r, a in
                        [("第一章 蛋白质的结构与功能", 0.0, 6), ("第二章 酶", 0.35, 8), ("第三章 糖代谢", 0.62, 12), ("第四章 脂代谢", 0.9, 9)]]
    data["timing"] = {"papers": [], "recorded_questions": 90, "by_type": [
        dict(question_type="single_choice", avg_seconds=40, questions=30, suggested_seconds=48),
        dict(question_type="fill_blank", avg_seconds=70, questions=30, suggested_seconds=48),
        dict(question_type="term", avg_seconds=95, questions=12, suggested_seconds=72),
        dict(question_type="short_answer", avg_seconds=300, questions=6, suggested_seconds=144),
        dict(question_type="essay", avg_seconds=500, questions=3, suggested_seconds=216)]}
    data["reviews"] = {"due_now": 4, "mastered": 12, "next_7_days": [dict(day=(TODAY + timedelta(days=k)).isoformat(), count=c) for k, c in enumerate([4, 0, 2, 0, 0, 7, 1])]}
    return data


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_chart_on_the_profile_page_stays_inside_its_card_and_is_labelled(engine, live_app, scheme):
    page, errors = show(engine, live_app, scheme, rich())
    assert page.evaluate("document.querySelector('.workspace, main.page') && document.querySelector('.workspace, main.page').scrollWidth <= innerWidth + 1")
    assert inside(page, "#profile-data .chart-svg, #profile-data .chart-block, #profile-data .hbar-row, #profile-data .progress-line, #profile-data .chart-legend") == 0
    assert inside(page, "#profile-data svg text", "svg") == 0                                # labels stay inside their own chart
    # a chart that colours by meaning says what the colours mean
    expect(page.locator("#profile-data .chart-block", has_text="平均每题用时").locator(".chart-legend")).to_contain_text("超出建议用时")
    expect(page.locator("#profile-data")).to_contain_text("正确率低于 50%")
    expect(page.locator("#profile-data .chart-block", has_text="未来 7 天").locator(".chart-legend")).to_contain_text("今天")
    for i, section in enumerate(page.locator("#profile-data > section").all()):
        section.scroll_into_view_if_needed()
        section.screenshot(path=str(SHOTS / f"charts-profile-{i}-{engine[0]}-{scheme}.png"))
    assert not errors, errors
    page.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_progress_page_charts_stay_inside_their_cards(engine, live_app, scheme):
    from test_part4_ui import seed

    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, scheme=scheme)
    page.goto(base + "/training#/progress")
    page.wait_for_selector("#funnel .funnel-row")
    assert page.evaluate("document.querySelector('.workspace, main.page').scrollWidth <= innerWidth + 1")
    assert inside(page, "#funnel, .funnel-row, #spend-chart svg, #spend-chart .chart-block, #spend-split .split, .spend-legend", "section.card, .card") == 0
    assert inside(page, "#spend-chart svg text", "svg") == 0
    for i, sel in enumerate(["#funnel", "#spend-chart", "#spend-split"]):
        node = page.locator(sel).first
        if node.count():
            node.locator("xpath=ancestor::section[1]").scroll_into_view_if_needed()
            node.locator("xpath=ancestor::section[1]").screenshot(path=str(SHOTS / f"charts-progress-{i}-{engine[0]}-{scheme}.png"))
    assert not errors, errors
    page.close()
