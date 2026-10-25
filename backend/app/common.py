"""Shared user identity and China calendar helpers."""

from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Any

Connection = Any
SHANGHAI = ZoneInfo("Asia/Shanghai")


def today_cn() -> date:
    return datetime.now(SHANGHAI).date()


def ensure_user(conn: Connection, username: str) -> int:
    row = conn.execute(
        """
        INSERT INTO users (username)
        VALUES (%s)
        ON CONFLICT (username) DO UPDATE SET username = EXCLUDED.username
        RETURNING id
        """,
        (username,),
    ).fetchone()
    return int(row["id"])
