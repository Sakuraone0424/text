"""A failed price refresh says why (a fixed code), is remembered, and shows on the progress page before the prices run out."""
import httpx
import pytest

from app import cny_prices, progress
from test_ai_automation import setup_ai  # noqa: F401


@pytest.mark.parametrize("exc,code", [
    (ValueError("official_price_layout_changed"), "official_price_layout_changed"),
    (ValueError("some free text that is not a code!"), "ValueError"),
    (httpx.ConnectError("x"), "network_ConnectError"),
    (httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://x"), response=httpx.Response(403)), "http_403"),
])
def test_the_failure_is_reported_as_a_fixed_code(exc, code):
    assert cny_prices.failure_code(exc) == code


def test_failures_are_counted_and_a_success_clears_them(db):
    cny_prices.remember_failure(db, "official_price_layout_changed")
    cny_prices.remember_failure(db, "official_price_layout_changed")
    state = cny_prices.status(db)
    assert state["ok"] is False and state["failures"] == 2 and state["code"] == "official_price_layout_changed"
    cny_prices.remember_success(db)
    state = cny_prices.status(db)
    assert state["ok"] is True and state["failures"] == 0 and state["last_ok_at"]


def test_the_progress_report_carries_the_price_status(db, setup_ai):
    cny_prices.remember_failure(db, "http_403")
    prices = progress.report(db, setup_ai)["prices"]
    assert prices["ok"] is False and prices["code"] == "http_403" and prices["failures"] == 1
    assert "hours_left" in prices and "blocking" in prices


def test_no_valid_price_in_force_is_blocking(db):
    db.execute("SELECT 1")                                    # an empty price table: nothing in force
    state = cny_prices.status(db)
    assert state["valid_until"] is None and state["blocking"] is True


def test_the_page_says_it(tmp_path):
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "progress.js").read_text()
    for text in ("价格刷新失败", "价格已过期，出题已被暂停", "official_price_layout_changed", "price-note"):
        assert text in js or text in (Path(__file__).resolve().parents[1] / "app" / "static" / "training.html").read_text(), text
