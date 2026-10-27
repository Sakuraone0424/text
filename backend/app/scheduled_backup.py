"""Opt-in daily private snapshots; same-volume retention is not off-site recovery."""

from datetime import datetime
from zoneinfo import ZoneInfo
import os
from app.db import get_db


def due(index, now, hour=4):
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if not 0 <= hour <= 23:
        raise ValueError("invalid_backup_hour")
    if local.hour < hour:
        return False
    previous = (index or {}).get("snapshot_at")
    if not previous:
        return True
    stamp = datetime.fromisoformat(previous.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("backup_snapshot_timezone_missing")
    return stamp.astimezone(ZoneInfo("Asia/Shanghai")).date() < local.date()


def tick():
    if os.environ.get("QUIZ_PRIVATE_BACKUP_ENABLED") != "1":
        return False
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").lower() == "production":
        raise PermissionError("Private backup target must be nonproduction")
    from app import review_app

    with get_db() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(280049) AS locked").fetchone()[
            "locked"
        ]:
            return False
        try:
            now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            try:
                _, index = review_app.backup_files()
            except Exception as exc:
                from fastapi import HTTPException

                if not isinstance(exc, (HTTPException, FileNotFoundError)):
                    raise
                index = None
            if not due(
                index, now, int(os.environ.get("QUIZ_PRIVATE_BACKUP_HOUR", "4"))
            ):
                return False
            review_app.backup_refresh(name="scheduled_private_backup_not_human_review")
            return True
        finally:
            conn.execute("SELECT pg_advisory_unlock(280049)")
