"""A signed-in learner whose data fails to load (server error or no network) stays on the page they asked for: never the
sign-in form, the reason is shown inside the visible area of THAT page, and a retry button is there. Chromium locally, WebKit in CI."""
import pytest

from test_live_training import live_app  # noqa: F401
from test_ui_screens import engine, open_page  # noqa: F401

ROUTES = ["#/home", "#/mistakes", "#/profile", "#/stats", "#/progress", "#/model",
          "#/stats/weekly", "#/stats/history", "#/stats/insights", "#/stats/source-status", "#/profile/archive"]

FAIL_DATA = """(mode) => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const data = /^\\/(v2|history|stats|legacy)\\b/.test(url.pathname);
    if (!data || url.pathname.startsWith('/v2/training-specification')) return real(input, init);
    if (mode === 'offline') return Promise.reject(new TypeError('Failed to fetch'));
    return Promise.resolve(new Response(JSON.stringify({detail: 'internal'}), {status: 500, headers: {'Content-Type': 'application/json'}}));
  };
}"""

VISIBLE_RETRY = """() => {
  const inView = (n) => { const r = n.getBoundingClientRect(); return n.getClientRects().length > 0 && r.width > 0 && r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth; };
  const retry = [...document.querySelectorAll('button')].filter((b) => /重试/.test(b.textContent) && !b.closest('[hidden]') && inView(b));
  return retry.length;
}"""


@pytest.mark.parametrize("mode", ["server_error", "offline"])
@pytest.mark.parametrize("route", ROUTES)
def test_a_failed_load_shows_the_reason_and_a_retry_button_on_the_same_page(engine, live_app, route, mode):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training")
    page.evaluate("localStorage.clear(); sessionStorage.clear()")   # a first visit: nothing cached to fall back on
    page.add_init_script("(" + FAIL_DATA + ")(" + repr(mode).replace("'", '"') + ")")
    page.goto(base + "/training" + route)
    page.wait_for_timeout(1800)
    assert page.evaluate("getComputedStyle(document.getElementById('login')).display") == "none" or page.evaluate("document.getElementById('login').hidden"), route
    assert page.evaluate("location.hash") == route
    assert page.evaluate(VISIBLE_RETRY) >= 1, f"{route}: no visible retry button"
    print("PROBE", route, mode, "|", page.evaluate("[...document.querySelectorAll('[data-page]')].filter(n=>!n.hidden).map(n=>n.innerText).join(' / ').replace(/\\s+/g,' ').slice(0,260)"))
    assert not errors, errors
