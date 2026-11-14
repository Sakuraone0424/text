"""Every page on an iPhone 15 profile: WebKit (CI) and Chromium, Safari and home-screen (standalone) mode, empty and
seeded data, light and dark. Checks the page shell (the document never scrolls: 16px gap, scrolling content,
bottom bar), that nothing of the page reaches the top 16px, and saves a still and a scrolled-to-the-middle screenshot
of each page (SHOT_DIR) for review.

WebKit cannot be downloaded in every sandbox; its cases skip when the browser is missing (CI installs it).
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
    "today": "2026-11-02", "exam_date": "2026-12-19", "days_left": 47, "streak": {"current": 0, "longest": 0},
    "today_paper": {"state": "none"},
    "last7": {"papers": 0, "avg_rate": None, "avg_points": None, "max_points": None, "delta_points": None},
    "today_papers": {}, "pending_by_kind": {},
    "last7_by_kind": {k: {"papers": 0, "avg_rate": None, "avg_points": None, "max_points": None, "delta_points": None} for k in ("objective", "subjective", "mixed")},
    "objective_rate": None, "subjective_rate": None, "weak_chapters": [],
    "reviews": {"due_now": 0, "next_7_days": [{"day": f"2026-11-{2 + k:02d}", "count": 0} for k in range(7)], "mastered": 0},
    "tomorrow": {"ready": 4, "needed": 60, "missing": 56, "complete": False, "eta_at": "2026-11-02T15:00:00+00:00", "eta_hours": 6.0, "eta_state": "estimated", "per_hour": 2.0},
    "has_data": False,
}


def open_page(engine, mode, base, path="/training", logged_in=True, overview=None, scheme="light", init_script=None):
    name, browser, device = engine
    context = browser.new_context(**{**device, "viewport": {"width": 393, "height": 852}, "color_scheme": scheme})
    if mode == "standalone":
        context.add_init_script(STANDALONE)
    if init_script:
        context.add_init_script(init_script)       # runs before the page's own scripts (every engine)
    page = context.new_page()
    if overview is not None:
        # The live fixture already prepared a paper for today; this is a learner without one.
        page.route("**/v2/stats/overview", lambda route: route.fulfill(json=overview))
        page.route("**/v2/model/progress/bank", lambda route: route.fulfill(json=overview["tomorrow"]))        # the live refresh of the same card
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if logged_in:
        # The session cookie is Secure, which WebKit refuses over plain http; sign in over the API and
        # hand the cookie to the browser context (a real device uses https).
        import httpx
        reply = httpx.post(base + "/auth/login", json={"password": "test-live-login"}, trust_env=False)
        reply.raise_for_status()
        context.add_cookies([{"name": "ustc_quiz_session", "value": reply.cookies["ustc_quiz_session"], "url": base}])
    page.goto(base + path)
    return page, errors


SCROLLER = ".workspace, main.page"


def check_shell(page, still=True):
    """The document itself never scrolls and no page content lies in the 16px under the status bar."""
    assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1 && document.body.scrollHeight <= innerHeight + 1"), page.url
    assert page.evaluate("document.querySelector('meta[name=viewport]').content") == "width=device-width, initial-scale=1"
    assert page.evaluate("getComputedStyle(document.body).overflow") == "hidden"
    # whatever is at the very top of the screen is the page background, never a heading, card or bar
    tops = page.evaluate("""() => [4, 12].flatMap((y) => [10, innerWidth / 2, innerWidth - 10].flatMap((x) =>
        document.elementsFromPoint(x, y).map((e) => e.tagName)))""")
    assert set(tops) <= {"HTML", "BODY"}, tops
    first = page.evaluate("""() => { const h = [...document.querySelectorAll('h1, h2, .date-line, .subbar, .page-head, .focus-toolbar')]
        .filter((n) => n.getClientRects().length && getComputedStyle(n).position !== 'fixed' && n.getBoundingClientRect().top >= 0)
        .map((n) => n.getBoundingClientRect().top); return h.length ? Math.min(...h) : null; }""")
    assert not still or first is None or first >= 16, first  # (scrolled content is clipped at the scroller's top edge)


def snap(page, engine, mode, state, name):
    """A still and a scrolled-to-the-middle screenshot, with the shell checks at both."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    check_shell(page)
    page.screenshot(path=str(SHOTS / f"{engine[0]}-{mode}-{state}-{name}.png"))
    moved = page.evaluate("""(sel) => { const s = [...document.querySelectorAll(sel)].find((n) => n.getClientRects().length);
        if (!s) return 0; s.scrollTop = Math.max(0, (s.scrollHeight - s.clientHeight) / 2); return s.scrollTop; }""", SCROLLER)
    page.wait_for_timeout(150)
    check_shell(page, still=False)
    page.screenshot(path=str(SHOTS / f"{engine[0]}-{mode}-{state}-{name}-scrolled.png"))
    page.evaluate("(sel) => { const s = [...document.querySelectorAll(sel)].find((n) => n.getClientRects().length); if (s) s.scrollTop = 0; }", SCROLLER)
    return moved


def fits(page):
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), page.url


def clear_of_the_top_inset(page, mode=None):
    check_shell(page)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("mode", ["safari", "standalone"])
def test_empty_state_pages(engine, live_app, mode, scheme):
    client, dsn, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, mode, base, overview=SHORT_BANK, scheme=scheme)
    # home with a short bank: grey button that says why, no made-up numbers
    expect(page.locator("#days-left")).not_to_have_text("—")
    expect(page.locator("#prepare-objective")).to_be_disabled()
    expect(page.locator("#prepare-objective")).to_have_text(re.compile(r"^题库还差 56 道，预计 .*\d\d:\d\d 可开始$"))      # no old question fills a paper: it says when
    expect(page.locator("#paper-state-objective")).to_have_text("题库准备中")
    expect(page.locator("#bank-eta-objective")).to_contain_text("预计")
    expect(page.locator("#streak-days")).to_have_class(re.compile("zero"))
    expect(page.locator("#stat-avg-objective")).to_have_text("—")
    expect(page.locator("#stat-avg-objective-note")).to_contain_text("做完首套后显示")
    assert page.locator("#status").evaluate("(n) => getComputedStyle(n).position") == "fixed"  # a toast, not a status line in the page
    expect(page.locator("#status")).not_to_contain_text("已登录")  # switching pages and refreshing never shows it
    toast = page.locator("#status").evaluate("(n) => getComputedStyle(n).bottom")
    assert toast.endswith("px") and float(toast[:-2]) >= 56 + 12  # lives above the bottom nav, never over a title
    page_colour = "rgb(244, 246, 250)" if scheme == "light" else "rgb(14, 16, 20)"
    assert page.evaluate("getComputedStyle(document.body, '::before').height") == "16px"  # the gap above the title
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == page_colour
    card = "rgb(255, 255, 255)" if scheme == "light" else "rgb(23, 26, 33)"
    assert page.locator(".stat-card").first.evaluate("(n) => getComputedStyle(n).backgroundColor") == card
    assert page.evaluate("document.querySelector('meta[name=theme-color][media*=dark]').content") == "#0E1014"
    nav = page.locator(".sidebar").bounding_box()
    # Safari: the bar is 56 px; home-screen mode adds a fixed 28-34 px gap below the icons (no viewport-fit=cover)
    expected = 56 if mode == "safari" else range(56 + 28, 56 + 34 + 1)
    assert (nav["height"] == expected) if mode == "safari" else (nav["height"] in expected)
    assert abs(nav["y"] + nav["height"] - page.evaluate("innerHeight")) < 1  # the bar sits at the bottom, nothing below it
    clear_of_the_top_inset(page, mode)
    fits(page)
    snap(page, engine, mode, ("" if scheme == "light" else "dark-") + "empty", "home")
    for view, name in (("mistakes", "mistakes"), ("profile", "profile")):
        page.locator(f'nav.primary-nav [data-view="{view}"]').click()
        page.wait_for_timeout(400)
        clear_of_the_top_inset(page, mode)
        fits(page)
        if view == "mistakes":
            # the full set of choices is there with no mistakes at all
            expect(page.locator("#filter-subject button")).to_have_text(["全部 0", "生化 0", "细胞 0"])
            expect(page.locator("#filter-type button")).to_have_text(["全部题型", "单选 0", "填空 0", "名词解释 0", "简答 0", "问答 0"])
            # zero days draw only the baseline: no little squares; the strip is named
            heights = page.locator("#due-mini i").evaluate_all("(els) => els.map((e) => e.getBoundingClientRect().height)")
            assert heights and all(h == 0 for h in heights), heights
            expect(page.locator(".due-side .stat-label")).to_have_text("未来 7 天")
        snap(page, engine, mode, ("" if scheme == "light" else "dark-") + "empty", name)
    progress, perrors = open_page(engine, mode, base, "/training#/progress", scheme=scheme)
    expect(progress.locator("#pv-content")).to_be_visible()
    fits(progress)
    snap(progress, engine, mode, ("" if scheme == "light" else "dark-") + "empty", "progress")
    # (signed in through the API: WebKit refuses the Secure session cookie from the form over plain http)
    model, merrors = open_page(engine, mode, base, "/training#/model", scheme=scheme)
    expect(model.locator("#controls")).to_be_visible()
    fits(model)
    snap(model, engine, mode, ("" if scheme == "light" else "dark-") + "empty", "model")
    login_page, lerrors = open_page(engine, mode, base, "/training", logged_in=False, scheme=scheme)
    expect(login_page.locator("#login")).to_be_visible()
    fits(login_page)
    snap(login_page, engine, mode, ("" if scheme == "light" else "dark-") + "empty", "login")
    assert not (errors or perrors or merrors or lerrors), (errors, perrors, merrors, lerrors)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("mode", ["safari", "standalone"])
def test_seeded_pages(engine, live_app, mode, scheme):
    client, dsn, blueprint = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, mode, base, scheme=scheme)
    expect(page.locator("#stat-objective")).not_to_have_text("—")
    expect(page.locator("#weak-chapters .progress-line")).to_have_count(5)
    clear_of_the_top_inset(page, mode)
    fits(page)
    snap(page, engine, mode, ("" if scheme == "light" else "dark-") + "data", "home")
    for view, name in (("mistakes", "mistakes"), ("profile", "profile")):
        page.locator(f'nav.primary-nav [data-view="{view}"]').click()
        page.wait_for_timeout(600)
        clear_of_the_top_inset(page, mode)
        fits(page)
        snap(page, engine, mode, ("" if scheme == "light" else "dark-") + "data", name)
    page.locator("#profile-history").click()
    page.wait_for_timeout(600)
    fits(page)
    snap(page, engine, mode, ("" if scheme == "light" else "dark-") + "data", "history")
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
        snap(page, engine, mode, ("" if scheme == "light" else "dark-") + "data", name)
        page.locator('nav.primary-nav [data-view="profile"]').click()
        page.wait_for_timeout(400)
    progress, perrors = open_page(engine, mode, base, "/training#/progress", scheme=scheme)
    expect(progress.locator("#pv-content")).to_be_visible()
    fits(progress)
    snap(progress, engine, mode, ("" if scheme == "light" else "dark-") + "data", "progress")
    model, merrors = open_page(engine, mode, base, "/training#/model", scheme=scheme)
    expect(model.locator("#controls")).to_be_visible()
    fits(model)
    snap(model, engine, mode, ("" if scheme == "light" else "dark-") + "data", "model")
    assert not (errors or perrors or merrors), (errors, perrors, merrors)


def test_no_blur_layers_anywhere_in_the_styles():
    for sheet in STATIC.glob("*.css"):
        text = sheet.read_text(encoding="utf-8")
        assert "backdrop-filter" not in text and not re.search(r"filter:\s*blur", text), sheet.name
        # nothing fades, masks or gradients the area under the status bar: plain page colour only
        assert not re.search(r"mask(-image)?\s*:|-webkit-mask|gradient\(", text), sheet.name
    for page in STATIC.glob("*.html"):
        assert not re.search(r"backdrop-filter|filter:\s*blur|mask-image|gradient\(", page.read_text(encoding="utf-8")), page.name
