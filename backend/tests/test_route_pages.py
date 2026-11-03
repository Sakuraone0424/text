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
    held = []
    page.route("**/v2/archive/status", lambda route: held.append(route))
    expect(page.locator("#login")).to_be_hidden()
    go(page, "#/profile")
    go(page, "#/profile/archive")
    for _ in range(50):                      # (WebKit needs a moment longer to send it)
        if held:
            break
        page.wait_for_timeout(100)
    assert held, "the archive request is waiting"
    go(page, "#/mistakes")                   # leave before the answer arrives
    held[0].fulfill(json={"configured": False, "message": "late"})
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
    if (shown(list) && getComputedStyle(list).listStyleType !== 'none' && !list.classList.contains('legacy-list'))
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
    from app.training_spec_v4 import POINTS

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
    expect(row.locator(".name")).to_have_text("单选")
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
    expect(page.locator("#spend-split .legend li")).to_have_count(3)
    assert not page.locator("#ledger").evaluate("(n) => n.open")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert not errors, errors


def test_the_pass_rate_ring_turns_orange_below_half(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, "/training#/progress")
    page.route("**/v2/model/progress*", lambda route: route.fulfill(json={**route.fetch().json(), "review_today": {"reviewed": 10, "passed": 3, "rate": 0.3}}))
    expect(page.locator("#pv-content")).to_be_visible()
    page.evaluate("window.ProgressView.reload()")
    expect(page.locator("#rate-ring")).to_have_class(re.compile(r"\blow\b"))
    expect(page.locator("#review-rate")).to_have_text("30%")
    colour = page.locator("#review-rate").evaluate("(n) => getComputedStyle(n).color")
    assert colour in ("rgb(234, 88, 12)", "rgb(251, 146, 60)"), colour
    page.unroute("**/v2/model/progress*")
    page.route("**/v2/model/progress*", lambda route: route.fulfill(json={**route.fetch().json(), "review_today": {"reviewed": 10, "passed": 6, "rate": 0.6}}))
    page.evaluate("window.ProgressView.reload()")
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
