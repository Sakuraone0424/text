"""Actual HTTP, browser and multi-connection tests using a disposable test-only schema.

Only this uniquely created schema is committed and removed; public is never used.
"""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest


@pytest.fixture
def live_app(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit development TEST_DATABASE_URL required")
    from migrate import apply_migrations
    import conftest as fixtures

    schema = "quiz_live_test_" + uuid.uuid4().hex
    process = None
    with psycopg.connect(url, row_factory=dict_row) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = make_conninfo(url, options=f"-csearch_path={schema}")
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            apply_migrations(conn)
            factory = fixtures.question_factory.__wrapped__(conn)
            blueprint = fixtures.blueprint.__wrapped__()
            fixtures.bank.__wrapped__(
                conn, factory, fixtures.passing_audit.__wrapped__(), blueprint
            )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            **os.environ,
            "DATABASE_URL": dsn,
            "QUIZ_USERNAME": "test-owner",
            "APP_PASSWORD": "test-live-login",
            "SESSION_SECRET": uuid.uuid4().hex,
            "QUIZ_API_KEY": "learner-live",
            "QUIZ_REVIEW_API_KEY": "operator-live",
            "NOTION_SYNC_ENABLED": "0",
            "QUIZ_WORKER_ENABLED": "1",
        }
        with (tmp_path / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                stdout=log,
                stderr=log,
            )
            with httpx.Client(
                base_url=f"http://localhost:{port}",
                trust_env=False,
                timeout=10,
                headers={"x-api-key": "learner-live"},
            ) as client:
                for _ in range(100):
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None:
                        raise RuntimeError("Test server stopped during startup")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Test server readiness timeout")

                def restart_server():
                    nonlocal process
                    process.terminate()
                    process.wait(timeout=15)
                    process = subprocess.Popen(
                        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                        cwd=Path(__file__).resolve().parents[1],
                        env=env, stdout=log, stderr=log,
                    )
                    for _ in range(100):
                        try:
                            if client.get("/health").status_code == 200:
                                return
                        except httpx.HTTPError:
                            pass
                        if process.poll() is not None:
                            raise RuntimeError("Test server stopped during restart")
                        time.sleep(0.1)
                    raise RuntimeError("Test server restart timeout")

                client.restart_for_test = restart_server
                yield client, dsn, blueprint
    finally:
        if process:
            process.terminate()
            process.wait(timeout=15)
        # This identifier was generated here and contains no production or user tables.
        with psycopg.connect(url) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def begin(client, blueprint):
    response = client.post("/v2/papers", json=blueprint.model_dump())
    assert response.status_code == 200, response.text
    pid = response.json()["paper_id"]
    return client.post(f"/v2/papers/{pid}/start").json()


def test_live_restart_preserves_draft_deadline_submission_and_learning(live_app):
    client, dsn, blueprint = live_app
    paper = begin(client, blueprint)
    pid = paper["paper_id"]
    question = next(q for q in paper["questions"] if q["question_type"] == "single_choice")
    saved = client.put(
        f"/v2/papers/{pid}/drafts/{question['id']}",
        json={"expected_version": 0, "value": {"text": "A"}},
    )
    assert saved.status_code == 200
    client.restart_for_test()
    restored = client.get(f"/v2/papers/{pid}").json()
    assert restored["timing"]["deadline"] == paper["timing"]["deadline"]
    assert restored["timing"]["drafts"][str(question["id"])]["text"] == "A"
    submitted = client.post(f"/v2/papers/{pid}/submit", json={"expected_version": 1, "answers": {}})
    assert submitted.status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        before = conn.execute("SELECT count(*) AS n FROM fsrs_events").fetchone()["n"]
    client.restart_for_test()
    repeated = client.post(f"/v2/papers/{pid}/submit", json={"expected_version": 1, "answers": {}})
    assert repeated.status_code == 200 and repeated.json()["attempt_id"] == submitted.json()["attempt_id"]
    assert client.get(f"/v2/papers/{pid}/result").status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM attempts_v2").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM fsrs_events").fetchone()["n"] == before


def test_live_concurrent_saves_and_duplicate_submit(live_app):
    client, dsn, blueprint = live_app
    paper = begin(client, blueprint)
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]

    def save(value):
        return client.put(
            f"/v2/papers/{pid}/drafts/{qid}",
            json={"expected_version": 0, "value": {"text": value}},
        )

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(save, ["A", "B"]))
    assert sorted(r.status_code for r in responses) == [200, 409]

    def submit(_):
        return client.post(f"/v2/papers/{pid}/submit", json={"expected_version": 1})

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(submit, [0, 1]))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json()["attempt_id"] == responses[1].json()["attempt_id"]
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT COUNT(*) FROM attempts_v2").fetchone()[0] == 1


def test_live_worker_submits_without_client_request(live_app):
    client, dsn, blueprint = live_app
    paper = begin(client, blueprint)
    pid = paper["paper_id"]
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE training_sessions SET started_at=CURRENT_TIMESTAMP-INTERVAL '2 hours',deadline=CURRENT_TIMESTAMP-INTERVAL '1 hour'"
        )
    # Query persistence directly: no request path is allowed to perform the expiry for this test.
    for _ in range(60):
        with psycopg.connect(dsn) as conn:
            submitted = conn.execute("SELECT COUNT(*) FROM attempts_v2").fetchone()[0]
        if submitted:
            break
        time.sleep(0.1)
    assert submitted == 1
    assert client.get(f"/v2/papers/{pid}/result").json()["score"] == 0


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = os.environ.get("QUIZ_BROWSER_EXECUTABLE") or shutil.which(
            "chromium"
        )
        browser = playwright.chromium.launch(
            headless=True, **({"executable_path": executable} if executable else {})
        )
        yield browser
        browser.close()


def login(page, base):
    page.goto(base + "/training")
    page.locator("#password").fill("test-live-login")
    page.locator("#login button").click()
    page.locator("#login").wait_for(state="hidden")
    page.locator("#prepare").click()
    page.locator("#start").wait_for(state="visible")


def open_question(page, question_id):
    """The redesigned UI keeps all drafts mounted; navigation only changes visibility."""
    page.locator("#question-map").click()
    page.locator(f'#question-grid button[data-question-id="{question_id}"]').click()


def confirm_submission(page):
    page.locator("#submit").click()
    page.locator("#confirm-submit").click()


def test_iphone_ipad_restore_upload_and_full_150_point_e2e(live_app, browser):
    from test_timed_training import image_bytes

    client, dsn, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    phone = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3,
    )
    page = phone.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, base)
    assert page.locator('a[href="/"]').count() == 0
    assert page.locator("details").count() == 0
    assert page.locator("article").count() == 0
    page.locator("#start").click()
    page.locator("#questions article:not([hidden])").wait_for()
    pid = client.post(
        "/v2/papers",
        json={"specification": "xiaohan-150-60-v1", "quotas": blueprint.quotas},
    ).json()["paper_id"]
    active = client.get(f"/v2/papers/{pid}").json()
    deadline = active["timing"]["deadline"]
    assert len(active["questions"]) == 28
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    for item in active["questions"]:
        open_question(page, item["id"])
        article = page.locator(f"article[data-id='{item['id']}']")
        if item["question_type"] == "single_choice":
            article.locator(".option").first.click()
        elif item["question_type"] == "fill_blank":
            article.locator("input").first.fill("fixture solution")
        else:
            article.locator("textarea").fill("Actual synthetic subjective explanation")
    page.wait_for_function(
        "Object.keys(JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('quiz:v2:')))).pending).length===0"
    )
    term = next(i for i in active["questions"] if i["question_type"] == "term")
    open_question(page, term["id"])
    article = page.locator(f"article[data-id='{term['id']}']")
    assert article.locator("input[type=file][capture=environment]").count() == 1
    article.locator("input[type=file]").nth(1).set_input_files(
        {"name": "handwriting.png", "mimeType": "image/png", "buffer": image_bytes()}
    )
    article.locator("img").wait_for()
    page.reload()
    page.locator("#prepare").click()
    page.locator("#questions article:not([hidden])").wait_for()
    assert (
        page.locator(f"article[data-id='{term['id']}'] textarea").input_value()
        == "Actual synthetic subjective explanation"
    )
    assert client.get(f"/v2/papers/{pid}").json()["timing"]["deadline"] == deadline
    tablet = browser.new_context(
        viewport={"width": 820, "height": 1180}, is_mobile=True, has_touch=True
    )
    second = tablet.new_page()
    second.goto(base + "/training")
    second.locator("#password").fill("test-live-login")
    second.locator("#login button").click()
    second.locator("#login").wait_for(state="hidden")
    second.locator("#prepare").click()
    second.locator("#questions article:not([hidden])").wait_for()
    assert (
        second.locator(f"article[data-id='{term['id']}'] textarea").input_value()
        == "Actual synthetic subjective explanation"
    )
    assert not second.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.on("dialog", lambda dialog: dialog.accept())
    confirm_submission(page)
    page.locator("#result > h2").wait_for()
    assert page.get_by_text("本卷命题依据（训练优先级，不是考试概率）").count() == 1
    result = client.get(f"/v2/papers/{pid}/result").json()
    assert result["pending"] == 16 and result["graded_points"] == 30
    with psycopg.connect(dsn) as conn:
        image_id = conn.execute("SELECT id FROM answer_images").fetchone()[0]
    auth = {"x-review-api-key": "operator-live"}
    assert (
        client.post(
            f"/v2/images/{image_id}/transcription",
            headers=auth,
            json={
                "text": "Manually confirmed answer",
                "confidence": 1,
                "confirmed": True,
                "reviewer": "expert",
            },
        ).status_code
        == 200
    )
    for item in active["questions"]:
        if item["question_type"] not in ("single_choice", "fill_blank"):
            graded = client.post(
                f"/v2/papers/{pid}/answers/{item['id']}/grade",
                headers=auth,
                json={
                    "criterion_points": [item["max_points"]],
                    "feedback": "Verified all rubric points with source",
                    "reviewer": "expert",
                },
            )
            assert graded.status_code == 200, graded.text
    assert client.get(f"/v2/papers/{pid}/result").json()["score"] == 150
    assert not errors
    phone.close()
    tablet.close()


def test_offline_text_and_photo_survive_reload_and_reconnect(live_app, browser):
    from test_timed_training import image_bytes

    client, dsn, blueprint = live_app
    base = str(client.base_url).rstrip("/")
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    page = context.new_page()
    login(page, base)
    page.locator("#start").click()
    page.locator("#questions article:not([hidden])").wait_for()
    page.evaluate("navigator.serviceWorker.ready")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    qid = page.locator("textarea").first.locator("xpath=..").get_attribute("data-id")
    open_question(page, qid)
    textarea = page.locator(f'article[data-id="{qid}"] textarea')
    context.set_offline(True)
    textarea.fill("Offline answer survives")
    article = page.locator(f"article[data-id='{qid}']")
    article.locator("input[type=file]").nth(1).set_input_files(
        {"name": "offline.png", "mimeType": "image/png", "buffer": image_bytes()}
    )
    page.wait_for_function(
        "document.querySelector('#status').textContent.includes('照片原件仍保留')"
    )
    page.reload()
    page.locator("#questions article:not([hidden])").wait_for()
    assert (
        page.locator(f"article[data-id='{qid}'] textarea").input_value()
        == "Offline answer survives"
    )
    context.set_offline(False)
    article = page.locator(f"article[data-id='{qid}']")
    article.locator("img").wait_for()
    pid = client.post(
        "/v2/papers",
        json={"specification": "xiaohan-150-60-v1", "quotas": blueprint.quotas},
    ).json()["paper_id"]
    saved = client.get(f"/v2/papers/{pid}").json()["timing"]["drafts"][qid]
    assert saved["text"] == "Offline answer survives" and len(saved["image_ids"]) == 1
    context.close()
