"""Two price versions in force: the same rates are one price; different rates are settled by the official page; the owner's click is the fallback;
and a price question never stops grading."""
from datetime import timedelta

import pytest

from app import ai_jobs as jobs, cny_billing as b, legacy_reuse
from cny_fixtures import price
from test_ai_automation import setup_ai  # noqa: F401  (fixture)

MODEL = "deepseek-flash"


def refresh_state(db, ok):
    now = b.utcnow().isoformat()
    legacy_reuse._set_state(db, "price_refresh", dict(ok=ok, code=None if ok else "http_503", failures=0 if ok else 1, at=now, last_ok_at=now if ok else None))


def two_versions(db, changed_rates=False, label="DeepSeek-V4-0701"):
    first = b.install_price(db, price(version="first-observation"))
    other = price(version="second-observation")
    other["model_version"] = label                              # the page printed another model-version label
    if changed_rates:
        other["rules"]["peak"]["output"] = "9"
    second = b.install_price(db, other)
    return first, second


def test_the_same_rates_under_another_version_label_are_not_a_conflict(db):
    first, second = two_versions(db)
    got = b.current_price(db, MODEL)                          # no error, no refresh state needed
    assert got["version"] == "second-observation" and got["model_version"] == "DeepSeek-V4-0701"
    assert b.price_conflict(db, MODEL) is None
    assert b.price_state(db, MODEL).startswith("ok:DeepSeek-V4-0701")


def test_the_same_rate_written_two_ways_is_the_same_price():
    assert b.price_key(dict(off_peak=dict(cache_hit="0.02", cache_miss="1.0", output="4"), peak=dict(cache_hit="0.04", cache_miss="2", output="8.00"))) == \
        b.price_key(dict(off_peak=dict(cache_hit="0.020", cache_miss="1", output="4.0"), peak=dict(cache_hit="0.04", cache_miss="2.0", output="8")))


def test_really_different_rates_take_the_newest_page_that_the_parser_passed_and_retire_the_old_one(db):
    first, second = two_versions(db, changed_rates=True)
    refresh_state(db, ok=True)                                # the last refresh read the official Chinese page and it parsed
    got = b.current_price(db, MODEL)
    assert got["rules"]["peak"]["output"] == "9" and got["version"] == "second-observation"
    resolution = db.execute("SELECT * FROM ai_cny_price_resolutions WHERE model_id=%s", (MODEL,)).fetchone()
    assert resolution["price_id"] == second and "旧版本失效" in resolution["basis"]
    assert b.price_conflict(db, MODEL) is None
    again = b.current_price(db, MODEL)                       # decided once, stays decided
    assert again["version"] == "second-observation"
    assert db.execute("SELECT count(*) AS n FROM ai_cny_price_resolutions").fetchone()["n"] == 1


def test_different_rates_with_an_unreadable_official_page_stop_and_say_which_versions(db):
    first, second = two_versions(db, changed_rates=True, label="DeepSeek-V4-0801")
    refresh_state(db, ok=False)
    with pytest.raises(ValueError, match="conflicting_cny_price_versions"):
        b.current_price(db, MODEL)
    shown = b.price_conflict(db, MODEL)
    assert [v["price_id"] for v in shown] == [first, second]
    assert {v["peak"]["output"] for v in shown} == {"8", "9"} and shown[1]["model_version"] == "DeepSeek-V4-0801"
    line = b.price_state(db, MODEL)                           # what the worker-run log prints
    assert line.startswith("conflicting_cny_price_versions:") and "DeepSeek-V4-0801" in line and "out=4/9" in line


def test_the_owners_click_settles_it_and_stands_until_new_rates_appear(db):
    first, second = two_versions(db, changed_rates=True)
    refresh_state(db, ok=False)
    b.confirm_price(db, first)                                # he picked the older version
    assert b.current_price(db, MODEL)["rules"]["peak"]["output"] == "8"
    assert b.price_conflict(db, MODEL) is None
    third = price(version="third-observation")
    third["rules"]["peak"]["output"] = "10"                   # a newer observation with other rates arrives
    b.install_price(db, third)
    with pytest.raises(ValueError, match="conflicting"):
        b.current_price(db, MODEL)
    refresh_state(db, ok=True)                                # and once the official page is read again, the newest wins
    assert b.current_price(db, MODEL)["rules"]["peak"]["output"] == "10"


def test_the_confirm_endpoint_only_accepts_a_version_that_is_in_conflict(client, db, setup_ai, monkeypatch):
    from contextlib import contextmanager

    from app import model_api, model_router as m
    from app.common import ensure_user

    @contextmanager
    def connection():
        yield db

    monkeypatch.setattr(model_api, "get_db", connection)
    m.configure(db, ensure_user(db, "test-owner"), "deepseek-flash", "high", daily_cny=1, monthly_cny=5, task_cny=1, trial_cny=5, stopped=False)
    first, second = two_versions(db, changed_rates=True)
    refresh_state(db, ok=False)
    headers = {"x-api-key": "learner-test-key", "origin": "http://testserver"}
    body = client.get("/v2/model", headers=headers).json()
    assert {first, second} <= {v["price_id"] for v in body["price_conflict"]}
    assert client.post("/v2/model/price-confirm", headers=headers, json={"price_id": 999999}).status_code == 409
    ok = client.post("/v2/model/price-confirm", headers=headers, json={"price_id": second})
    assert ok.status_code == 200, ok.text
    assert client.get("/v2/model", headers=headers).json()["price_conflict"] is None


# ---- a price question never stops grading ---------------------------------------------------------------------------------

def test_grading_is_estimated_at_the_last_verified_price_while_the_price_is_unconfirmed(db, setup_ai):
    first, second = two_versions(db, changed_rates=True)
    refresh_state(db, ok=False)
    job = jobs.enqueue(db, setup_ai, "grade-during-conflict", "grading", {"question": {}, "evidence": []})     # (enqueue itself needs a price too)
    assert job["settings_snapshot"]["price_snapshot"].get("unconfirmed") is True
    claimed = jobs.claim(db)
    assert claimed and claimed["id"] == job["id"]                                      # claimed, not left waiting
    assert claimed["reserved_cny"] > 0
    assert jobs.LAST_CLAIM.get("outcome") != "waiting_for_verified_price"


def test_other_tasks_still_wait_for_a_verified_price(db, setup_ai):
    job = jobs.enqueue(db, setup_ai, "review-before-conflict", "science_review", {})      # queued while the price was still clear
    first, second = two_versions(db, changed_rates=True)
    refresh_state(db, ok=False)
    # the conflict is on now: a writing/review task is not claimed, it waits
    assert jobs.claim(db) is None and jobs.LAST_CLAIM.get("outcome") == "waiting_for_verified_price"
    assert db.execute("SELECT state FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()["state"] in ("pending", "retrying")


def test_an_expired_price_does_not_stop_grading_either(db, setup_ai):
    db.execute("ALTER TABLE ai_cny_prices DISABLE TRIGGER cny_prices_immutable")
    db.execute("UPDATE ai_cny_prices SET valid_from=valid_from-interval '3 days',valid_until=clock_timestamp()-interval '1 hour',verified_at=verified_at-interval '3 days'")
    db.execute("ALTER TABLE ai_cny_prices ENABLE TRIGGER cny_prices_immutable")
    with pytest.raises(ValueError, match="verified_cny_price_required"):
        b.current_price(db, MODEL)
    job = jobs.enqueue(db, setup_ai, "grade-expired-price", "grading", {"question": {}, "evidence": []})
    claimed = jobs.claim(db)
    assert claimed and claimed["id"] == job["id"]


def test_a_stuck_grading_task_is_put_right_even_when_planning_fails_on_the_price(db, setup_ai, monkeypatch):
    from psycopg.types.json import Jsonb

    from app import auto_worker

    payload = dict(instructions="x", evidence=[dict(unit_id=i % 3, locator="p", text=f"段落{i}") for i in range(8)])
    jid = db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code,attempts) VALUES ('g-2722',%s,'grading','retrying','{}',%s,%s,2) RETURNING id",
        (setup_ai, Jsonb(payload), "ValueError:evidence_exceeds_hard_cap")).fetchone()["id"]
    two_versions(db, changed_rates=True)
    refresh_state(db, ok=False)
    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "1")
    report = {}
    auto_worker._plan_queue(db, report, [{"user_id": setup_ai, "stopped": False}], True, False)
    row = db.execute("SELECT state,error_code,payload FROM ai_jobs WHERE id=%s", (jid,)).fetchone()
    assert row["error_code"] is None and len(row["payload"]["evidence"]) == 3          # cut down and started again
    assert report.get("grading_requeue", {}).get("evidence_shrunk") == 1
