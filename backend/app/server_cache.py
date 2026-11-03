"""A small stale-while-revalidate cache for read-only pages that are expensive to build.

A copy younger than `ttl` seconds is returned as is. An older copy (up to `max_stale`) is returned at once while ONE
background refresh rebuilds it, so a person opening the page never waits for the rebuild. Only when there is nothing
at all is the page built on the spot.
"""

import threading
import time


class StaleCache:
    def __init__(self, ttl=30, max_stale=600, clock=time.monotonic):
        self.ttl, self.max_stale, self.clock = ttl, max_stale, clock
        self._items = {}
        self._refreshing = set()
        self._lock = threading.Lock()

    def get(self, key, build, fresh=False):
        now = self.clock()
        with self._lock:
            item = self._items.get(key)
        if item and not fresh:
            age = now - item[0]
            if age < self.ttl:
                return item[1]
            if age < self.max_stale:
                self._refresh_in_background(key, build)
                return item[1]
        value = build()
        with self._lock:
            self._items[key] = (self.clock(), value)
        return value

    def _refresh_in_background(self, key, build):
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)

        def run():
            try:
                value = build()
                with self._lock:
                    self._items[key] = (self.clock(), value)
            except Exception:  # noqa: BLE001  (the old copy keeps being served; the next request tries again)
                pass
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        threading.Thread(target=run, daemon=True).start()

    def invalidate(self, key=None):
        with self._lock:
            if key is None:
                self._items.clear()
            else:
                self._items.pop(key, None)
