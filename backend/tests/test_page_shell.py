"""The page shell: only the content area scrolls; scroll position is remembered per page; a focused field is
brought into view; nothing is left below the bottom bar. Chromium locally, WebKit iPhone 15 in CI."""
import pytest
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_ui_screens import engine, open_page, seed  # noqa: F401  (fixtures/helpers)


def scroller_top(page, selector):
    return page.evaluate("(sel) => document.querySelector(sel).scrollTop", selector)


def test_each_main_page_remembers_where_it_was_left(engine, live_app):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "standalone", base)
    expect(page.locator("#stat-objective")).not_to_have_text("—")
    page.locator('nav.primary-nav [data-view="profile"]').click()
    page.wait_for_timeout(500)
    page.evaluate("document.querySelector('.workspace').scrollTop = 900")
    here = scroller_top(page, ".workspace")
    assert here > 300
    page.locator('nav.primary-nav [data-view="mistakes"]').click()
    page.wait_for_timeout(500)
    assert scroller_top(page, ".workspace") == 0              # another page starts at its own top
    page.locator('nav.primary-nav [data-view="profile"]').click()
    page.wait_for_timeout(500)
    assert abs(scroller_top(page, ".workspace") - here) <= 2   # and the first one is where it was left
    assert not page.evaluate("window.scrollY") and not page.evaluate("document.documentElement.scrollTop")
    assert not errors, errors


def test_the_bottom_bar_is_the_last_thing_on_screen_and_the_document_never_scrolls(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, _ = open_page(engine, "safari", base)
    nav = page.locator(".sidebar").bounding_box()
    height = page.evaluate("innerHeight")
    assert abs(nav["y"] + nav["height"] - height) < 1           # nothing below it, nothing behind it
    page.evaluate("window.scrollBy(0, 800)")   # (mobile WebKit has no mouse wheel)
    page.wait_for_timeout(200)
    assert page.evaluate("window.scrollY") == 0                  # the wheel moves the content area only


def test_an_in_page_anchor_scrolls_the_content_area_not_the_document(engine, live_app):
    client, dsn, _ = live_app
    seed(client, dsn)
    base = str(client.base_url).rstrip("/")
    page, _ = open_page(engine, "safari", base, "/training#/model")
    expect(page.locator("#controls")).to_be_visible()
    page.evaluate("document.querySelector('#h-danger').scrollIntoView()")
    page.wait_for_timeout(300)
    assert scroller_top(page, ".workspace") > 200 and page.evaluate("window.scrollY") == 0
    box = page.locator("#h-danger").bounding_box()
    nav_top = page.locator("#savebar").bounding_box()["y"]
    assert 16 <= box["y"] and box["y"] + box["height"] <= nav_top + 1   # visible, between the top gap and the save bar


def test_a_field_that_gets_focus_is_scrolled_into_the_visible_area(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, _ = open_page(engine, "safari", base, "/training#/model")
    expect(page.locator("#controls")).to_be_visible()
    # the page script brings a focused field to the middle of the content area (the keyboard needs the room)
    page.evaluate("document.querySelector('.workspace').scrollTop = 0")
    page.evaluate("document.querySelector('#retry-limit').focus()")
    page.wait_for_timeout(900)
    box = page.locator("#retry-limit").bounding_box()
    height = page.evaluate("innerHeight")
    savebar = page.locator("#savebar").bounding_box()["y"]
    assert 16 <= box["y"] and box["y"] + box["height"] <= savebar
