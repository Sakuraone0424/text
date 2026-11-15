"""Run by hand (not collected by the suite): frame times and click-to-paint under 4x CPU throttling.
   python3 -m pytest tests/perf_motion_probe.py -s -q -p no:cacheprovider"""
import statistics

from test_live_training import browser, live_app, login  # noqa: F401
from test_report_ui import PHONE, start_exam  # noqa: F401
from test_ui_screens import engine, open_page  # noqa: F401

FRAME_PROBE = """() => { window.__frames = []; let last = performance.now(); const tick = (t) => { window.__frames.push(t - last); last = t; if (window.__run) requestAnimationFrame(tick); }; window.__run = true; requestAnimationFrame(tick); }"""


def frames(page):
    f = page.evaluate("window.__run = false; window.__frames.slice(1)")
    if not f:
        return "no frames"
    return f"frames={len(f)} median={statistics.median(f):.1f}ms p95={sorted(f)[max(0, int(len(f) * .95) - 1)]:.1f}ms worst={max(f):.0f}ms >20ms={sum(1 for x in f if x > 20)}"


PAINT = """async (code) => { const t0 = performance.now(); eval(code); await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); return performance.now() - t0; }"""


def touch(page, kind, y):
    s = page.__dict__.setdefault("_cdp2", page.context.new_cdp_session(page))
    s.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": 200, "y": y}]})


def test_probe(engine, live_app):
    if engine[0] != "chromium":
        return
    client, _, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    page, _ = open_page(engine, "safari", base)
    page.wait_for_selector('nav.primary-nav [data-view="home"]')
    page.wait_for_timeout(1500)
    page.context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": 4})
    out = {}
    clicks = []
    page.evaluate(FRAME_PROBE)
    for view in ("profile", "mistakes", "home", "profile", "home"):
        clicks.append(page.evaluate(PAINT, f"document.querySelector('nav.primary-nav [data-view=\"{view}\"]').click()"))
        page.wait_for_timeout(350)
    out["tab switch: click -> painted"] = f"median={statistics.median(clicks):.0f}ms worst={max(clicks):.0f}ms"
    out["page changes: frames"] = frames(page)
    page.evaluate(FRAME_PROBE)
    t = page.evaluate(PAINT, "document.getElementById('question-dialog').showModal()")
    page.wait_for_timeout(400)
    out["open answer sheet: click -> painted"] = f"{t:.0f}ms"
    out["open answer sheet: frames"] = frames(page)
    page.evaluate("document.getElementById('question-dialog').close()")
    page.wait_for_timeout(300)
    page.evaluate(FRAME_PROBE)
    t = page.evaluate(PAINT, "location.hash = '#/progress'")
    page.wait_for_timeout(500)
    out["open a sub-page: click -> painted"] = f"{t:.0f}ms"
    out["open a sub-page: frames"] = frames(page)
    page.evaluate("location.hash = '#/home'")
    page.wait_for_timeout(500)
    page.evaluate(FRAME_PROBE)
    touch(page, "touchStart", 200)
    for dy in range(10, 300, 20):
        touch(page, "touchMove", 200 + dy)
    touch(page, "touchEnd", 500)
    page.wait_for_timeout(1500)
    out["pull to refresh: frames"] = frames(page)
    page.close()
    # answering: choosing an option
    page, pid, paper, errors = start_exam(client, blueprint, browser_of(engine), accept_dialogs=True)
    page.context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": 4})
    options = page.locator("#questions article:not([hidden]) .option")
    if options.count():
        times = []
        for i in range(min(4, options.count())):
            times.append(page.evaluate(PAINT, f"document.querySelectorAll('#questions article:not([hidden]) .option')[{i}].click()"))
        out["choose an option: click -> painted"] = f"median={statistics.median(times):.0f}ms worst={max(times):.0f}ms"
    else:
        out["choose an option"] = "(first question is not a single choice)"
    t = page.evaluate(PAINT, "document.getElementById('next-question').click()")
    out["next question: click -> painted"] = f"{t:.0f}ms"
    print()
    for k, v in out.items():
        print("PERF", k, ":", v)


def browser_of(engine):
    class _B:
        def new_context(self, **kw):
            return engine[1].new_context(**kw)
    return _B()
