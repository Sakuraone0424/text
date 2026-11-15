import os
from pathlib import Path
from test_motion_ui import FULL, start, touch
from test_live_training import live_app  # noqa: F401
from test_ui_screens import engine  # noqa: F401

SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))


def test_shots(engine, live_app):
    if engine[0] != "chromium":
        return
    for scheme in ("light", "dark"):
        page, _ = start(engine, live_app, scheme, routes={"/v2/stats/overview": [FULL, 4000], "/v2/papers": [None, 4000]})
        page.wait_for_selector('#home-view[data-loading="1"]')
        page.wait_for_timeout(250)
        page.screenshot(path=str(SHOTS / f"motion-loading-{scheme}.png"))
        page.close()
        page, _ = start(engine, live_app, scheme, routes={"/v2/stats/overview": FULL})
        page.wait_for_function("!document.querySelector('[data-rolling]')")
        page.evaluate("window.__routes['/v2/stats/overview'] = [window.__routes['/v2/stats/overview'], 3000]")
        touch(page, "touchStart", 200)
        for dy in range(10, 130, 10):
            touch(page, "touchMove", 200 + dy)
        page.wait_for_timeout(120)
        page.screenshot(path=str(SHOTS / f"motion-pull-{scheme}.png"))
        touch(page, "touchEnd", 330)
        page.wait_for_timeout(700)
        page.screenshot(path=str(SHOTS / f"motion-refreshing-{scheme}.png"))
        page.wait_for_timeout(3000)
        page.evaluate("document.getElementById('question-dialog').showModal()")
        page.wait_for_timeout(450)
        page.screenshot(path=str(SHOTS / f"motion-sheet-{scheme}.png"))
        page.evaluate("document.getElementById('question-dialog').close()")
        page.wait_for_timeout(300)
        page.evaluate("Motion.done()")
        page.wait_for_timeout(260)
        page.screenshot(path=str(SHOTS / f"motion-done-{scheme}.png"))
        page.close()
