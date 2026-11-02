"""Real HTTP/UI, synthetic model catalog, no real model API or iOS device claim."""

# ruff: noqa: F811
import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from playwright.sync_api import expect
from test_live_training import live_app, browser  # noqa: F401
from app import model_router as models


@pytest.mark.parametrize("width,height", [(440, 956), (1024, 1366)])
def test_mobile_model_settings_restore(live_app, browser, width, height):
    client, dsn, _ = live_app
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        models.refresh(
            c,
            lambda: dict(
                data=[dict(id="deepseek-flash", name="Synthetic catalog fixture")]
            ),
        )
        c.execute(
            "UPDATE ai_model_catalog SET compatibility=%s",
            (Jsonb(dict(real_api_verified=True, efforts=["off", "high"])),),
        )
    base = str(client.base_url).rstrip("/")
    context = browser.new_context(viewport=dict(width=width, height=height), has_touch=True)
    page = context.new_page()
    # one sign-in for the whole site: sign in on the main app, then the sub-pages never ask again
    page.goto(base + "/training")
    page.locator("#password").fill("test-live-login")
    page.locator("#login button").click()
    page.locator("#login").wait_for(state="hidden")
    page.goto(base + "/training#/model")
    expect(page.locator("#controls")).to_be_visible()
    expect(page.locator("#login")).to_be_hidden()
    page.goto(base + "/training#/progress")
    expect(page.locator("#pv-content")).to_be_visible()
    expect(page.locator("#login")).to_be_hidden()
    page.goto(base + "/training#/model")
    expect(page.locator("#controls")).to_be_visible()
    expect(page.locator("#model-view .subbar h1")).to_have_text("模型与费用")
    expect(page.locator("#used-day")).to_contain_text("今日已用")
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    expect(page.locator("#save")).to_be_disabled()  # nothing changed yet
    expect(page.locator("#dirty-note")).to_have_text("没有未保存的改动")
    assert page.evaluate("getComputedStyle(document.querySelector('#price-refresh')).minHeight") == "44px"
    page.locator("#daily").fill("10.01")
    page.locator("#monthly").fill("50")
    page.locator("#task-budget").fill("1")
    page.locator("summary", has_text="说明与累计试验上限").click()  # the explanations are folded by default
    page.locator("#trial-budget").fill("10")
    page.locator("#max-concurrency").select_option("3")
    expect(page.locator("#dirty-note")).to_have_text("有未保存的改动")
    page.locator("#save").click()
    expect(page.locator("#message")).to_have_text("已保存")
    expect(page.locator("#dirty-note")).to_have_text("没有未保存的改动")
    page.reload()
    expect(page.locator("#daily")).to_have_value("10.01")
    expect(page.locator("#max-concurrency")).to_have_value("3")  # the user's value survives a refresh
    page.locator("summary", has_text="说明与累计试验上限").click()
    expect(page.locator("#trial-active")).to_be_checked()
    for n in ("4", "1"):
        page.locator("#max-concurrency").select_option(n)
        page.locator("#save").click()
        expect(page.locator("#dirty-note")).to_have_text("没有未保存的改动")
        page.reload()
        expect(page.locator("#max-concurrency")).to_have_value(n)
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    expect(page.locator("#effort option")).to_have_count(2)
    page.locator("#effort").select_option("off")
    page.locator("#save").click()
    expect(page.locator("#message")).to_have_text("已保存")
    page.reload()
    expect(page.locator("#effort")).to_have_value("off")
    # the danger zone asks before it acts: declining changes nothing (a new learner starts stopped)
    expect(page.locator("#stop-toggle")).to_have_text("恢复 AI 任务")
    page.once("dialog", lambda d: d.dismiss())
    page.locator("#stop-toggle").click()
    expect(page.locator("#stop-toggle")).to_have_text("恢复 AI 任务")
    page.once("dialog", lambda d: d.accept())
    page.locator("#stop-toggle").click()
    expect(page.locator("#stop-toggle")).to_have_text("停止 AI 任务")
    page.once("dialog", lambda d: d.accept())
    page.locator("#stop-toggle").click()
    expect(page.locator("#stop-toggle")).to_have_text("恢复 AI 任务")
    page.once("dialog", lambda d: d.accept())
    page.locator("#default").click()
    expect(page.locator("#effort")).to_have_value("high")
    expect(page.locator("#max-concurrency")).to_have_value("1")  # defaults do not touch the saved limits
    page.close()
