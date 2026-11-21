import numpy as np
import pandas as pd
import pytest

from conftest import FakeMarket, make_history
from fundkit.models import Quote
from fundkit.research.liquidity import snapshot


def _market(history, bid=None, ask=None):
    q = Quote(ticker="X", price=100.0, bid=bid, ask=ask, expense_ratio=None, nav=None,
              quote_type="EQUITY", avg_volume=None)
    return FakeMarket({"X": {"quote": q, "history": history}})


def _wavy(volume):
    """Deterministic non-constant history: price 100 * (1 + 0.01*sin), volume constant."""
    h = make_history(100.0, 63, 0.0, volume=volume)
    close = 100.0 * (1 + 0.02 * np.sin(np.arange(63)))
    h["Close"] = close
    return h


def test_liquidity_grade_high():
    # $100M/day (100 * 1M shares), 0.05% spread
    r = snapshot("X", _market(make_history(100.0, 63, 0.0, volume=1_000_000.0), bid=99.975, ask=100.025))
    assert r.adtv_dollars == pytest.approx(100e6)
    assert r.spread_pct == pytest.approx(0.0005)
    assert r.grade == "High"


def test_spread_none_when_bid_missing():
    r = snapshot("X", _market(make_history(100.0, 63), bid=None, ask=100.1))
    assert r.spread_pct is None


@pytest.mark.parametrize("bid,ask", [(0.0, 100.0), (-1.0, 100.0), (99.0, 0.0)])
def test_spread_none_when_nonpositive(bid, ask):
    assert snapshot("X", _market(make_history(100.0, 63), bid=bid, ask=ask)).spread_pct is None


def test_amihud_scaled_e9():
    h = _wavy(1_000_000.0)
    r = snapshot("X", _market(h))
    close, vol = h.Close.to_numpy(), h.Volume.to_numpy()
    ret = np.log(close[1:] / close[:-1])
    expected = np.mean(np.abs(ret) / (close[1:] * vol[1:])) * 1e9
    assert r.amihud_e9 == pytest.approx(expected)
    assert r.amihud_e9 > 0


def test_amihud_skips_zero_volume_rows():
    h = _wavy(1_000_000.0)
    h.iloc[10:20, h.columns.get_loc("Volume")] = 0.0
    r = snapshot("X", _market(h))
    close, vol = h.Close.to_numpy(), h.Volume.to_numpy()
    ret = np.log(close[1:] / close[:-1])
    mask = vol[1:] > 0
    expected = np.mean(np.abs(ret[mask]) / (close[1:][mask] * vol[1:][mask])) * 1e9
    assert r.amihud_e9 == pytest.approx(expected)


def test_impact_bps_1pct_adv():
    h = _wavy(1_000_000.0)
    r = snapshot("X", _market(h))
    ret = np.log(h.Close.to_numpy()[1:] / h.Close.to_numpy()[:-1])
    assert r.impact_bps_1pct_adv == pytest.approx(np.std(ret, ddof=1) * np.sqrt(0.01) * 1e4)


def test_adtv_is_mean_of_close_times_volume():
    h = _wavy(500_000.0)
    r = snapshot("X", _market(h))
    assert r.adtv_dollars == pytest.approx(np.mean(h.Close.to_numpy() * h.Volume.to_numpy()))


@pytest.mark.parametrize("dollars,grade", [
    (600e6, "Very High"), (500e6, "High"), (50e6, "High"), (49.9e6, "Moderate"), (5e6, "Moderate"),
    (4.9e6, "Low"), (0.5e6, "Low"), (0.4e6, "Very Low"),
])
def test_grade_table_boundaries(dollars, grade):
    h = make_history(100.0, 63, 0.0, volume=dollars / 100.0)
    assert snapshot("X", _market(h)).grade == grade


def test_all_zero_volume_is_very_low_without_warning():
    h = _wavy(0.0)
    r = snapshot("X", _market(h))
    assert r.grade == "Very Low" and r.adtv_dollars == 0.0 and r.amihud_e9 == 0.0


def test_insufficient_history_raises():
    with pytest.raises(ValueError, match="insufficient history"):
        snapshot("X", _market(make_history(100.0, 2)))
