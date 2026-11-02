"""Every page on an iPhone 15 profile: WebKit (CI) and Chromium, Safari and home-screen (standalone) mode, empty and
seeded data. Checks fit and safe areas, and saves a screenshot of each page (SHOT_DIR) for review.

WebKit cannot be downloaded in every sandbox; its cases skip when the browser is missing (CI installs it).
Safe areas are simulated with --sat-test / --sab-test (headless browsers report none): 47 px top and 34 px
bottom in home-screen mode, 0 / 34 px in Safari.
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from test_live_training import live_app, login  # noqa: F401
from test_part4_ui import LEARNER, seed

SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
INSETS = {"safari": (0, 34), "standalone": (47, 34)}
STANDALONE = """
Object.defineProperty(navigator, 'standalone', {value: true});
const real = window.matchMedia.bind(window);
window.matchMedia = (q) => /display-mode:\\s*standalone/.test(q) ? Object.assign(real('all'), {matches: true, media: q}) : real(q);
"""


@pytest.fixture(params=["webkit", "chromium"])
def engine(request):
    with sync_playwright() as p:
        try:
            browser = getattr(p, request.param).launch()
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"{request.param} is not installed here: {str(error)[:80]}")
        device = p.devices["iPhone 15"]
        yield request.param, browser, device
        browser.close()


SHORT_BANK = {
    "today": "2026-11-02", "exam_date": "2026-12-21", "days_left": 49, "streak": {"current": 0, "longest": 0},
    "today_paper": {"state": "none"},
    "last7": {"papers": 0, "avg_rate": None, "avg_points": None, "max_points": None, "delta_points": None},
    "objective_rate": None, "subjective_rate": None, "weak_chapters": [],
    "reviews": {"due_now": 0, "next_7_days": [{"day": f"2026-11-{2 + k:02d}", "count": 0} for k in range(7)], "mastered": 0},
    "tomorrow": {"ready": 4, "needed": 60, "missing": 56, "complete": False, "eta_at": "2026-11-02T15:00:00+00:00", "eta_hours": 6.0, "eta_state": "estimated", "per_hour": 2.0},
    "has_data": False,
}


def open_page(engine, mode, base, path="/training", logged_in=True, overview=None):
    name, browser, device = engine
    context = browser.new_context(**{**device, "viewport": {"width": 393, "height": 852}})
    if mode == "standalone":
        context.add_init_script(STANDALONE)
    page = context.new_page()
    if overview is not None:
        # The live fixture already prepared a paper for today; this is a learner without one.
        page.route("**/v2/stats/overview", lambda route: route.fulfill(json=overview))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    top, bottom = INSETS[mode]
    if logged_in:
        # The session cookie is Secure, which WebKit refuses over plain http; sign in over the API and
        # hand the cookie to the browser context (a real device uses https).
        import httpx
        reply = httpx.post(base + "/auth/login", json={"password": "test-live-login"}, trust_env=False)
        reply.raise_for_status()
        context.add_cookies([{"name": "ustc_quiz_session", "value": reply.cookies["ustc_quiz_session"], "url": base}])
    page.goto(base + path)
    page.add_style_tag(content=f":root, .training-app {{ --sat-test: {top}px; --sab-test: {bottom}px; }}")
    return page, errors


def snap(page, engine, mode, state, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{engine[0]}-{mode}-{state}-{name}.png"), full_page=True)


def fits(page):
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), page.url


def clear_of_the_top_inset(page, mode):
    """Nothing may be laid out inside the top inset (status bar / notch / dynamic island)."""
    top = INSETS[mode][0]
    boxes = page.evaluate("""() => [...document.querySelectorAll('h1, h2, .date-line, .subbar, .page-head, .focus-toolbar')]
        .filter((n) => n.getClientRects().length && getComputedStyle(n).position !== 'fixed')
        .map((n) => n.getBoundingClientRect().top + window.scrollY)""")
    assert boxes and min(boxes) >= top, (min(boxes) if boxes else None, top)


@pytest.mark.parametrize("mode", ["safari", "standalone"])
def test_empty_state_pages(engine, live_app, mode):
    client, dsn, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, mode, base, overview=SHORT_BANK)
    # home with a short bank: grey button that says why, no made-up numbers
    expect(page.locator("#days-left")).not_to_have_text("—")
    expect(page.locator("#prepare")).to_be_disabled()
    expect(page.locator("#prepare")).to_have_text("题库还差 56 道，暂不能开始")
    expect(page.locator("#paper-state")).to_have_text("题库准备中")
    expect(page.locator("#bank-eta")).to_contain_text("预计")
    expect(page.locator("#streak-days")).to_have_class(re.compile("zero"))
    expect(page.locator("#stat-avg")).to_have_text("—")
    expect(page.locator("#stat-avg-note")).to_contain_text("做完首套后显示")
    assert page.locator("#status").evaluate("(n) => getComputedStyle(n).position") == "fixed"  # a toast, not a status line in the page
    expect(page.locator("#status")).not_to_contain_text("已登录")  # switching pages and refreshing never shows it
    toast = page.locator("#status").evaluate("(n) => getComputedStyle(n).bottom")
    assert toast.endswith("px") and float(toast[:-2]) >= 56 + INSETS[mode][1]  # lives above the bottom nav, never over a title
    band = page.evaluate("() => { const s = getComputedStyle(document.body, '::before'); return [s.position, s.height, s.backgroundColor]; }")
    assert band == ["fixed", f"{INSETS[mode][0]}px", "rgb(244, 246, 250)"], band  # the opaque status-bar band
    assert page.locator(".sidebar").bounding_box()["height"] == 56 + INSETS[mode][1]
    clear_of_the_top_inset(page, mode)
    fits(page)
    snap(page, engine, mode, "empty", "home")
    for view, name in (("mistakes", "mistakes"), ("profile", "profile")):
        page.locator(f'nav.primary-nav [data-view="{view}"]').click()
        page.wait_for_timeout(400)
        clear_of_the_top_inset(page, mode)
        fits(page)
        if view == "mistakes":
            # the full set of choices is there with no mistakes at all
            expect(page.locator("#filter-subject button")).to_have_text(["全部", "生化", "细胞"])
            expect(page.locator("#filter-type button")).to_have_text(["全部", "单选", "填空", "名词解释", "简答", "问答"])
            # zero days draw only the baseline: no little squares; the strip is named
            heights = page.locator("#due-mini i").evaluate_all("(els) => els.map((e) => e.getBoundingClientRect().height)")
            assert heights and all(h == 0 for h in heights), heights
            expect(page.locator(".due-side .stat-label")).to_have_text("未来 7 天")
        snap(page, engine, mode, "empty", name)
    progress, perrors = open_page(engine, mode, base, "/progress")
    expect(progress.locator("#content")).to_be_visible()
    fits(progress)
    snap(progress, engine, mode, "empty", "progress")
    # (signed in through the API: WebKit refuses the Secure session cookie from the form over plain http)
    model, merrors = open_page(engine, mode, base, "/static/model.html")
    expect(model.locator("#controls")).to_be_visible()
    fits(model)
    snap(model, engine, mode, "empty", "model")
    login_page, lerrors = open_page(engine, mode, base, "/training", logged_in=False)
    expect(login_page.locator("#login")).to_be_visible()
    fits(login_page)
    snap(login_page, engine, mode, "empty", "login")
    assert not (errors or perrors or merrors or lerrors), (errors, perrors, merrors, lerrors)


@pytest.mark.parametrize("mode", ["safari", "standalone"])
def test_seeded_pages(engine, live_app, mode):
    client, dsn, blueprint = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, mode, base)
    expect(page.locator("#stat-avg")).not_to_have_text("—")
    expect(page.locator("#weak-chapters .progress-line")).to_have_count(3)
    clear_of_the_top_inset(page, mode)
    fits(page)
    snap(page, engine, mode, "data", "home")
    for view, name in (("mistakes", "mistakes"), ("profile", "profile")):
        page.locator(f'nav.primary-nav [data-view="{view}"]').click()
        page.wait_for_timeout(600)
        clear_of_the_top_inset(page, mode)
        fits(page)
        snap(page, engine, mode, "data", name)
    page.locator("#profile-history").click()
    page.wait_for_timeout(600)
    fits(page)
    snap(page, engine, mode, "data", "history")
    # the other sub-views under "我的" follow the same rules: insights and the archive status
    for action, name in (("#stats-back", None), ('[data-action="insights"]', "insights"), ("#archive", "archive")):
        if name is None:
            page.locator(action).click()
            page.locator('nav.primary-nav [data-view="profile"]').click()
            page.wait_for_timeout(400)
            continue
        page.locator(action).first.click()
        page.wait_for_timeout(700)
        fits(page)
        snap(page, engine, mode, "data", name)
        page.locator('nav.primary-nav [data-view="profile"]').click()
        page.wait_for_timeout(400)
    progress, perrors = open_page(engine, mode, base, "/progress")
    expect(progress.locator("#content")).to_be_visible()
    fits(progress)
    snap(progress, engine, mode, "data", "progress")
    model, merrors = open_page(engine, mode, base, "/static/model.html")
    expect(model.locator("#controls")).to_be_visible()
    fits(model)
    snap(model, engine, mode, "data", "model")
    assert not (errors or perrors or merrors), (errors, perrors, merrors)


def test_no_blur_layers_anywhere_in_the_styles():
    for sheet in STATIC.glob("*.css"):
        text = sheet.read_text(encoding="utf-8")
        assert "backdrop-filter" not in text and not re.search(r"filter:\s*blur", text), sheet.name
        # nothing fades, masks or gradients the area under the status bar: plain page colour only
        assert not re.search(r"mask(-image)?\s*:|-webkit-mask|gradient\(", text), sheet.name
    for page in STATIC.glob("*.html"):
        assert not re.search(r"backdrop-filter|filter:\s*blur|mask-image|gradient\(", page.read_text(encoding="utf-8")), page.name
