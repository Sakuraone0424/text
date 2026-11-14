"""Response times per route (p50 / p95 / max over the last 500 requests of each), for /v2/perf and the slow-request log."""

import logging
import threading
from collections import defaultdict, deque

log = logging.getLogger("quiz.perf")
SLOW_MS = 300
_TIMES = defaultdict(lambda: deque(maxlen=500))
_LOCK = threading.Lock()


def record(route, method, ms, status):
    with _LOCK:
        _TIMES[(method, route)].append((ms, status))
    if ms > SLOW_MS:
        log.warning("slow request %s %s %d ms (status %s)", method, route, ms, status)


def _percentile(values, share):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(share * (len(values) - 1))))]


def report():
    out = []
    with _LOCK:
        items = {k: list(v) for k, v in _TIMES.items()}
    for (method, route), rows in sorted(items.items()):
        ms = [r[0] for r in rows]
        out.append(dict(route=route, method=method, count=len(ms), p50_ms=_percentile(ms, 0.5), p95_ms=_percentile(ms, 0.95), max_ms=max(ms),
                        errors=sum(1 for r in rows if r[1] >= 500)))
    out.sort(key=lambda r: -r["p95_ms"])
    return out


def reset():
    with _LOCK:
        _TIMES.clear()
