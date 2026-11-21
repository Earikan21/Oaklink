import pytest

from conftest import FakeMarket
from fundkit.research.etf import premium
from fundkit.models import Quote


def _market(price, nav, bid=None, ask=None, quote_type="ETF"):
    q = Quote(ticker="E", price=price, bid=bid, ask=ask, expense_ratio=None, nav=nav, quote_type=quote_type,
              avg_volume=None)
    return FakeMarket({"E": {"quote": q}})


def test_etf_premium_meaningful_only_beyond_spread():
    # +0.40% premium vs ~0.10% spread -> meaningful
    r = premium("E", _market(100.40, 100.0, bid=100.35, ask=100.45))
    assert r.premium_pct == pytest.approx(0.40)
    assert r.spread_pct == pytest.approx(0.10 / 100.40 * 100)
    assert r.meaningful is True
    # +0.05% premium vs ~0.10% spread -> not meaningful
    r = premium("E", _market(100.05, 100.0, bid=100.00, ask=100.10))
    assert r.premium_pct == pytest.approx(0.05)
    assert r.meaningful is False


def test_discount_is_negative_and_meaningful_by_magnitude():
    r = premium("E", _market(99.60, 100.0, bid=99.55, ask=99.65))
    assert r.premium_pct == pytest.approx(-0.40)
    assert r.meaningful is True


def test_fallback_threshold_when_spread_unknown():
    r = premium("E", _market(100.30, 100.0))
    assert r.spread_pct is None and r.meaningful is True
    r = premium("E", _market(100.20, 100.0))
    assert r.spread_pct is None and r.meaningful is False


def test_etf_premium_rejects_equity():
    with pytest.raises(ValueError):
        premium("E", _market(100.0, 100.0, quote_type="EQUITY"))


@pytest.mark.parametrize("nav", [None, 0.0, -5.0])
def test_etf_premium_rejects_missing_nav(nav):
    with pytest.raises(ValueError):
        premium("E", _market(100.0, nav))
