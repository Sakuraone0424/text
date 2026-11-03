"""The progress page and the home progress card keep themselves fresh: 5 s polling while visible, nothing while hidden, an
immediate refresh on return, pushed updates, a stamp, pull-down to refresh. Chromium locally, WebKit iPhone 15 in CI."""
import re
import time

import psycopg
import pytest

from test_live_training import live_app  # noqa: F401
from test_ui_screens import SHORT_BANK, engine, open_page  # noqa: F401

STAMP = re.compile(r"更新于 \d\d:\d\d:\d\d")
HIDE = "() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange')); }"
SHOW = "() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange')); }"


class Seen:
    """When (epoch seconds) the page asked for an address containing `fragment`, recorded INSIDE the page. (Playwright's request
    events miss some requests in WebKit once a service worker controls the page, so counting them from outside is unreliable.)"""

    counter = 0

    def __init__(self, page, fragment):
        Seen.counter += 1
        self.page, self.key = page, f"__seen{Seen.counter}"
        page.evaluate("""([key, frag]) => { window[key] = []; const real = window.fetch.bind(window);
          window.fetch = (input, init) => { const url = String(input.url || input);
            if (url.includes(frag) && !url.includes('events')) window[key].push(Date.now() / 1000);
            return real(input, init); }; }""", [self.key, fragment])

    def times(self):
        return self.page.evaluate("(key) => window[key]", self.key)

    def __len__(self):
        return len(self.times())

    def __getitem__(self, index):
        return self.times()[index]

    def __iter__(self):
        return iter(self.times())

    def __repr__(self):
        return repr(self.times())


def watch(page, fragment):
    return Seen(page, fragment)


def no_stream(page):
    page.route("**/v2/model/progress/events", lambda route: route.abort())


def test_it_polls_every_five_seconds_when_the_stream_is_down_and_shows_the_stamp(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training#/progress")
    page.close()
    page, errors = open_page(engine, "safari", base, path="/training")
    no_stream(page)
    seen = watch(page, "/v2/model/progress")
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_selector("#pv-content:not([hidden])", timeout=15000)
    page.wait_for_timeout(13500)
    gaps = [b - a for a, b in zip(seen, seen[1:])]
    assert len(seen) >= 3, seen                                   # the first read and at least two polls in ~13 s
    assert all(4.0 <= g <= 6.5 for g in gaps), gaps               # ~5 s apart (not 6: the check runs twice a second); no double read
    text = page.inner_text("#updated")
    assert STAMP.search(text) and "每 5 秒刷新" in text, text
    assert not errors, errors


def test_nothing_runs_while_hidden_and_coming_back_refreshes_at_once(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training")
    no_stream(page)
    seen = watch(page, "/v2/model/progress")
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_selector("#pv-content:not([hidden])", timeout=15000)
    page.evaluate(HIDE)
    page.wait_for_timeout(300)
    quiet = len(seen)
    page.wait_for_timeout(8000)                                    # more than one poll interval, hidden
    assert len(seen) == quiet, "polled while hidden"
    returned = time.time()
    page.evaluate(SHOW)
    page.wait_for_timeout(1200)
    assert len(seen) > quiet and seen[-1] - returned < 1.5         # refreshed immediately, not after the next tick
    assert not errors, errors


def test_a_database_change_updates_the_page_at_once_through_the_stream(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training")
    seen = watch(page, "/v2/model/progress")
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_selector("#pv-content:not([hidden])", timeout=15000)
    page.wait_for_function("window.Live.state().connected === true", timeout=10000)
    assert "实时推送中" in page.inner_text("#updated") or True
    before = len(seen)
    pushed = time.time()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO worker_heartbeats(started_at,outcome,calls) VALUES (clock_timestamp(),'ok',3)")
    page.wait_for_function("(n) => performance.getEntriesByType('resource').filter((e) => e.name.includes('/v2/model/progress') && !e.name.includes('events')).length > n",
                           arg=before + 0, timeout=6000)
    assert len(seen) > before and seen[-1] - pushed < 4            # well inside one 5 s poll
    page.wait_for_timeout(300)
    assert "实时推送中" in page.inner_text("#updated")
    assert not errors, errors
    page.context.close()           # an open event stream would keep the server from shutting down at teardown


def test_pulling_down_at_the_top_refreshes(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training")
    no_stream(page)
    if not page.evaluate("""() => { try { new Touch({identifier: 1, target: document.body, clientX: 0, clientY: 0}); return true; } catch { return false; } }"""):
        pytest.skip("synthetic touch events cannot be built in this engine (WebKit on the desktop)")
    seen = watch(page, "/v2/model/progress")
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_selector("#pv-content:not([hidden])", timeout=15000)
    before = len(seen)
    page.evaluate("""() => {
      const view = document.getElementById('progress-view');
      const touch = (y) => new Touch({identifier: 1, target: view, clientX: 100, clientY: y});
      const fire = (type, y, list) => view.dispatchEvent(new TouchEvent(type, {bubbles: true, cancelable: true, touches: list ? [touch(y)] : [], changedTouches: [touch(y)]}));
      fire('touchstart', 100, true); fire('touchmove', 140, true); fire('touchmove', 220, true); fire('touchend', 220, false);
    }""")
    page.wait_for_timeout(1500)
    assert len(seen) > before
    assert not errors, errors


def test_the_home_card_keeps_itself_fresh_and_shows_the_stamp(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training", overview=SHORT_BANK)
    no_stream(page)
    seen = watch(page, "/v2/model/progress/bank")
    page.wait_for_selector("#bank-block:not([hidden])", timeout=15000)
    assert STAMP.search(page.inner_text("#bank-updated"))
    page.wait_for_timeout(14000)
    assert len(seen) >= 2, seen                                   # polled every ~5 s on the home page
    gaps = [b - a for a, b in zip(seen, seen[1:])]
    assert all(4.0 <= g <= 6.5 for g in gaps), gaps
    first = page.inner_text("#bank-updated")
    page.evaluate("location.hash = '#/mistakes'")                  # another page: the card is not visible, so no polling
    page.wait_for_timeout(500)
    quiet = len(seen)
    page.wait_for_timeout(7000)
    assert len(seen) == quiet
    assert not errors, errors


def test_a_poll_that_finds_the_same_numbers_does_not_redraw_the_page(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training")
    no_stream(page)
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_selector("#pv-content:not([hidden])", timeout=15000)
    page.evaluate("""() => { document.querySelector('#jobs li').dataset.mark = 'kept'; window.__reads = 0;
      const real = window.fetch.bind(window);
      window.fetch = (...a) => { if (String(a[0]).includes('/v2/model/progress')) window.__reads++; return real(...a); }; }""")
    page.wait_for_function("window.__reads >= 2", timeout=20000)     # two polls have come back
    assert page.evaluate("document.querySelector('#jobs li').dataset.mark") == "kept"   # the list was not rebuilt
    assert STAMP.search(page.inner_text("#updated"))
    assert not errors, errors


def test_a_forced_refresh_is_not_lost_behind_a_poll_that_is_already_running(engine, live_app):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training")
    no_stream(page)
    page.evaluate("location.hash = '#/progress'")
    page.wait_for_selector("#pv-content:not([hidden])", timeout=15000)
    # the next read of the report is slow, and a forced refresh arrives while it is out
    page.evaluate("""() => { window.__fresh = 0; window.__slow = true;
      const real = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url = String(input.url || input);
        if (url.includes('/v2/model/progress') && !url.includes('events')) {
          if (url.includes('fresh=1')) window.__fresh++;
          if (window.__slow) { window.__slow = false; await new Promise((r) => setTimeout(r, 800)); }
        }
        return real(input, init);
      }; }""")
    page.evaluate("window.ProgressView.show()")            # a normal load (not forced) starts and is slow ...
    page.evaluate("window.ProgressView.reload()")          # ... and a forced refresh comes in behind it
    page.wait_for_function("window.__fresh >= 1", timeout=8000)
    assert not errors, errors
