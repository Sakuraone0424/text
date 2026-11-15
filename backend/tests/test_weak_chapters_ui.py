"""Home 薄弱章节 card: up to five chapters, 「查看全部 →」 opens 我的 at 章节掌握情况, and going back returns to the same place on the
home page. Chromium locally, WebKit iPhone 15 in CI."""
import re

import pytest
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_part4_ui import seed
from test_ui_screens import SHORT_BANK, engine, open_page  # noqa: F401

SCROLLER = ".workspace"


def scroll_top(page):
    return page.evaluate("(sel) => document.querySelector(sel).scrollTop", SCROLLER)


def chapters(n):
    return [{"subject": "biochemistry", "chapter": f"第{i}章", "rate": 0.1 * i, "answers": 3 + i} for i in range(1, n + 1)]


def test_the_card_lists_five_chapters_lowest_rate_first_and_links_to_all(engine, live_app):
    client, dsn, _ = live_app
    seed(client, dsn)                        # seven chapters with graded answers
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base)
    rows = page.locator("#weak-chapters .progress-line")
    expect(rows).to_have_count(5)
    rates = [int(re.search(r"(\d+)%", t).group(1)) for t in rows.all_inner_texts()]
    assert rates == sorted(rates), rates
    expect(page.locator("#weak-all")).to_have_text("查看全部 →")
    expect(page.locator("#weak-all")).to_be_visible()
    assert not errors, errors


@pytest.mark.parametrize("count", [1, 3, 4])
def test_fewer_than_five_chapters_show_as_many_as_there_are(engine, live_app, count):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, overview={**SHORT_BANK, "weak_chapters": chapters(count), "has_data": True})
    expect(page.locator("#weak-chapters .progress-line")).to_have_count(count)
    expect(page.locator("#weak-all")).to_be_visible()


def test_the_empty_state_is_unchanged_and_has_no_link(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, overview={**SHORT_BANK, "weak_chapters": []})
    expect(page.locator("#weak-chapters")).to_contain_text("做完首套后，按章节正确率列出最需要补的 3 章")
    expect(page.locator("#weak-all")).to_be_hidden()
    expect(page.locator("#weak-practice")).to_be_hidden()


def test_view_all_scrolls_to_the_chapter_section_and_back_returns_to_the_same_spot(engine, live_app):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base)
    expect(page.locator("#weak-chapters .progress-line")).to_have_count(5)
    page.locator("#weak-all").scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    left_at = scroll_top(page)
    assert left_at > 0, "the weak-chapters card should need scrolling to reach on a phone"
    page.locator("#weak-all").click()
    page.wait_for_function("location.hash === '#/profile'")
    page.wait_for_selector("#profile-chapters", state="visible")
    page.wait_for_timeout(900)                                           # the page may draw twice; the scroll is repeated
    box = page.evaluate("""() => {
      const s = document.querySelector('.workspace').getBoundingClientRect();
      const t = document.getElementById('profile-chapters').getBoundingClientRect();
      return {offset: t.top - s.top, height: s.height, bottom: t.bottom - s.top};
    }""")
    assert -4 <= box["offset"] <= 60, box                                 # the section heading is at the top of the page
    assert 0 < box["bottom"] < box["height"], box
    assert scroll_top(page) > 0
    page.go_back()
    page.wait_for_function("location.hash === '#/home' || location.hash === ''")
    page.wait_for_timeout(500)
    assert abs(scroll_top(page) - left_at) <= 2, (scroll_top(page), left_at)
    expect(page.locator("#weak-all")).to_be_visible()
    assert not errors, errors
