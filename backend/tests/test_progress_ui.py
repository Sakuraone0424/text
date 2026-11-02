"""Part 2 in a real browser at iPhone width: the progress page after login."""
import os
from pathlib import Path

from playwright.sync_api import expect

from test_live_training import browser, live_app, login  # noqa: F401

PHONE = dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))


def test_the_page_asks_for_login_then_shows_ten_buckets_without_horizontal_scroll(live_app, browser):
    client, dsn, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    page = browser.new_context(**PHONE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/progress")
    expect(page.locator("#notice")).to_contain_text("需要登录")
    login(page, base)
    page.goto(base + "/progress")
    expect(page.locator("#content")).to_be_visible()
    expect(page.locator("#bank .row")).to_have_count(10)
    expect(page.locator("#bank .subject")).to_have_text(["生物化学", "细胞生物学"])
    expect(page.locator("#paper-main")).to_contain_text("还差 60 题")
    expect(page.locator("#worker")).to_contain_text("还没有运行记录")
    expect(page.locator("#period")).not_to_be_empty()
    expect(page.locator("#spend-month")).to_contain_text("¥")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / "part2-progress.png"), full_page=True)
    # the home page links to it
    page.goto(base + "/training")
    expect(page.locator("#open-progress")).to_be_visible()
    assert not errors, errors
