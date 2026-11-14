"""Public Chinese price page intake. Never an API-key call or implicit FX conversion."""

from datetime import timedelta
from html.parser import HTMLParser
import hashlib
import re
import httpx
from app import cny_billing as b


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        if tag in ("td", "th"):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


_EXPECTED_ROWS = [
    ("缓存命中", "空闲时段"),
    ("高峰时段",),
    ("缓存未命中", "空闲时段"),
    ("高峰时段",),
    ("百万tokens输出", "空闲时段"),
    ("高峰时段",),
]


def _plain(value):
    """Canonical text for an amount ('9.0' and '9' are the same tariff; a stored price is compared as text, so a cosmetic
    '.0' on the page must not look like a changed price)."""
    text = format(b.money(value).normalize(), "f")
    return text


def _price_rows(all_rows, width):
    """The six model-price rows. Every row starting with 价格 is tried, so an extra table or notice before or after the price
    table (concurrency limit, search and fetch prices) cannot be mistaken for it."""
    for i, r in enumerate(all_rows):
        if not (r and r[0].startswith("价格")):
            continue
        rows = all_rows[i : i + 6]
        if len(rows) != 6 or any(len(x) < width for x in rows):
            continue
        if all(
            all(w in "".join(x[: -width + 1]) for w in words)
            for x, words in zip(rows, _EXPECTED_ROWS)
        ):
            return rows
    raise ValueError(
        "official_prices_missing"
        if not any(r and r[0].startswith("价格") for r in all_rows)
        else "official_price_layout_changed"
    )


def _tool_rates(all_rows):
    rates = {}
    for tool in ("Search", "Fetch"):
        row = next((r for r in all_rows if r and r[0] == tool), None)
        if not row:
            continue
        price = next((c for c in row[1:] if re.fullmatch(r"\d+(?:\.\d+)?元", c)), None)
        unit = next((c for c in row[1:] if re.search(r"1,?000", c)), None)
        if price and unit:
            rates[tool.lower() + "_per_success"] = _plain(b.money(price[:-1]) / 1000)
    return rates


# The peak/off-peak rule this system prices by (cost_control.is_peak): Beijing time, Monday-Friday except Chinese public
# holidays, 09:00-12:00 and 14:00-18:00 is peak; everything else (weekends, holidays) is off-peak at half the peak price.
EXPECTED_PERIOD_RULES = dict(
    weekdays="mon-fri", hours=((540, 720), (840, 1080)), holidays_off_peak=True, off_peak_ratio="half",
)
_RANGE = re.compile(r"(\d{1,2})[:：](\d{2})\s*[-–—~～至到]\s*(\d{1,2})[:：](\d{2})")
_HALF = re.compile(r"一半|50\s*%|1\s*/\s*2|二分之一|五折|减半|半价")


def period_rules(text):
    """The peak-period RULE stated on the price page (weekdays, hours, holiday treatment, off-peak ratio), independent of how
    it is worded. Anything not stated clearly comes back as None / empty so it cannot equal the rule we price by."""
    sentences = [x.strip() for x in re.split(r"[。；;\n]", text) if x.strip()]
    ranged = [x for x in sentences if _RANGE.search(x)]
    peak = next((x for x in ranged if "高峰" in x), ranged[0] if ranged else "")
    hours = tuple(sorted((int(a) * 60 + int(b), int(c) * 60 + int(d)) for a, b, c, d in _RANGE.findall(peak)))
    weekdays = "mon-fri" if re.search(r"周一\s*(至|到|-|~)\s*周五|星期一\s*(至|到)\s*星期五|工作日", peak) else None
    holiday_excluded = bool(re.search(r"不含[^，。）)]{0,8}节假日|除[^，。）)]{0,8}节假日", peak))
    ratio_sentence = next((x for x in sentences if re.search(r"空闲|低峰|非高峰|闲时", x) and re.search(r"高峰", x)
                           and re.search(r"一半|\d+\s*%|\d\s*折|\d\s*/\s*\d|二分之一|减半|半价|倍|[一二三四五六七八九\d]\s*成", x)), "")
    if not ratio_sentence:
        ratio = "half"          # not restated on the page: the rate table itself carries the prices
    else:
        ratio = "half" if _HALF.search(ratio_sentence) and not re.search(r"(?<!5)(?<!50)\d+\s*%|[1-46-9]\s*折|倍|[一二三四五六七八九\d]\s*成", ratio_sentence) else "changed"
    # weekends and holidays are off-peak: said outright, or implied by "Monday-Friday except holidays"
    offpeak_days = holiday_excluded and weekdays == "mon-fri"
    return dict(weekdays=weekdays, hours=hours, holidays_off_peak=offpeak_days, off_peak_ratio=ratio)


def parse(html, observed=None):
    at = observed or b.utcnow()
    p = Tables()
    p.feed(html)
    header = next((r for r in p.rows if r and r[0] == "模型"), None)
    versions = next((r for r in p.rows if r and r[0] == "模型版本"), None)
    if not header or not versions or len(header) != len(versions):
        raise ValueError("official_price_layout_changed")
    rows = _price_rows(p.rows, len(header))
    digest = hashlib.sha256(html.encode()).hexdigest()
    page_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))
    records = []
    for col, raw in enumerate(header[1:]):
        model = re.sub(r"\s*[(（\[]\d+[)）\]]\s*", "", raw).strip()
        if not re.fullmatch(r"deepseek-[a-zA-Z0-9._-]+", model):
            raise ValueError("price_model_identity_invalid")
        values = []
        for r in rows:
            value = r[-len(header) + 1 + col]
            if not re.fullmatch(r"\d+(?:\.\d+)?元", value):
                raise ValueError("official_cny_price_units_unknown")
            values.append(_plain(value[:-1]))
        rules = {
            period: {
                k: values[i]
                for k, i in zip(("cache_hit", "cache_miss", "output"), indices)
            }
            for period, indices in [("off_peak", (0, 2, 4)), ("peak", (1, 3, 5))]
        }
        # Unknown boundaries explicitly use peak upper bound. Search/fetch stay disabled; their table is not needed for model
        # billing, so a missing or reshaped one never blocks the model prices (rates are kept only when it is in the known shape).
        tool_rates = _tool_rates(p.rows)
        rules.update(
            boundary_policy="upper_bound_unspecified_official_settlement",
            **tool_rates,
            server_tool_effective_at="2026-11-01T00:00:00+08:00",
            network_tools_enabled=False,
        )
        if period_rules(page_text) != EXPECTED_PERIOD_RULES:
            raise ValueError("official_period_policy_needs_reverification")
        records.append(
            dict(
                model_id=model,
                model_version=versions[col + 1],
                currency="CNY",
                version=model + ":cny:" + digest[:20] + ":" + at.isoformat(),
                valid_from=at.isoformat(),
                valid_until=(at + timedelta(hours=24)).isoformat(),
                official_effective_at=None,
                source_url=b.SOURCE,
                source_sha256=digest,
                verified_at=at.isoformat(),
                rules=rules,
            )
        )
    return records


_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{3,60}")


def failure_code(exc):
    """A short fixed code for why a refresh failed: our own reason (official_price_layout_changed, ...), the HTTP status, or the
    kind of network error. Never text from the page."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "network_" + type(exc).__name__
    if isinstance(exc, ValueError) and _SAFE_CODE.fullmatch(str(exc)):
        return str(exc)
    return type(exc).__name__


def remember_failure(conn, code):
    from app import legacy_reuse

    old = legacy_reuse._state(conn, "price_refresh") or {}
    legacy_reuse._set_state(conn, "price_refresh", dict(ok=False, code=code, failures=int(old.get("failures", 0)) + 1,
                                                       at=b.utcnow().isoformat(), last_ok_at=old.get("last_ok_at")))


def remember_success(conn):
    from app import legacy_reuse

    now = b.utcnow().isoformat()
    legacy_reuse._set_state(conn, "price_refresh", dict(ok=True, code=None, failures=0, at=now, last_ok_at=now))


def status(conn):
    """For the progress page: did the last refresh work, when do the prices in force run out, and what then happens."""
    from app import legacy_reuse

    state = legacy_reuse._state(conn, "price_refresh") or {}
    row = conn.execute("SELECT max(valid_until) AS until FROM ai_cny_prices WHERE valid_until>verified_at").fetchone()
    until = row["until"] if row else None
    left = conn.execute("SELECT EXTRACT(EPOCH FROM (%s::timestamptz - clock_timestamp())) AS s", (until,)).fetchone()["s"] if until else None
    return dict(
        ok=state.get("ok", True), code=state.get("code"), failures=state.get("failures", 0), last_ok_at=state.get("last_ok_at"),
        valid_until=until, hours_left=None if left is None else round(left / 3600, 1),
        blocking=bool(until is None or left is None or left <= 0),
    )


def refresh(conn):
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(b.SOURCE, headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
    records = parse(response.text)
    for p in records:
        b.install_price(conn, p)
    # If rates conflict with a still-active observation, current_price blocks calls.
    return [dict(model_id=p["model_id"], version=p["version"]) for p in records]


def main():
    import argparse
    from app.db import get_db

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-official-cny-prices", required=True, action="store_true"
    )
    parser.parse_args()
    with get_db() as c:
        print(refresh(c))


if __name__ == "__main__":
    main()
