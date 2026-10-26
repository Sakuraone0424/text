"""Real HTTP/browser tests on synthetic data in a uniquely created nonproduction schema."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4
import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from app import candidate_review, review_app
from migrate import apply_migrations
from test_candidate_review import packet


@pytest.fixture
def live_review(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit synthetic test database required")
    schema = "source_staging_mobile_" + uuid4().hex
    process = None
    with psycopg.connect(url, row_factory=dict_row) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        name = c.execute("SELECT current_database() AS name").fetchone()["name"]
        c.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        apply_migrations(c)
        content = packet.__wrapped__(c, tmp_path)
        candidate_review.create_packet(c, content)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "SOURCE_STAGING_DATABASE_URL": url,
        "CANDIDATE_REVIEW_SCHEMA": schema,
        "CANDIDATE_REVIEW_DATABASE_NAME": name,
        "CANDIDATE_SESSION_SECRET": uuid4().hex,
        "CANDIDATE_REVIEW_USERS": json.dumps(
            {"synthetic-reviewer": review_app.password_hash("synthetic-only-password")}
        ),
        "CANDIDATE_COOKIE_SECURE": "0",
    }
    try:
        with (tmp_path / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.review_app:app",
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
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get(base + "/").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Synthetic review server unavailable")
            yield base, url, schema
    finally:
        if process:
            process.terminate()
            process.wait(timeout=15)
        with psycopg.connect(url) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize("width,height", [(390, 844), (820, 1180)])
def test_touch_reviewer_complete_synthetic_flow(live_review, tmp_path, width, height):
    from playwright.sync_api import sync_playwright,expect

    with sync_playwright() as p:
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        browser = p.chromium.launch(
            headless=True, **({"executable_path": executable} if executable else {})
        )
        page = browser.new_page(
            viewport={"width": width, "height": height}, has_touch=True
        )
        page.goto(live_review[0])
        page.locator("[name=username]").fill("synthetic-reviewer")
        page.locator("[name=password]").fill("synthetic-only-password")
        page.get_by_role("button", name="进入私有审核").click()
        page.locator("#list button").first.click()
        page.get_by_role("heading", name="参考答案", exact=True).wait_for()
        assert page.locator("article").inner_text().find("Synthetic kinase") >= 0
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        for box in page.locator("[data-check]").all():
            box.check()
        page.locator("#attest").check()
        page.locator("#note").fill(
            "Synthetic browser reviewer action, only on synthetic rollback-test data"
        )
        page.get_by_role("button", name="通过候选审核", exact=True).click()
        page.get_by_text("已保存人工记录；题目仍未发布。", exact=True).wait_for()
        expect(page.locator('#summary')).to_contain_text('人工候选通过 1')
        assert "正式合格 0" in page.locator("#summary").inner_text()
        page.screenshot(
            path=str(tmp_path / f"synthetic-review-{width}.png"), full_page=True
        )
        browser.close()
    with psycopg.connect(live_review[1], row_factory=dict_row) as c:
        c.execute(
            sql.SQL("SET LOCAL search_path TO {}").format(
                sql.Identifier(live_review[2])
            )
        )
        assert (
            c.execute("SELECT count(*) AS n FROM candidate_human_decisions").fetchone()[
                "n"
            ]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM reviewed_questions WHERE status='published'"
            ).fetchone()["n"]
            == 0
        )
        assert (
            c.execute("SELECT count(*) AS n FROM source_review_events").fetchone()["n"]
            == 0
        )
