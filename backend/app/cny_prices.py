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


def parse(html, observed=None):
    at = observed or b.utcnow()
    p = Tables()
    p.feed(html)
    header = next((r for r in p.rows if r and r[0] == "模型"), None)
    versions = next((r for r in p.rows if r and r[0] == "模型版本"), None)
    if not header or not versions or len(header) != len(versions):
        raise ValueError("official_price_layout_changed")
    start = next(
        (i for i, r in enumerate(p.rows) if r and r[0].startswith("价格")), None
    )
    if start is None:
        raise ValueError("official_prices_missing")
    rows = p.rows[start : start + 6]
    expected = [
        ("缓存命中", "空闲时段"),
        ("高峰时段",),
        ("缓存未命中", "空闲时段"),
        ("高峰时段",),
        ("百万tokens输出", "空闲时段"),
        ("高峰时段",),
    ]
    if len(rows) != 6:
        raise ValueError("official_price_layout_changed")
    for r, words in zip(rows, expected):
        if not all(w in "".join(r[: -len(header) + 1]) for w in words):
            raise ValueError("official_price_layout_changed")
    digest = hashlib.sha256(html.encode()).hexdigest()
    records = []
    for col, raw in enumerate(header[1:]):
        model = re.sub(r"\(\d+\)$", "", raw)
        if not re.fullmatch(r"deepseek-[a-zA-Z0-9._-]+", model):
            raise ValueError("price_model_identity_invalid")
        values = []
        for r in rows:
            value = r[-len(header) + 1 + col]
            if not re.fullmatch(r"\d+(?:\.\d+)?元", value):
                raise ValueError("official_cny_price_units_unknown")
            values.append(str(b.money(value[:-1])))
        rules = {
            period: {
                k: values[i]
                for k, i in zip(("cache_hit", "cache_miss", "output"), indices)
            }
            for period, indices in [("off_peak", (0, 2, 4)), ("peak", (1, 3, 5))]
        }
        # Unknown boundaries explicitly use peak upper bound. Search/fetch remain denied.
        tool_rates = {}
        for tool in ("Search", "Fetch"):
            row = next((r for r in p.rows if r and r[0] == tool), None)
            if (
                not row
                or len(row) != 3
                or row[1] != "每 1,000 次成功请求"
                or not re.fullmatch(r"\d+(?:\.\d+)?元", row[2])
            ):
                raise ValueError("tool_policy_needs_reverification")
            tool_rates[tool.lower() + "_per_success"] = str(b.money(row[2][:-1]) / 1000)
        rules.update(
            boundary_policy="upper_bound_unspecified_official_settlement",
            **tool_rates,
            server_tool_effective_at="2026-11-01T00:00:00+08:00",
            network_tools_enabled=False,
        )
        if not all(
            text in html
            for text in (
                "2026 年 11 月 1 日 00:00",
                "北京时间周一至周五（不含中国法定节假日）9:00 - 12:00、14:00 - 18:00",
            )
        ):
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
