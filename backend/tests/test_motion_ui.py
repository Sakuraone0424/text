"""Motion and refresh states, in a real browser at iPhone width: shared rules (150-300 ms, transform + opacity, reduced motion
instant), page changes, number roll, charts once, sheets, the skeleton sweep, the pull-to-refresh feel, and the refresh states
(no empty-state text while refreshing, the start button never clickable while loading). WebKit in CI, Chromium here."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_ui_screens import SHORT_BANK, engine, open_page  # noqa: F401

SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))
FORBIDDEN = {"width", "height", "top", "left", "right", "bottom", "margin", "marginTop", "marginLeft", "padding", "fontSize", "inset"}

FULL = {**SHORT_BANK, "has_data": True, "streak": {"current": 1, "longest": 1},
        "last7": {"papers": 1, "avg_rate": 0.62, "avg_points": 187.0, "max_points": 300, "delta_points": None},
        "objective_rate": 0.71, "subjective_rate": 0.4,
        "weak_chapters": [{"subject": "biochemistry", "chapter": "糖代谢", "rate": 0.3, "answers": 6}],
        "tomorrow": {"ready": 60, "needed": 60, "missing": 0, "complete": True, "eta_at": None, "eta_hours": None, "eta_state": "done", "per_hour": 0}}
GRADING = {**FULL, "last7": {"papers": 0, "avg_rate": None, "avg_points": None, "max_points": None, "delta_points": None},
           "objective_rate": 0.66, "subjective_rate": None, "pending_papers": 1}


def serve(page, **routes):
    """Answer fetches in the page itself (WebKit's route() cannot see requests a service worker handles).
    routes: path -> (json, delay_ms). A callable may replace the json to vary it per call."""
    page.context.add_init_script("""(() => { const routes = %s, real = window.fetch.bind(window); window.__calls = {};
        window.__routes = routes;
        window.fetch = (input, init) => { const url = String(input && input.url || input); const hit = Object.keys(window.__routes).find((k) => url.includes(k));
          if (!hit) return real(input, init);
          window.__calls[hit] = (window.__calls[hit] || 0) + 1;
          const r = window.__routes[hit], body = Array.isArray(r) ? r[0] : r, delay = Array.isArray(r) ? r[1] : 0;
          return new Promise((resolve) => setTimeout(() => resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })), delay)); }; })();""" % json.dumps(routes))


def start(engine, live_app, scheme="light", reduced=False, routes=None, path="/training"):
    client, _, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, scheme=scheme)
    if reduced:
        page.emulate_media(reduced_motion="reduce")
    serve(page, **(routes or {"/v2/stats/overview": FULL}))
    page.goto(base + path)
    page.wait_for_selector('nav.primary-nav [data-view="home"]')
    return page, errors


def animated_properties(page):
    """Every property the page is animating or transitioning right now."""
    return page.evaluate("""() => document.getAnimations().flatMap((a) => {
        const e = a.effect; if (!e) return [];
        const names = a.transitionProperty ? [a.transitionProperty] : (e.getKeyframes ? e.getKeyframes().flatMap((k) => Object.keys(k)) : []);
        return names.filter((n) => !['offset', 'easing', 'composite', 'computedOffset'].includes(n)); })""")


# ---- 1. the rules -------------------------------------------------------------------------------------------------------

def test_motion_lives_in_one_stylesheet_and_one_script_used_by_every_page():
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "training.html").read_text()
    assert "/static/motion.css" in html and "/static/motion.js" in html
    css = "".join(p.read_text() for p in root.glob("*.css") if p.name != "motion.css")
    assert "@keyframes" not in css.replace("@keyframes pulse", "")          # no page writes its own animation (the old pulse aside)
    js = (root / "motion.js").read_text()
    assert "prefers-reduced-motion" in js and "cubic-bezier" in js


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_page_change_moves_only_transform_and_opacity(engine, live_app, scheme):
    page, errors = start(engine, live_app, scheme)
    seen = set()
    for view in ("profile", "mistakes", "home"):                          # the four tabs cross-fade
        page.locator(f'nav.primary-nav [data-view="{view}"]').click()
        seen |= set(animated_properties(page))
    page.locator('nav.primary-nav [data-view="profile"]').click()
    page.goto(page.url.split("#")[0] + "#/progress")                      # a sub-page slides in from the right
    page.wait_for_timeout(60)
    seen |= set(animated_properties(page))
    assert not (seen & FORBIDDEN), seen
    assert "opacity" in seen
    assert not errors, errors
    page.close()


def test_a_sub_page_slides_in_from_the_right_and_a_tab_change_only_fades(engine, live_app):
    page, _ = start(engine, live_app)
    page.locator('nav.primary-nav [data-view="profile"]').click()
    frames = page.evaluate("""() => document.getAnimations().map((a) => a.effect.getKeyframes().map((k) => k.transform || ''))""")
    assert not any("translateX" in "".join(f) for f in frames)            # tabs: opacity only
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_timeout(40)
    frames = page.evaluate("""() => document.getAnimations().map((a) => a.effect.getKeyframes ? a.effect.getKeyframes().map((k) => k.transform || '') : [])""")
    assert any("translateX(32px)" in "".join(f) for f in frames)           # push: from the right
    page.close()


def test_the_back_button_slides_the_page_out_to_the_right_then_shows_the_one_below(engine, live_app):
    page, _ = start(engine, live_app)
    page.locator('nav.primary-nav [data-view="profile"]').click()
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_timeout(400)
    page.locator("#progress-view [data-back]").first.click()
    page.wait_for_timeout(60)
    out = page.evaluate("""() => document.getAnimations().some((a) => a.effect.getKeyframes && a.effect.getKeyframes().some((k) => (k.transform || '').includes('100%')))""")
    assert out                                                              # sliding out to the right
    page.wait_for_function("location.hash === '#/profile'")
    page.close()


# ---- reduced motion -------------------------------------------------------------------------------------------------------

def test_with_reduced_motion_everything_is_instant(engine, live_app):
    page, errors = start(engine, live_app, reduced=True)
    assert page.evaluate("Motion.reduced()")
    page.locator('nav.primary-nav [data-view="profile"]').click()
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_timeout(30)
    assert page.evaluate("document.getAnimations().filter((a) => a.playState === 'running' && (a.effect.getComputedTiming().duration > 1)).length") == 0
    # a control's transitions are effectively instant, the skeleton sweep is gone
    assert page.evaluate("parseFloat(getComputedStyle(document.querySelector('#prepare')).transitionDuration) * 1000") <= 0.02
    # a number does not roll: it is final at once
    final = page.evaluate("""() => { const n = document.createElement('span'); document.body.append(n); Motion.num(n, 187); return n.textContent; }""")
    assert final == "187"
    page.close()


# ---- 4. numbers ---------------------------------------------------------------------------------------------------------

def test_numbers_roll_from_zero_the_first_time_and_from_the_old_value_afterwards(engine, live_app):
    page, _ = start(engine, live_app)
    result = page.evaluate("""() => new Promise((resolve) => {
        const n = document.createElement('span'); document.body.append(n);
        Motion.num(n, 100);
        const first = [n.textContent];
        requestAnimationFrame(() => requestAnimationFrame(() => { first.push(n.textContent);
          setTimeout(() => { const settled = n.textContent; Motion.num(n, 120); const second = [n.textContent];
            requestAnimationFrame(() => requestAnimationFrame(() => { second.push(n.textContent);
              setTimeout(() => resolve({ first, settled, second, final: n.textContent }), 600); })); }, 600); })); })""")
    assert result["first"][0] == "0" and float(result["first"][1]) < 100       # from 0 up to the value
    assert result["settled"] == "100"
    assert float(result["second"][0]) == 100 and 100 <= float(result["second"][1]) < 120 and result["final"] == "120"   # not from 0 again
    page.close()


def test_the_home_numbers_roll_in_once_and_a_refresh_does_not_start_from_zero(engine, live_app):
    page, _ = start(engine, live_app)
    expect(page.locator("#stat-avg")).to_have_text("187")
    assert page.locator("#stat-avg").get_attribute("data-roll-value") == "187"
    page.evaluate("Insights.loadHome()")
    values = page.evaluate("""() => new Promise((resolve) => { const out = []; const n = document.getElementById('stat-avg');
        const t = setInterval(() => out.push(n.textContent), 16); setTimeout(() => { clearInterval(t); resolve(out); }, 400); })""")
    assert all(v == "187" for v in values)                                      # the same value: no roll at all
    page.close()


# ---- 5. charts ------------------------------------------------------------------------------------------------------------

def test_a_progress_bar_fills_from_the_left_once(engine, live_app):
    page, _ = start(engine, live_app)
    fill = page.locator("#weak-chapters .hbar-track > i").first
    page.evaluate("() => { window.__seen = []; const f = document.querySelector('#weak-chapters .hbar-track > i'); const t = setInterval(() => window.__seen.push(f.getBoundingClientRect().width), 20); setTimeout(() => clearInterval(t), 700); }")
    page.wait_for_timeout(800)
    widths = page.evaluate("window.__seen")
    assert widths and max(widths) > 4 and min(widths) < max(widths)             # it was growing, then full
    assert page.evaluate("Motion.played.size") > 0
    before = page.evaluate("Motion.played.size")
    page.evaluate("Insights.loadHome()")
    page.wait_for_timeout(200)
    assert page.evaluate("Motion.played.size") == before                        # a refresh does not play it again
    page.close()


# ---- 7. sheets ------------------------------------------------------------------------------------------------------------

def test_dialogs_are_bottom_sheets_that_slide_up_and_down(engine, live_app):
    page, _ = start(engine, live_app)
    page.evaluate("document.getElementById('question-dialog').showModal()")
    assert page.evaluate("getComputedStyle(document.getElementById('question-dialog')).animationName") == "sheet-up"
    box = page.evaluate("(() => { const r = document.getElementById('question-dialog').getBoundingClientRect(); return [r.bottom, innerHeight]; })()")
    assert abs(box[0] - box[1]) < 2                                              # sitting on the bottom edge
    page.evaluate("document.getElementById('question-dialog').close()")
    assert page.evaluate("document.getElementById('question-dialog').classList.contains('closing')")
    page.wait_for_function("!document.getElementById('question-dialog').open")
    page.close()


# ---- 10. skeleton -------------------------------------------------------------------------------------------------------

def test_the_skeleton_has_a_light_sweep_that_loops(engine, live_app):
    page, _ = start(engine, live_app)
    page.evaluate("""() => { const s = document.createElement('div'); s.className = 'skeleton card-sk'; s.id = 'probe'; document.getElementById('home-view').append(s); }""")
    anim = page.evaluate("getComputedStyle(document.getElementById('probe'), '::after').animationName + '|' + getComputedStyle(document.getElementById('probe'), '::after').animationDuration + '|' + getComputedStyle(document.getElementById('probe'), '::after').animationIterationCount")
    assert anim == "shimmer|1.2s|infinite"
    page.close()


# ---- the refresh states ---------------------------------------------------------------------------------------------------

def test_the_start_button_is_not_clickable_while_the_state_loads(engine, live_app):
    page, _ = start(engine, live_app, routes={"/v2/stats/overview": [FULL, 1200]})
    button = page.locator("#prepare")
    assert button.is_disabled() and button.get_attribute("data-loading") == "1"      # a grey placeholder, not a primary button
    expect(page.locator("#paper-state")).not_to_have_text("已就绪")
    expect(button).to_be_enabled(timeout=4000)                                       # real state arrives: ready
    assert button.get_attribute("data-loading") is None
    expect(page.locator("#paper-state")).to_have_text("已就绪")
    page.close()


def test_the_start_button_stays_disabled_when_the_bank_is_short(engine, live_app):
    page, _ = start(engine, live_app, routes={"/v2/stats/overview": SHORT_BANK})
    expect(page.locator("#paper-state")).to_have_text("题库准备中")
    assert page.locator("#prepare").is_disabled() and page.locator("#prepare").get_attribute("data-loading") is None
    page.close()


def test_stat_cards_keep_their_numbers_while_refreshing(engine, live_app):
    page, _ = start(engine, live_app)
    expect(page.locator("#stat-avg")).to_have_text("187")
    page.evaluate("window.__routes['/v2/stats/overview'] = [window.__routes['/v2/stats/overview'], 1500]")      # the next answer is slow
    page.evaluate("Insights.loadHome()")
    page.wait_for_timeout(300)
    assert page.locator("#stat-avg").inner_text() == "187" and page.locator("#stat-objective").inner_text() == "71%"
    for sel in ("#stat-avg-note", "#stat-objective-note", "#stat-subjective-note"):
        assert "做完首套后显示" not in page.locator(sel).inner_text()
    page.close()


def test_a_paper_still_being_graded_is_not_shown_as_no_data(engine, live_app):
    page, _ = start(engine, live_app, routes={"/v2/stats/overview": GRADING})
    expect(page.locator("#stat-avg")).to_have_text("判分中")
    expect(page.locator("#stat-avg-note")).to_have_text("判分完成后显示")
    expect(page.locator("#stat-objective")).to_have_text("66%")                      # the objective answers are already marked
    assert "做完首套后显示" not in page.locator("#home-view").inner_text()
    page.close()


def test_the_first_visit_without_a_kept_copy_shows_placeholders_not_empty_sentences(engine, live_app):
    page, _ = start(engine, live_app, routes={"/v2/stats/overview": [FULL, 1500]})
    assert page.locator("#home-view").get_attribute("data-loading") == "1"
    assert page.evaluate("getComputedStyle(document.getElementById('stat-avg')).color") in ("rgba(0, 0, 0, 0)", "transparent")
    expect(page.locator("#home-view")).not_to_have_attribute("data-loading", "1", timeout=4000)
    page.close()


# ---- 4. pull to refresh (touch input needs Chromium's DevTools protocol here; WebKit's behaviour is the same code) --------

def touch(page, kind, y, x=200):
    session = page.__dict__.setdefault("_cdp", page.context.new_cdp_session(page))
    points = [] if kind == "touchEnd" else [{"x": x, "y": y}]
    session.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})


@pytest.fixture
def chromium_only(engine):
    if engine[0] != "chromium":
        pytest.skip("touch input through the DevTools protocol")


def test_pull_to_refresh_feels_like_ios_and_keeps_the_data(engine, live_app, chromium_only):
    page, errors = start(engine, live_app, routes={"/v2/stats/overview": FULL})
    expect(page.locator("#stat-avg")).to_have_text("187")
    page.evaluate("window.__routes['/v2/stats/overview'] = [window.__routes['/v2/stats/overview'], 1600]")       # a slow refresh
    touch(page, "touchStart", 200)
    moved = []
    for dy in (20, 40, 80, 160, 320):
        touch(page, "touchMove", 200 + dy)
        moved.append((dy, page.evaluate("new DOMMatrix(getComputedStyle(document.getElementById('home-view')).transform).m42")))
    shifts = [m for _, m in moved]
    assert all(b > a for a, b in zip(shifts, shifts[1:]))                         # it follows the finger ...
    assert shifts[-1] < 320 * 0.5 and (shifts[-1] - shifts[-2]) < (shifts[1] - shifts[0]) * 4   # ... with damping: ever harder
    assert page.locator(".ptr-label").inner_text() == "松开刷新" and float(page.locator(".ptr").evaluate("n => getComputedStyle(n).opacity")) > 0.9
    touch(page, "touchMove", 230)
    assert page.locator(".ptr-label").inner_text() == "下拉刷新"                      # back under the threshold
    touch(page, "touchMove", 400)
    touch(page, "touchEnd", 400)
    page.wait_for_timeout(300)
    assert page.locator(".ptr.spin").count() == 1                                  # the indicator turns while it refreshes
    assert page.locator("#stat-avg").inner_text() == "187"                         # the old data stays on screen
    assert 30 < page.evaluate("new DOMMatrix(getComputedStyle(document.getElementById('home-view')).transform).m42") < 70   # resting spot
    assert "更新中" not in page.locator("#home-view").inner_text() and page.locator(".pull-hint").count() == 0
    page.wait_for_function("document.querySelectorAll('.ptr.spin').length === 0", timeout=6000)
    page.wait_for_timeout(300)
    assert page.evaluate("getComputedStyle(document.getElementById('home-view')).transform") == "none"
    assert not errors, errors
    page.close()


def test_a_short_pull_springs_back_without_refreshing(engine, live_app, chromium_only):
    page, _ = start(engine, live_app)
    calls = page.evaluate("window.__calls['/v2/stats/overview']")
    touch(page, "touchStart", 200)
    touch(page, "touchMove", 230)
    touch(page, "touchEnd", 230)
    page.wait_for_timeout(350)
    assert page.evaluate("getComputedStyle(document.getElementById('home-view')).transform") == "none"
    assert page.evaluate("window.__calls['/v2/stats/overview']") == calls
    page.close()


@pytest.mark.parametrize("name,locator", [("mistakes", "#mistakes-view"), ("profile", "#profile-view"), ("progress", "#progress-view"), ("model", "#model-view"), ("home", "#home-view")])
def test_every_refreshing_page_has_the_same_pull_to_refresh(engine, live_app, name, locator):
    page, _ = start(engine, live_app)
    page.evaluate(f"location.hash = '#/{name}'")
    page.wait_for_function(f"!document.querySelector('{locator}').hidden")
    assert page.evaluate("typeof Live.pullToRefresh") == "function"
    assert page.evaluate(f"document.querySelector('{locator}') !== null")
    page.close()
