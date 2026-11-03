"""Server push for the question-writing progress page and the home progress card.

PostgreSQL triggers (sql/038) send NOTIFY on job-state changes, bank changes and worker runs. ONE listener thread of the API
process hears them (the worker is a different process, so the database is the bridge), waits a moment so a burst becomes
one event, rebuilds the cached progress pages once, and only then tells every open page "there is an update". The pages
(server-sent events at /v2/model/progress/events) fetch the cached copy, so a burst of changes costs one rebuild no matter
how many pages are open. Payloads carry no data: just that something changed.
"""
import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)
CHANNEL = "progress_update"
DEBOUNCE_SECONDS = 0.5


class Hub:
    """Fan-out of 'update' events to the open event streams (each lives on its own asyncio loop)."""

    def __init__(self):
        self.version = 0
        self.closing = False
        self._subscribers = set()
        self._lock = threading.Lock()

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)           # a slow page only needs to know that SOMETHING is new
        entry = (asyncio.get_running_loop(), queue)
        with self._lock:
            self._subscribers.add(entry)
        return entry

    def unsubscribe(self, entry):
        with self._lock:
            self._subscribers.discard(entry)

    def publish(self, reason="update"):
        with self._lock:
            self.version += 1
            version, targets = self.version, list(self._subscribers)
        for loop, queue in targets:
            loop.call_soon_threadsafe(_offer, queue, {"version": version, "reason": reason})
        return version

    def count(self):
        with self._lock:
            return len(self._subscribers)


def _offer(queue, event):
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(event)


HUB = Hub()
_REBUILDERS = []


def on_change(rebuild):
    """Register a callable that rebuilds a cached page; it runs once per burst, before the pages are told."""
    _REBUILDERS.append(rebuild)


def announce(reason="update"):
    """Rebuild the cached pages, then tell the open pages (also usable in-process, e.g. after cancelling a task)."""
    for rebuild in list(_REBUILDERS):
        try:
            rebuild()
        except Exception as exc:  # noqa: BLE001
            logger.error("Progress rebuild failed (%s)", type(exc).__name__)
    return HUB.publish(reason)


def listen_forever(stop, connect=None):
    """The listener loop (run in a daemon thread). Reconnects after any failure; never raises."""
    from app.db import listen_connection

    connect = connect or listen_connection
    while not stop.is_set():
        conn = None
        try:
            conn, schema = connect()
            conn.execute(f"LISTEN {CHANNEL}")
            pending, first_at = set(), None
            while not stop.is_set():
                got = False
                for note in conn.notifies(timeout=0.25):
                    got = True
                    if note.payload.split(":", 1)[0] == schema:    # another schema of the same database is not ours
                        pending.add(note.payload.split(":", 1)[-1])
                        first_at = first_at or time.monotonic()
                if pending and time.monotonic() - first_at >= DEBOUNCE_SECONDS:
                    announce(",".join(sorted(pending)))
                    pending, first_at = set(), None
                elif not got and not pending:
                    continue
        except Exception as exc:  # noqa: BLE001  (the pages keep polling every 5 s while this is down)
            logger.error("Progress listener stopped (%s); retrying", type(exc).__name__)
            stop.wait(5)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


def start(stop):
    thread = threading.Thread(target=listen_forever, args=(stop,), daemon=True, name="progress-listener")
    thread.start()
    return thread
