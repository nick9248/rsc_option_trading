"""Small builders of evidence items for the decision tests (spot 100,000 so a 1 % band is 1,000)."""

from datetime import datetime, timedelta, timezone

from coding.core.analytics.decision.evidence import (
    DIRECTIONAL, PRICE_LEVEL, RANGE_REGIME, EvidenceItem)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
SPOT = 100_000.0


def level(item_id, group, price, quality=0.5, strength=1.0, age_hours=0.0):
    """A price_level item."""
    return EvidenceItem(item_id, f"source {item_id}", group, PRICE_LEVEL, "a test level", NOW - timedelta(hours=age_hours),
                        strength, quality, price=price)


def directional(item_id, group, direction, quality=0.5, strength=1.0, age_hours=0.0, horizon_days=None):
    """A directional item."""
    return EvidenceItem(item_id, f"source {item_id}", group, DIRECTIONAL, "a test lean", NOW - timedelta(hours=age_hours),
                        strength, quality, direction=direction, horizon_days=horizon_days)


def regime(item_id, group, regime_name, quality=0.5, strength=1.0):
    """A range_regime item."""
    return EvidenceItem(item_id, f"source {item_id}", group, RANGE_REGIME, "a test regime", NOW, strength, quality,
                        regime=regime_name)


