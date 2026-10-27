"""Independent deadline and optional integration loops; no AI calls."""

import logging
import os
import threading
from app.db import get_db

logger = logging.getLogger(__name__)


def tick():
    from app.training_sessions import expire_due

    with get_db() as conn:
        expire_due(conn)


def integrations_tick():
    if os.environ.get("QUIZ_SERVICE_ROLE", "api") == "api":
        from app.scheduled_backup import tick as backup_tick

        backup_tick()  # Uses the API service's persistent volume, not ephemeral batch storage.
    from app.archiving import configured_adapter, process_one
    from app.automation import prepare_daily

    adapter = configured_adapter()
    if adapter:
        try:
            with get_db() as conn:
                process_one(conn, adapter)
        finally:
            adapter.close()
    with get_db() as conn:
        prepare_daily(conn)


def main():
    stop = threading.Event()

    def loop(fn, interval):
        while not stop.is_set():
            try:
                fn()
            except Exception as exc:
                logger.error("Worker cycle failed (%s)", type(exc).__name__)
            stop.wait(interval)

    threads = [
        threading.Thread(target=loop, args=(tick, 1)),
        threading.Thread(target=loop, args=(integrations_tick, 2)),
    ]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        stop.set()
        for thread in threads:
            thread.join()


if __name__ == "__main__":
    main()
