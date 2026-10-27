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
    page = browser.new_page(viewport=dict(width=width, height=height), has_touch=True)
    page.goto(str(client.base_url).rstrip("/") + "/static/model.html")
    page.locator("#password").fill("test-live-login")
    page.locator("#login button").click()
    expect(page.locator("#controls")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    assert page.locator("#effort option").count() == 2
    page.locator("#effort").select_option("off")
    page.locator("#save").click()
    expect(page.locator("#message")).to_contain_text("/ off")
    page.reload()
    expect(page.locator("#effort")).to_have_value("off")
    page.locator("#default").click()
    expect(page.locator("#effort")).to_have_value("high")
    expect(page.locator("#stopped")).to_be_checked()
    page.close()
