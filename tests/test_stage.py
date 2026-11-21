import numpy as np
import pytest

from conftest import FakeMarket, make_history
from fundkit.research.stage import classify


def _m(hist):
    return FakeMarket({"X": {"history": hist}})


@pytest.fixture
def fake_market_uptrend():
    return _m(make_history(100.0, 300, 0.002))


@pytest.fixture
def fake_market_downtrend():
    return _m(make_history(100.0, 300, -0.002))


def test_stage2_on_rising_series(fake_market_uptrend):
    assert classify("X", fake_market_uptrend) == 2


def test_stage4_on_falling_series(fake_market_downtrend):
    assert classify("X", fake_market_downtrend) == 4


def test_stage1_on_flat_series():
    assert classify("X", _m(make_history(100.0, 300, 0.0))) == 1


def test_stage3_when_topping():
    # Long uptrend then a 15% one-day drop: price falls below the 50MA (not stage 2) but stays above
    # a still-rising 200MA (not stage 4), and the 200MA slope rules out stage 1.
    h = make_history(100.0, 300, 0.002)
    h.loc[h.index[-1], "Close"] = h.Close.iloc[-1] * 0.85
    close = h.Close
    sma200 = close.rolling(200).mean()
    assert close.iloc[-1] > sma200.iloc[-1]
    assert sma200.iloc[-1] / sma200.iloc[-22] - 1 > 0.01
    assert close.iloc[-1] < close.rolling(50).mean().iloc[-1]
    assert classify("X", _m(h)) == 3


def test_stage_requires_250_days():
    with pytest.raises(ValueError, match="insufficient history"):
        classify("X", _m(make_history(100.0, 249, 0.002)))


def test_stage_accepts_exactly_250_days():
    assert classify("X", _m(make_history(100.0, 250, 0.002))) == 2


def test_returns_plain_int(fake_market_uptrend):
    assert type(classify("X", fake_market_uptrend)) is int


# ---------------------------------------------------------------- I6: gaps in the close series
def test_downtrend_with_one_nan_still_stage_4():
    h = make_history(100.0, 300, -0.002)
    h.loc[h.index[-10], "Close"] = np.nan
    assert classify("X", _m(h)) == 4


def test_nans_count_against_the_minimum_history():
    h = make_history(100.0, 255, -0.002)
    h.loc[h.index[:10], "Close"] = np.nan  # 245 usable closes
    with pytest.raises(ValueError, match="insufficient history"):
        classify("X", _m(h))


def test_non_finite_moving_average_is_insufficient_history():
    h = make_history(100.0, 300, -0.002)
    h.loc[h.index[-5], "Close"] = np.inf
    with pytest.raises(ValueError, match="insufficient history"):
        classify("X", _m(h))
