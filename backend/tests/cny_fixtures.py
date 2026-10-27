"""Synthetic RMB rates, never a live price/source assertion."""

from datetime import timedelta
from app.cny_billing import utcnow, SOURCE


def price(model="deepseek-flash", version=None):
    at = utcnow()
    return dict(
        model_id=model,
        model_version=None,
        currency="CNY",
        version=version or "synthetic-cny-" + model,
        valid_from=(at - timedelta(seconds=1)).isoformat(),
        valid_until=(at + timedelta(hours=23)).isoformat(),
        verified_at=at.isoformat(),
        source_url=SOURCE,
        source_sha256="f" * 64,
        rules=dict(
            off_peak=dict(cache_hit="0.02", cache_miss="1", output="4"),
            peak=dict(cache_hit="0.04", cache_miss="2", output="8"),
        ),
    )
