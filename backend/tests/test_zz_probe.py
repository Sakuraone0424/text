import pytest
from test_error_states_ui import *  # noqa
from test_error_states_ui import ROUTES, FAIL_DATA

@pytest.mark.parametrize("route", ROUTES)
def test_probe(engine, live_app, route):
    client, dsn, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, path="/training")
    page.add_init_script("(" + FAIL_DATA + ")(\"server_error\")")
    page.goto(base + "/training" + route)
    page.wait_for_timeout(1800)
    print("PROBE", route, "|", page.evaluate("[...document.querySelectorAll('[data-page]')].filter(n=>!n.hidden).map(n=>n.innerText).join(' / ').replace(/\\s+/g,' ').slice(0,300)"), "| login hidden:", page.evaluate("document.getElementById('login').hidden"))
