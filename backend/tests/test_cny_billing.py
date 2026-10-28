"""CNY deterministic/real PostgreSQL tests. All model responses and rates are synthetic."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app import cny_billing as b, model_router as m, ai_jobs as jobs
from app.common import ensure_user
from cny_fixtures import price
from test_ai_automation import setup_ai  # noqa: F401
from test_live_training import live_app  # noqa: F401, F811
from migrate import apply_migrations, SQL_DIR


USAGE = dict(input_tokens=1000, output_tokens=200, cache_read_input_tokens=3000)


def test_decimal_and_separate_cache_rates():
    p = price()
    at = datetime(2026, 10, 27, 10, tzinfo=b.SHANGHAI)
    out = b.estimate(p, USAGE, at, at)
    assert Decimal(out["amount_cny"]) == Decimal("0.00372")
    assert out["invoice_confirmed"] is False
    assert sum(b.money("0.00000001") for _ in range(10000)) == Decimal("0.00010000")


@pytest.mark.parametrize(
    "usage,expected",
    [
        (
            dict(input_tokens=0, output_tokens=0, cache_read_input_tokens=1000000),
            "0.04",
        ),
        (dict(input_tokens=1000000, output_tokens=0, cache_read_input_tokens=0), "2"),
        (dict(input_tokens=0, output_tokens=1000000, cache_read_input_tokens=0), "8"),
        (
            dict(
                input_tokens=1,
                output_tokens=0,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=9,
            ),
            "0.00002",
        ),
    ],
)
def test_token_categories_exact(usage, expected):
    at = datetime(2026, 10, 27, 10, tzinfo=b.SHANGHAI)
    assert Decimal(b.estimate(price(), usage, at, at)["amount_cny"]) == Decimal(
        expected
    )


@pytest.mark.parametrize(
    "day,hour,expected",
    [
        ("2026-10-27", 9, "peak"),
        ("2026-10-27", 12, "off_peak"),
        ("2026-10-27", 14, "peak"),
        ("2026-10-27", 18, "off_peak"),
        ("2026-10-01", 10, "off_peak"),  # National holiday weekday
        (
            "2026-10-10",
            10,
            "off_peak",
        ),  # make-up Saturday: official pricing still says weekends off
        ("2026-02-20", 10, "off_peak"),
        ("2026-10-25", 10, "off_peak"),
    ],
)
def test_chinese_calendar_period_and_makeup_weekend(day, hour, expected):
    at = datetime.fromisoformat(day).replace(hour=hour, tzinfo=b.SHANGHAI)
    assert b.period(at, b.calendar_2026()) == expected


def test_unknown_calendar_and_cross_period_bound():
    at = datetime(2027, 1, 4, 11, 59, tzinfo=b.SHANGHAI)
    assert b.period(at, b.calendar_2026()) == "unknown"
    out = b.estimate(price(), USAGE, at, at + timedelta(minutes=2))
    assert out["amount_cny"] == "0.00372" and "upper_bound" in out["settlement_rule"]
    assert out["period_start"] == "unknown"


@pytest.mark.parametrize(
    "usage",
    [
        {},
        dict(input_tokens=5, output_tokens=1),
        dict(input_tokens=-1, output_tokens=1, cache_read_input_tokens=0),
        dict(input_tokens=1.5, output_tokens=1, cache_read_input_tokens=0),
    ],
)
def test_missing_provider_usage_not_fabricated(usage):
    at = b.utcnow()
    assert b.estimate(price(), usage, at, at)["status"] == "unknown"


@pytest.mark.parametrize(
    "mode,at,expected",
    [
        ("server", "2026-10-31T23:59:59+08:00", "0"),
        ("server", "2026-11-01T00:00:00+08:00", "0.01"),
        ("direct", "2026-10-27T00:00:00+08:00", "0.01"),
    ],
)
def test_search_fetch_effective_policy(mode, at, expected):
    p = price()
    p["rules"].update(
        server_tool_effective_at="2026-11-01T00:00:00+08:00",
        search_per_success="0.005",
        fetch_per_success="0.005",
    )
    for tool in ("search", "fetch"):
        assert b.tool_cost(tool, 2, datetime.fromisoformat(at), mode, p) == Decimal(
            expected
        )
    with pytest.raises(ValueError):
        b.tool_cost("search", 1, b.utcnow())


def test_price_conflict_and_immutability(db):
    p = price()
    b.install_price(db, p)
    changed = price(version="conflicting-price")
    changed["rules"]["peak"]["output"] = "9"
    b.install_price(db, changed)
    with pytest.raises(ValueError, match="conflicting"):
        b.current_price(db, "deepseek-flash")
    with pytest.raises(psycopg.Error), db.transaction():
        db.execute("UPDATE ai_cny_prices SET rules='{}'")


def test_expired_unknown_and_new_model_prices(db):
    with pytest.raises(ValueError, match="verified_cny"):
        b.current_price(db, "unknown-model")
    p = price()
    p["valid_until"] = (b.utcnow() - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        b.install_price(db, p)
    p = price("different-model")
    p["rules"]["peak"]["output"] = "27"
    b.install_price(db, p)
    assert b.reserve_bound(b.current_price(db, "different-model"), 1000) == Decimal(
        "0.091"
    )


@pytest.mark.parametrize("limit", ["daily_cny", "monthly_cny", "task_cny", "trial_cny"])
def test_each_budget_blocks_before_request(db, setup_ai, limit):
    m.configure(db, setup_ai, "deepseek-flash", "high", **{limit: Decimal("0.000001")})
    jobs.enqueue(db, setup_ai, "bounded-" + limit, "compatibility", {})
    assert jobs.claim(db) is None
    assert (
        db.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"] == 0
    )


def test_unknown_timeout_and_retry_reservations_immutable(db, setup_ai):
    job = jobs.enqueue(db, setup_ai, "timeout", "science_review", {})
    assert (
        jobs.enqueue(db, setup_ai, "timeout", "science_review", {})["id"] == job["id"]
    )
    first = jobs.claim(db)
    jobs.finish(db, first, error=TimeoutError())
    db.execute("UPDATE ai_jobs SET next_retry=clock_timestamp()-interval '1 minute'")
    second = jobs.claim(db)
    assert second["reserved_cny"] == first["reserved_cny"] * 2
    report = b.summary(db, setup_ai)
    assert (
        report["usage_estimate_cny"]["unknown"] == 1
        and Decimal(report["unknown_reserved_cny"]) == first["reserved_cny"] * 2
    )
    assert report["confirmed_invoice_cny"] is None
    with pytest.raises(psycopg.Error), db.transaction():
        db.execute("DELETE FROM ai_cny_reservations")


def test_balance_insufficient_does_not_retry_or_release(db, setup_ai):
    jobs.enqueue(db, setup_ai, "balance", "science_review", {})
    j = jobs.claim(db)
    assert jobs.finish(db, j, error=jobs.ProviderFailure(402)) == "blocked"
    assert b.totals(db, setup_ai)["trial"] == j["reserved_cny"]
    assert (
        db.execute("SELECT error_code FROM ai_jobs").fetchone()["error_code"]
        == "provider_http_402"
    )


def test_usage_estimate_and_verified_bill_differ(db, setup_ai, monkeypatch):
    jobs.enqueue(db, setup_ai, "estimated", "science_review", {})
    j = jobs.claim(db)
    at = datetime(2026, 10, 27, 10, tzinfo=b.SHANGHAI)
    db.execute("UPDATE ai_jobs SET started_at=%s WHERE id=%s", (at, j["id"]))
    monkeypatch.setattr(b, "utcnow", lambda: at + timedelta(seconds=10))
    jobs.finish(db, j, result=dict(usage=USAGE))
    assert db.execute("SELECT estimated_cny FROM ai_jobs").fetchone()[
        "estimated_cny"
    ] == Decimal("0.00372")
    b.record_invoice(db, j["id"], j["attempts"], "0.0035", "a" * 64)
    report = b.summary(db, setup_ai)
    assert report["confirmed_invoice_cny"] == "0.0035"
    assert report["events"][0]["kind"] == "bill_confirmation"
    assert report["railway_cost_included"] is False
    with pytest.raises(psycopg.Error), db.transaction():
        db.execute("DELETE FROM ai_cny_events")


def test_legacy_usd_archived_without_conversion(raw_db, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    for path in sorted(SQL_DIR.glob("*.sql")):
        if path.name.startswith("018"):
            continue
        (old / path.name).write_bytes(path.read_bytes())
    apply_migrations(raw_db, old)
    uid = ensure_user(raw_db, "old-dollar-owner")
    raw_db.execute(
        "INSERT INTO ai_settings(user_id,daily_usd,monthly_usd,stopped) VALUES(%s,3,12,false)",
        (uid,),
    )
    raw_db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload,reserved_usd,actual_usd) VALUES('old',%s,'science_review','{}','{}',0.12,0.11)",
        (uid,),
    )
    apply_migrations(raw_db)
    row = raw_db.execute("SELECT * FROM ai_settings").fetchone()
    assert (
        row["daily_usd"] == 3
        and row["monthly_usd"] == 12
        and row["daily_cny"] == 0
        and row["stopped"]
    )
    job = raw_db.execute("SELECT * FROM ai_jobs").fetchone()
    assert (
        job["actual_usd"] == Decimal("0.11")
        and job["estimated_cny"] is None
        and job["reserved_cny"] == 0
    )
    with pytest.raises(psycopg.Error), raw_db.transaction():
        raw_db.execute("UPDATE ai_settings SET daily_usd=4")


def test_cny_api_decimal_currency_and_no_usd(client, db, monkeypatch):
    from app import model_api

    @contextmanager
    def connection():
        yield db

    monkeypatch.setattr(model_api, "get_db", connection)
    headers = {"x-api-key": "learner-test-key"}
    result = client.post(
        "/v2/model/limits",
        headers=headers,
        json=dict(
            daily_cny="10.01",
            monthly_cny="50",
            task_cny="1",
            trial_cny="10",
            stopped=True,
        ),
    )
    assert result.status_code == 200 and Decimal(
        result.json()["settings"]["daily_cny"]
    ) == Decimal("10.01")
    assert "daily_usd" not in result.json()["settings"]
    bad = client.post(
        "/v2/model/limits", headers=headers, json=dict(daily_usd=10, monthly_usd=50)
    )
    assert bad.status_code == 422
    bill = client.get("/v2/model/billing", headers=headers)
    assert bill.status_code == 200 and bill.json()["currency"] == "CNY"
    assert client.get("/v2/model/billing").status_code == 401
    assert (
        client.get(
            "/v2/model/billing/history?task_type=science_review", headers=headers
        ).json()
        == []
    )
    assert (
        client.get("/v2/model/billing/history?limit=101", headers=headers).status_code
        == 422
    )
    assert (
        client.get(
            "/v2/model/billing/history?task_type=arbitrary", headers=headers
        ).status_code
        == 422
    )


def test_concurrent_cny_reservation_one_winner(live_app):  # noqa: F811
    _, dsn, _ = live_app
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        uid = setup_ai.__wrapped__(c)
        m.configure(
            c, uid, "deepseek-flash", "high", daily_cny="0.10", max_concurrency=4
        )
        jobs.enqueue(c, uid, "parallel1", "science_review", {})
        jobs.enqueue(c, uid, "parallel2", "science_review", {})

    def claim(_):
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            return jobs.claim(c)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(r is not None for r in results) == 1
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        assert b.totals(c, uid)["day"] <= Decimal("0.10")
        assert (
            c.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"]
            == 1
        )


def test_stop_switch_and_price_change_freezes_queued_snapshot(db, setup_ai):
    original = jobs.enqueue(db, setup_ai, "frozen-price", "science_review", {})
    p = price(version="price-new")
    p["rules"]["peak"]["output"] = "27"
    b.install_price(db, p)
    assert jobs.claim(db) is None
    assert (
        original["settings_snapshot"]["price_snapshot"]["rules"]["peak"]["output"]
        == "8"
    )
    m.configure(db, setup_ai, "deepseek-flash", "high", stopped=True)
    assert m.settings(db, setup_ai)["stopped"] is True


def test_usage_over_bound_circuit_breaker(db, setup_ai):
    jobs.enqueue(db, setup_ai, "huge", "science_review", {})
    j = jobs.claim(db)
    jobs.finish(db, j, result=dict(usage={**USAGE, "output_tokens": 100000000}))
    assert m.settings(db, setup_ai)["stopped"] is True
    assert b.summary(db, setup_ai)["events"][0]["kind"] == "unknown"


def test_public_price_parser_cny_and_layout_fail_closed():
    from app.cny_prices import parse

    cells = [
        ["模型", "deepseek-flash", "deepseek-another"],
        ["模型版本", "version-A", "version-B"],
        ["价格", "缓存命中", "空闲时段", "0.02元", "0.15元"],
        ["高峰时段", "0.04元", "0.30元"],
        ["缓存未命中", "空闲时段", "1元", "4.5元"],
        ["高峰时段", "2元", "9元"],
        ["百万tokens输出", "空闲时段", "4元", "13.5元"],
        ["高峰时段", "8元", "27元"],
        ["Search", "每 1,000 次成功请求", "5元"],
        ["Fetch", "每 1,000 次成功请求", "5元"],
    ]
    html = (
        "<table>"
        + "".join(
            "<tr>" + "".join("<td>" + v + "</td>" for v in row) + "</tr>"
            for row in cells
        )
        + "</table>"
    )
    html += "2026 年 11 月 1 日 00:00 北京时间周一至周五（不含中国法定节假日）9:00 - 12:00、14:00 - 18:00"
    prices = parse(html)
    assert (
        prices[0]["currency"] == "CNY" and prices[1]["rules"]["peak"]["output"] == "27"
    )
    assert prices[0]["official_effective_at"] is None
    with pytest.raises(ValueError):
        parse(html.replace("0.02元", "0.02 USD"))
    with pytest.raises(ValueError):
        parse(html.replace("每 1,000 次成功请求", "每次请求"))


def test_offpeak_usage_estimate_and_cross_boundary():
    p = price()
    start = datetime(2026, 10, 27, 13, tzinfo=b.SHANGHAI)
    out = b.estimate(p, USAGE, start, start + timedelta(seconds=100))
    assert Decimal(out["amount_cny"]) == Decimal("0.00186")
    assert out["settlement_rule"] == "single_known_band_usage_estimate"
    start = start.replace(hour=11, minute=59, second=59)
    out = b.estimate(p, USAGE, start, start + timedelta(seconds=2))
    assert (
        Decimal(out["amount_cny"]) == Decimal("0.00372")
        and out["period_end"] == "off_peak"
    )


def test_beijing_day_month_boundaries(db, setup_ai):
    j = jobs.enqueue(db, setup_ai, "boundaries", "science_review", {})
    now = db.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    local = now.astimezone(b.SHANGHAI)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    for attempt, when, amount in [
        (1, now, "0.1"),
        (2, day - timedelta(seconds=1), "0.2"),
        (3, month - timedelta(seconds=1), "0.3"),
    ]:
        db.execute(
            "INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot,created_at) VALUES(%s,%s,%s,%s,%s)",
            (j["id"], attempt, amount, Jsonb(price()), when),
        )
    totals = b.totals(db, setup_ai)
    assert totals["day"] == Decimal("0.1")
    assert totals["month"] == (Decimal("0.1") if local.day == 1 else Decimal("0.3"))
    assert totals["trial"] == Decimal("0.6")


def test_manual_switch_uses_new_model_cny_not_flash_price(db, setup_ai):
    original = jobs.enqueue(db, setup_ai, "old-model", "science_review", {})
    row = db.execute("SELECT * FROM ai_model_catalog").fetchone()
    db.execute(
        "INSERT INTO ai_model_catalog(model_id,metadata,compatibility) VALUES('synthetic-pro','{}',%s)",
        (Jsonb(row["compatibility"]),),
    )
    pro = price("synthetic-pro")
    pro["rules"]["peak"]["output"] = "27"
    b.install_price(db, pro)
    m.configure(db, setup_ai, "synthetic-pro", "high")
    new = jobs.enqueue(db, setup_ai, "new-model", "science_review", {})
    assert (
        original["settings_snapshot"]["price_snapshot"]["rules"]["peak"]["output"]
        == "8"
    )
    assert new["settings_snapshot"]["price_snapshot"]["rules"]["peak"]["output"] == "27"


def test_upgrade_does_not_use_unknown_model_price(db, setup_ai):
    from app.model_compatibility import auto_upgrade

    row = db.execute("SELECT * FROM ai_model_catalog").fetchone()
    cap = {**row["compatibility"], "quality_benchmark_passed": True}
    db.execute(
        "INSERT INTO ai_model_catalog(model_id,metadata,compatibility) VALUES('unknown-price-new','{}',%s)",
        (Jsonb(cap),),
    )
    m.configure(db, setup_ai, "deepseek-flash", "high", auto_upgrade=True)
    assert auto_upgrade(db, setup_ai) is None


def test_lease_recovery_records_unknown_and_stop_blocks_new_calls(db, setup_ai):
    jobs.enqueue(db, setup_ai, "expired", "science_review", {})
    first = jobs.claim(db)
    db.execute("UPDATE ai_jobs SET lease_until=clock_timestamp()-interval '1 second'")
    m.configure(db, setup_ai, "deepseek-flash", "high", stopped=True)
    assert jobs.claim(db) is None
    event = db.execute("SELECT * FROM ai_cny_events WHERE kind='unknown'").fetchone()
    assert event["attempt"] == first["attempts"]
    assert b.totals(db, setup_ai)["trial"] == first["reserved_cny"]


def test_finish_trial_requires_explicit_setting_daily_stays_enforced(db, setup_ai):
    m.configure(db, setup_ai, "deepseek-flash", "high", trial_cny=0, trial_active=False)
    jobs.enqueue(db, setup_ai, "post-trial", "science_review", {})
    assert jobs.claim(db) is not None
    m.configure(db, setup_ai, "deepseek-flash", "high", daily_cny=0)
    jobs.enqueue(db, setup_ai, "post-trial-blocked", "science_review", {})
    assert jobs.claim(db) is None


def test_cancelled_job_retains_reservation_and_has_separate_state(
    client, db, setup_ai, monkeypatch
):
    from app import model_api

    @contextmanager
    def connection():
        yield db

    monkeypatch.setattr(model_api, "get_db", connection)
    # Use authenticated owner's ID rather than an unrelated synthetic owner.
    uid = ensure_user(db, "test-owner")
    m.settings(db, uid)
    m.configure(
        db,
        uid,
        "deepseek-flash",
        "high",
        daily_cny=1,
        monthly_cny=5,
        task_cny=1,
        trial_cny=5,
        stopped=False,
    )
    j = jobs.enqueue(db, uid, "cancel", "science_review", {})
    claim = jobs.claim(db)
    response = client.post(
        f"/v2/model/jobs/{j['id']}/cancel",
        headers={"x-api-key": "learner-test-key"},
        json={},
    )
    assert response.status_code == 200
    assert (
        db.execute("SELECT state FROM ai_jobs WHERE id=%s", (j["id"],)).fetchone()[
            "state"
        ]
        == "cancelled"
    )
    assert b.totals(db, uid)["trial"] == claim["reserved_cny"]
    with pytest.raises(ValueError, match="stale_lease"):
        jobs.finish(db, claim, result={})


def test_price_conflict_survives_old_observation_expiry(db):
    at = b.utcnow()
    first = price(version="old-rate")
    b.install_price(db, first)
    changed = price(version="new-rate")
    changed["rules"]["peak"]["output"] = "7"
    new_id = b.install_price(db, changed)
    # Even with no active overlap later, conflicting historical observations need explicit evidence.
    with pytest.raises(ValueError, match="conflicting"):
        b.current_price(db, "deepseek-flash", at + timedelta(hours=1))
    b.resolve_price_conflict(
        db,
        new_id,
        "a" * 64,
        "Synthetic billing-policy confirmation for this test; not an actual official document validation.",
    )
    assert b.current_price(db, "deepseek-flash")["rules"]["peak"]["output"] == "7"
    rollback = price(version="cached-old-page-observed-later")
    b.install_price(db, rollback)
    with pytest.raises(ValueError, match="conflicting"):
        b.current_price(db, "deepseek-flash")
    # Separate model has clean version chain: HTML-only update does not fabricate a rate conflict.
    other = price("synthetic-stable", version="stable1")
    b.install_price(db, other)
    other = price("synthetic-stable", version="stable2")
    b.install_price(db, other)
    assert b.current_price(db, "synthetic-stable")["version"] == "stable2"


def test_price_refresh_reprices_attempt_without_changing_model_snapshot(db, setup_ai):
    original = jobs.enqueue(db, setup_ai, "price-refresh-safe", "science_review", {})
    refreshed = price(version="same-rates-refreshed")
    b.install_price(db, refreshed)
    claimed = jobs.claim(db)
    assert (
        claimed["id"] == original["id"]
        and claimed["settings_snapshot"] == original["settings_snapshot"]
    )
    reserved = db.execute("SELECT price_snapshot FROM ai_cny_reservations").fetchone()[
        "price_snapshot"
    ]
    assert reserved["version"] == "same-rates-refreshed"
    assert (
        claimed["settings_snapshot"]["price_snapshot"]["version"]
        != "same-rates-refreshed"
    )
