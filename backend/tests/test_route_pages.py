"""One shell, many routes: every route shows only its own content, every route is styled (directly opened and reached by
navigation), and the progress page draws its numbers as ring, bars, dots and a split bar. Chromium locally, WebKit iPhone 15 in CI."""
import re

import pytest
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_ui_screens import engine, open_page, seed, snap  # noqa: F401

MAIN = ["#/home", "#/mistakes", "#/profile", "#/stats", "#/progress", "#/model"]
# (route that opens the sub-page, text that only that sub-page shows)
SUBS = [
    ("#/stats/weekly", None),
    ("#/stats/history", None),
    ("#/stats/insights", None),
    ("#/stats/source-status", "教材与60题题库核验"),
    ("#/profile/archive", "Notion归档状态"),
]


# Playwright's route() does not see requests a service worker makes in every engine (WebKit), so the two tests that need to
# hold or alter one patch fetch inside the page instead: the same in every browser.
HOLD_FETCH = """(part) => {
  const real = window.fetch.bind(window);
  window.__held = [];
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    if (!url.includes(part)) return real(input, init);
    return new Promise((resolve) => window.__held.push(() => resolve(new Response('{"configured": false, "message": "late"}', {status: 200, headers: {'Content-Type': 'application/json'}}))));
  };
}"""
MERGE_FETCH = """([part, merge]) => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    if (!url.includes(part)) return real(input, init);
    return real(input, init).then(async (r) => new Response(JSON.stringify({...(await r.json()), ...merge}), {status: 200, headers: {'Content-Type': 'application/json'}}));
  };
}"""


def go(page, route):
    page.evaluate("(r) => { location.hash = r }", route)
    page.wait_for_timeout(500)


def learning_children(page):
    return page.evaluate("document.getElementById('learning').childElementCount")


def visible_page_text(page):
    return page.evaluate("[...document.querySelectorAll('[data-page]')].filter((n) => !n.hidden).map((n) => n.innerText).join('\\n')")


# ---------------------------------------------------------------- 一: a route shows only its own content

@pytest.mark.parametrize("sub,marker", SUBS)
def test_a_sub_page_never_shows_up_on_a_main_page(engine, live_app, sub, marker):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base)
    expect(page.locator("#login")).to_be_hidden()
    go(page, "#/home")
    go(page, sub)
    page.wait_for_timeout(600)
    assert learning_children(page) > 0, "the sub-page itself must show its content"
    if marker:
        assert marker in visible_page_text(page)
    for main in MAIN:                       # then every main page, one after the other
        go(page, main)
        assert learning_children(page) == 0, f"{sub} left content behind on {main}"
        if marker:                           # (titles that are also a button label elsewhere are covered by the empty check)
            assert marker not in visible_page_text(page), f"{marker!r} shows on {main}"
        assert page.locator("#learning").is_hidden()
    # back out of the sub-page with the browser's own back button
    go(page, "#/profile")
    go(page, sub)
    page.wait_for_timeout(500)
    page.go_back()
    page.wait_for_timeout(500)
    assert learning_children(page) == 0
    assert not errors, errors


def test_a_sub_page_that_finishes_loading_after_you_left_does_not_draw_itself(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base)
    expect(page.locator("#login")).to_be_hidden()
    page.evaluate(HOLD_FETCH, "/v2/archive/status")
    go(page, "#/profile")
    go(page, "#/profile/archive")
    page.wait_for_function("window.__held.length > 0")
    go(page, "#/mistakes")                   # leave before the answer arrives
    page.evaluate("window.__held.forEach((release) => release())")
    page.wait_for_timeout(500)
    assert learning_children(page) == 0
    assert "Notion归档状态" not in visible_page_text(page)
    go(page, "#/profile")
    assert learning_children(page) == 0 and "Notion归档状态" not in visible_page_text(page)
    assert not errors, errors


# ---------------------------------------------------------------- 二: nothing is left in browser-default style

UNSTYLED = """
() => {
  const bad = [];
  const shown = (n) => n.getClientRects().length > 0 && !n.closest('[hidden]');
  const root = [...document.querySelectorAll('[data-page]')].filter((n) => !n.hidden)[0];
  if (!root) return ['no visible page'];
  for (const list of root.querySelectorAll('ol, ul')) {
    if (shown(list) && getComputedStyle(list).listStyleType !== 'none' && !list.classList.contains('legacy-list') && !list.classList.contains('steps'))
      bad.push('list numbering/bullets: ' + (list.id || list.className || list.tagName));
  }
  for (const s of root.querySelectorAll('summary')) {
    if (shown(s) && getComputedStyle(s).listStyleType !== 'none') bad.push('bare details triangle: ' + s.textContent.trim().slice(0, 20));
  }
  for (const b of root.querySelectorAll('button')) {
    if (!shown(b)) continue;
    const c = getComputedStyle(b);
    if (c.borderTopStyle === 'outset' || ['rgb(239, 239, 239)', 'rgb(107, 107, 107)'].includes(c.backgroundColor))
      bad.push('unstyled button: ' + (b.id || b.textContent.trim().slice(0, 20)));
  }
  return bad;
}
"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_route_is_styled_when_opened_directly_and_when_navigated_to(engine, live_app, scheme):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    routes = MAIN + [r for r, _ in SUBS]
    for route in routes:                                     # opened directly
        page, errors = open_page(engine, "safari", base, "/training" + route, scheme=scheme)
        page.wait_for_timeout(900)
        assert page.evaluate(UNSTYLED) == [], (route, "direct")
        assert not errors, errors
    page, errors = open_page(engine, "safari", base, scheme=scheme)   # reached by navigation, in one session
    page.wait_for_timeout(500)
    for route in routes:
        go(page, route)
        page.wait_for_timeout(500)
        assert page.evaluate(UNSTYLED) == [], (route, "navigated")
    assert not errors, errors
    # the stylesheet that styles the progress and model pages is actually part of the page
    assert page.evaluate("[...document.styleSheets].some((s) => (s.href || '').endsWith('/static/views.css'))")


# ---------------------------------------------------------------- 三: the progress page as a picture

def seed_progress(dsn, review=None):
    from app.training_spec_v5 import POINTS

    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        uid = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
        n = 0
        for kind, state, qtype, code in (
            ("generation", "blocked", "term", "insufficient_textbook_evidence"), ("generation", "blocked", "term", "insufficient_textbook_evidence"),
            ("generation", "blocked", "term", "insufficient_textbook_evidence"), ("generation", "blocked", "fill_blank", "output_schema_violation"),
            ("generation", "blocked", "single_choice", "brand_new_code_nobody_translated"), ("generation", "succeeded", "single_choice", None),
            ("generation", "succeeded", "fill_blank", None), ("quality_review", "blocked", "term", "evidence_version_changed"),
        ):
            n += 1
            conn.execute(
                "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code,result,finished_at) "
                "VALUES (%s,%s,%s,%s,'{}',%s,%s,%s,clock_timestamp())",
                (f"seed-{n}", uid, kind, state, Jsonb({"subject": "biochemistry", "question_type": qtype, "max_points": POINTS[qtype]}),
                 "invalid_or_uncertain_result" if code else None, Jsonb({"dispatch_failure": {"code": code}} if code else {"real_api": True})),
            )
    return uid


def test_the_progress_page_shows_ring_bars_dots_and_a_split_bar(engine, live_app):
    client, dsn, _ = live_app
    seed_progress(dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, "/training#/progress")
    expect(page.locator("#pv-content")).to_be_visible()
    # top: ring (grey while nothing was reviewed), reserve bar, three small cards
    expect(page.locator("#rate-ring")).to_have_class(re.compile(r"\bnone\b"))
    expect(page.locator(".view-progress .tile")).to_have_count(3 + 4 + 3)          # three at the top, four in the spend card, three in the fold
    for tile in page.locator(".card.key .tile").all():
        assert tile.evaluate("(n) => getComputedStyle(n).borderTopWidth") == "1px"   # card style, not a bare row
    # bank rows: name + bar + stocked/target, then small tags
    row = page.locator("#bank .group:visible .row").first
    expect(row.locator(".name")).to_have_text("填空")
    expect(row.locator(".bar")).to_be_visible()
    expect(row.locator(".count")).to_contain_text("/")
    expect(page.locator("#bank .group:visible .row .tags .tag").first).to_contain_text("今日 +")
    term = page.locator("#bank .group:visible .row", has_text="名词解释").first
    expect(term.locator(".tag.attn")).to_have_text("被挡住 3")                          # 3 of 3 blocked = a lot: orange
    # today's funnel, switchable by type
    expect(page.locator("#funnel .funnel-row")).to_have_count(4)
    expect(page.locator("#funnel-types button")).to_have_text(["全部", "单选", "填空", "名词解释", "简答", "问答"])
    page.locator('#funnel-types [data-type="term"]').click()
    expect(page.locator('#funnel-types [data-type="term"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#funnel .funnel-row b").first).to_have_text("3")
    # blocked reasons: ranked bars with Chinese text, count and share; unknown codes are folded into 其他, never "原因未归类"
    names = page.locator("#waste .waste-name").all_inner_texts()
    assert names[0] == "教材里没有足够依据，模型判定无法作答" and "其他" in names
    assert "原因未归类" not in page.locator("main, .workspace").first.inner_text()
    first = page.locator("#waste .waste-row").first
    expect(first.locator(".waste-count")).to_contain_text("3 次")
    expect(first.locator(".waste-count")).to_contain_text("%")
    assert first.locator(".wbar > i").evaluate("(n) => n.getBoundingClientRect().width") > 10
    other = page.locator("#waste .waste-row.other")
    other.locator("summary").click()
    expect(other.locator("code")).to_contain_text("brand_new_code_nobody_translated")
    # recent tasks: dot, name, time + reason, tag; no list numbers
    li = page.locator("#jobs li").first
    expect(li.locator(".dot")).to_be_visible()
    assert page.locator("#jobs").evaluate("(n) => getComputedStyle(n).listStyleType") == "none"
    assert li.evaluate("(n) => getComputedStyle(n).display") == "grid"
    # spend: one split bar and the reconciliation folded away
    expect(page.locator("#spend-split .split")).to_be_visible()
    expect(page.locator("#spend-split .spend-legend li")).to_have_count(3)
    assert not page.locator("#ledger").evaluate("(n) => n.open")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert not errors, errors


def test_the_pass_rate_ring_turns_orange_below_half(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, "/training#/progress")
    expect(page.locator("#pv-content")).to_be_visible()
    page.evaluate(MERGE_FETCH, ["/v2/model/progress", {"review_today": {"reviewed": 10, "passed": 3, "rate": 0.3}}])
    page.evaluate("window.ProgressView.reload()")
    expect(page.locator("#rate-ring")).to_have_class(re.compile(r"\blow\b"))
    expect(page.locator("#review-rate")).to_have_text("30%")
    colour = page.locator("#review-rate").evaluate("(n) => getComputedStyle(n).color")
    assert colour in ("rgb(234, 88, 12)", "rgb(251, 146, 60)"), colour
    page.evaluate(MERGE_FETCH, ["/v2/model/progress", {"review_today": {"reviewed": 10, "passed": 6, "rate": 0.6}}])
    page.evaluate("window.ProgressView.reload()")
    expect(page.locator("#review-rate")).to_have_text("60%")
    expect(page.locator("#rate-ring")).not_to_have_class(re.compile(r"\blow\b"))
    assert not errors, errors


# ---------------------------------------------------------------- screenshots (light / dark x Safari / home-screen)

@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("mode", ["safari", "standalone"])
def test_screenshots_of_the_changed_pages(engine, live_app, mode, scheme):
    client, dsn, _ = live_app
    seed(client, dsn)
    seed_progress(dsn)
    base = str(client.base_url).rstrip("/")
    prefix = "" if scheme == "light" else "dark-"
    page, errors = open_page(engine, mode, base, "/training#/progress", scheme=scheme)
    expect(page.locator("#pv-content")).to_be_visible()
    snap(page, engine, mode, prefix + "route", "progress")
    for route, name in (("#/model", "model"), ("#/profile", "profile"), ("#/mistakes", "mistakes"), ("#/profile/archive", "archive"),
                        ("#/stats/weekly", "weekly"), ("#/stats/history", "history"), ("#/stats/insights", "insights"),
                        ("#/stats/source-status", "source-status"), ("#/home", "home")):
        go(page, route)
        page.wait_for_timeout(400)
        snap(page, engine, mode, prefix + "route", name)
    assert not errors, errors


# ---------------------------------------------------------------- PR 2b: the Notion card, the spend legend

NOTION_CARD = "Notion归档状态"


def tap(page, selector, wait=700):
    page.locator(selector).first.click()
    page.wait_for_timeout(wait)


def clean_main_page(page, label):
    assert learning_children(page) == 0, f"{label}: a sub-page left content behind"
    assert NOTION_CARD not in visible_page_text(page), f"{label}: the Notion card is on a main page"
    assert page.locator("#learning").evaluate("(n) => n.getBoundingClientRect().height") == 0, f"{label}: the sub-page container takes room"


def test_the_notion_card_is_only_ever_on_the_archive_sub_page_whatever_the_way_in_and_out(engine, live_app):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    # opened directly: the profile page and the mistakes page
    for route in ("#/profile", "#/mistakes"):
        page, errors = open_page(engine, "safari", base, "/training" + route)
        page.wait_for_timeout(900)
        clean_main_page(page, "direct " + route)
    # with real taps: in, out by the tab bar, in again, out by the browser's back, in and out at once
    page, errors = open_page(engine, "safari", base)
    page.wait_for_timeout(900)
    tap(page, 'nav.primary-nav [data-view="profile"]')
    clean_main_page(page, "profile")
    tap(page, "#archive", 1200)
    assert NOTION_CARD in visible_page_text(page) and learning_children(page) > 0       # here, and only here
    tap(page, 'nav.primary-nav [data-view="mistakes"]', 900)
    clean_main_page(page, "mistakes after archive")
    tap(page, 'nav.primary-nav [data-view="profile"]', 900)
    clean_main_page(page, "profile after archive")
    tap(page, "#archive", 1200)
    page.go_back()
    page.wait_for_timeout(800)
    clean_main_page(page, "back from archive")
    tap(page, "#archive", 80)                                                       # leave before it has loaded
    tap(page, 'nav.primary-nav [data-view="mistakes"]', 1500)
    clean_main_page(page, "left while loading")
    for sub_button in ("#weekly", "#history", "#insights", "#source-status"):          # the stats sub-pages, in and out
        go(page, "#/stats")
        tap(page, sub_button, 1200)
        assert learning_children(page) > 0
        for main in ("#/profile", "#/mistakes", "#/home"):
            go(page, main)
            clean_main_page(page, f"{sub_button} then {main}")
    assert not errors, errors


def test_scripts_and_pages_of_the_shell_are_revalidated_on_every_load(live_app):
    client, _, _ = live_app
    for path in ("/static/training-ui.js", "/static/training.js", "/static/views.css", "/training", "/"):
        assert client.get(path, follow_redirects=True).headers.get("cache-control") == "no-cache", path
    assert "v19-one-shell" in client.get("/training-sw.js").text


def test_the_spend_legend_is_one_row_under_the_bar_at_390px(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, "/training#/progress")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator("#pv-content")).to_be_visible()
    legend = page.locator("#spend-split .spend-legend")
    legend.scroll_into_view_if_needed()
    box, bar, card = legend.bounding_box(), page.locator("#spend-split .split").bounding_box(), page.locator("#spend-split").locator("xpath=ancestor::section[1]").bounding_box()
    assert box["y"] >= bar["y"] + bar["height"] - 1                                  # under the bar, not beside or behind it
    assert box["width"] > 0.8 * bar["width"] and card["x"] <= box["x"] and box["x"] + box["width"] <= card["x"] + card["width"] + 1
    items = legend.locator("li").evaluate_all("(els) => els.map((e) => { const r = e.getBoundingClientRect(); return [r.top, r.height, r.width]; })")
    assert len(items) == 3 and all(h < 32 and w > 60 for _, h, w in items), items        # horizontal text, not a vertical column
    assert len({round(top) for top, _, _ in items}) <= 2                                 # a row (it may wrap to two when narrow)
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.locator("#spend-split").screenshot(path="/tmp/claude-0/shots/" + engine[0] + "-spend-legend-390.png")
    assert not errors, errors


ARCHIVE_STATUS = """() => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    if (!url.includes('/v2/archive/status')) return real(input, init);
    const body = {configured: true, consented: true, last_synced_at: new Date().toISOString(), success: 4, failed: 1, pending: 2,
      recent: [{at: new Date().toISOString(), ok: false, code: 'not_found', message: '找不到这个页面：请把页面分享（连接）给集成，并确认页面 ID 正确', detail: {}},
               {at: new Date().toISOString(), ok: true, code: null, message: null, detail: {training: 1, mistakes: 12}}]};
    return Promise.resolve(new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}}));
  };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_archive_page_shows_status_switch_buttons_records_and_the_setup_steps(engine, live_app, scheme):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, "/training#/profile/archive", scheme=scheme)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator("#learning .archive-card")).to_be_visible()
    expect(page.locator("#learning .tile")).to_have_count(6)
    expect(page.locator("#learning")).to_contain_text("服务器还没有设置 NOTION_TOKEN")      # unconfigured: honest, no pretend sync
    expect(page.get_by_role("button", name="立即同步")).to_be_disabled()
    expect(page.get_by_role("button", name="测试连接")).to_be_enabled()
    page.locator("#learning input[type=checkbox]").check()                                # the switch really saves
    expect(page.locator("#learning")).to_contain_text("同步已打开")
    page.evaluate(ARCHIVE_STATUS)                                                         # a configured server with some history
    go(page, "#/profile")
    go(page, "#/profile/archive")
    expect(page.locator("#learning .sync-list li")).to_have_count(2)
    expect(page.locator("#learning .sync-list li").first).to_contain_text("同步失败：找不到这个页面")
    expect(page.locator("#learning .sync-list li").nth(1)).to_contain_text("12 道错题")
    expect(page.get_by_role("button", name="立即同步")).to_be_enabled()
    help_box = page.locator("#learning details.fold")
    assert not help_box.evaluate("(n) => n.open")
    help_box.locator("summary").click()
    expect(help_box.locator("ol.steps li")).to_have_count(7)
    expect(help_box).to_contain_text("notion.so/my-integrations")
    expect(help_box).to_contain_text("NOTION_PARENT_PAGE_ID")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    snap(page, engine, "safari", ("" if scheme == "light" else "dark-") + "route", "archive-notion")
    assert not errors, errors


@pytest.mark.parametrize("sub,marker", SUBS)
def test_a_sub_page_opened_by_its_address_draws_itself(engine, live_app, sub, marker):
    """A deep link or a home-screen app restored to a sub-page: shell-ready used to be sent before training.js had hooked up the
    sub-pages' buttons, so the page stayed empty."""
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, "/training" + sub)
    page.wait_for_function("document.getElementById('learning').childElementCount > 0", timeout=8000)
    if marker:
        assert marker in visible_page_text(page)
    for main in ("#/mistakes", "#/profile"):
        go(page, main)
        clean_main_page(page, f"{sub} opened directly, then {main}")
    assert not errors, errors
