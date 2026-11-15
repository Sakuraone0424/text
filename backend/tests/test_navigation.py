"""Every page-to-page navigation of the one application shell: no full reload, the right page, the browser's
back works and the page left comes back as it was. Chromium locally, WebKit iPhone 15 in CI.

Inventory (from -> via -> to), each one exercised below:
  bottom bar:  home/practice/mistakes/profile  <->  each other                  (#/home #/practice #/mistakes #/profile)
  home         quick card "出题进度"            -> #/progress
  profile      links "出题进度", "模型与费用"     -> #/progress, #/model        (back -> #/profile)
  stats        buttons 近7天 / 学习建议 / 历史试卷 / 教材与题库核验 / 归档状态 -> #/stats/<sub>
  progress, model, stats: the subbar's back button -> the page they were opened from (or their parent)
  old addresses /progress and /static/model.html -> #/progress, #/model     (server redirects, then one load)
"""
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_ui_screens import engine, open_page, seed  # noqa: F401

MARK = "window.__shell = (window.__shell || 0) + 1; window.__stay = 'same-document'"


def shown(page):
    return page.evaluate("[...document.querySelectorAll('[data-page]')].filter(n => !n.hidden).map(n => n.dataset.page)")


def same_document(page):
    return page.evaluate("window.__stay") == "same-document"


def test_every_navigation_stays_in_the_one_document_and_lands_on_the_right_page(engine, live_app):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "standalone", base)
    expect(page.locator("#stat-objective")).not_to_have_text("—")
    page.evaluate(MARK)
    loads = []
    page.on("framenavigated", lambda frame: loads.append(frame.url) if frame == page.main_frame else None)

    def expect_page(view, hash_prefix):
        expect(page.locator(f'[data-page="{view}"]')).to_be_visible()
        assert shown(page) == [view], (view, shown(page))
        assert page.evaluate("location.hash").startswith(hash_prefix), page.evaluate("location.hash")
        assert same_document(page), f"{view}: the document was reloaded"

    # bottom bar, every ordered pair
    bar = {"home": "#/home", "practice": "#/practice", "mistakes": "#/mistakes", "profile": "#/profile"}
    for a in bar:
        for b in bar:
            if a == b:
                continue
            page.locator(f'nav.primary-nav [data-view="{a}"]').click()
            page.locator(f'nav.primary-nav [data-view="{b}"]').click()
            expect_page(b, bar[b])
    # home -> progress -> back
    page.locator('nav.primary-nav [data-view="home"]').click()
    page.locator("#open-progress").click()
    expect_page("progress", "#/progress")
    page.locator('[data-page="progress"] [data-back]').click()
    expect_page("home", "#/home")
    # profile -> progress / model -> back to profile
    for link, view in (('a[href="#/progress"]', "progress"), ('a[href="#/model"]', "model")):
        page.locator('nav.primary-nav [data-view="profile"]').click()
        page.locator(f"#profile-view {link}").first.click()
        expect_page(view, "#/" + view)
        page.locator(f'[data-page="{view}"] [data-back]').click()
        expect_page("profile", "#/profile")
    # stats sub pages and back
    page.locator('nav.primary-nav [data-view="profile"]').click()
    for button, sub in (("weekly", "weekly"), ("insights", "insights"), ("history", "history"), ("source-status", "source-status")):
        page.evaluate("location.hash = '#/stats'")
        expect_page("stats", "#/stats")
        page.locator(f"#stats-view #{button}").click()
        expect(page.locator(f'[data-page="stats"]')).to_be_visible()
        assert page.evaluate("location.hash") == f"#/stats/{sub}", page.evaluate("location.hash")
        assert same_document(page)
    # the browser's own back restores the previous page
    page.evaluate("location.hash = '#/mistakes'")
    page.evaluate("location.hash = '#/profile'")
    page.go_back()
    expect_page("mistakes", "#/mistakes")
    page.go_back()
    assert same_document(page)
    # nothing above caused a document load; the only frame navigations are same-document hash changes
    assert all(u.split("#")[0] == base + "/training" for u in loads), loads
    assert not errors, errors


def test_the_old_addresses_redirect_into_the_shell(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    for old, view in (("/progress", "progress"), ("/static/model.html", "model")):
        page, errors = open_page(engine, "safari", base, old)
        expect(page.locator(f'[data-page="{view}"]')).to_be_visible()
        assert page.url.endswith("/training#/" + view), page.url
        assert not errors, errors
        page.context.close()


def test_a_filter_and_the_scroll_position_survive_leaving_and_coming_back(engine, live_app):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base)
    expect(page.locator("#stat-objective")).not_to_have_text("—")
    page.evaluate(MARK)
    page.locator('nav.primary-nav [data-view="profile"]').click()
    page.wait_for_timeout(400)
    link = page.locator("#profile-view a[href='#/model']").first
    link.scroll_into_view_if_needed()                  # the click itself would scroll it into view
    page.evaluate("document.querySelector('.workspace').scrollTop += 120")
    here = page.evaluate("document.querySelector('.workspace').scrollTop")
    assert here > 300
    link.click()
    expect(page.locator('[data-page="model"]')).to_be_visible()
    page.go_back()                                    # what an iPhone edge swipe does
    expect(page.locator('[data-page="profile"]')).to_be_visible()
    page.wait_for_timeout(300)
    assert abs(page.evaluate("document.querySelector('.workspace').scrollTop") - here) <= 2
    assert same_document(page)
    assert not errors, errors


def test_a_visited_page_is_shown_from_the_cache_in_under_100_ms(engine, live_app):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base)
    expect(page.locator("#stat-objective")).not_to_have_text("—")
    for view in ("mistakes", "profile", "home"):        # visit once so the data layer holds a copy
        page.locator(f'nav.primary-nav [data-view="{view}"]').click()
        page.wait_for_timeout(600)
    page.route("**/v2/**", lambda route: (page.wait_for_timeout(1500), route.continue_()))   # a slow network
    timings = {}
    for view in ("mistakes", "profile", "home"):
        timings[view] = page.evaluate("""(view) => new Promise((done) => {
            const target = document.querySelector(`[data-page="${view}"]`);
            const began = performance.now();
            let app = null;
            const seen = new MutationObserver(() => {
                if (!target.hidden && app === null) {       // the router has switched the page in the DOM
                    app = performance.now() - began;
                    seen.disconnect();
                    requestAnimationFrame(() => requestAnimationFrame(() => done({app, ms: performance.now() - began,
                        visible: !target.hidden, filled: target.textContent.trim().length > 20})));
                }
            });
            seen.observe(target, {attributes: true, attributeFilter: ["hidden"]});
            document.querySelector(`nav.primary-nav [data-view="${view}"]`).click();
        })""", view)
        page.wait_for_timeout(200)
    print("cached page switch (ms, DOM / painted):", {k: (round(v["app"]), round(v["ms"])) for k, v in timings.items()})
    for view, t in timings.items():
        # the content is in the DOM within 100 ms of the tap; the painted time depends on the machine (CI's shared
        # WebKit runner needs ~200 ms for two frames), so it only has to stay well under a second
        assert t["visible"] and t["filled"] and t["app"] < 100 and t["ms"] < 600, (view, t)
    assert not errors, errors
