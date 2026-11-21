import numpy as np
import pandas as pd
import pytest

from conftest import FakeMarket
from fundkit.research.correlation import max_corr_with, pairwise


def _series(days, seed, start=100.0, end="2026-11-27"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=days)
    close = start * np.exp(np.cumsum(rng.normal(0, 0.01, days)))
    return pd.DataFrame({"Close": close}, index=idx)


@pytest.fixture
def fake_market_two_series():
    a = _series(200, 1)
    rng = np.random.default_rng(2)
    # B is partly driven by A's returns so the correlation is neither 0 nor 1.
    b_ret = 0.5 * np.log(a.Close / a.Close.shift(1)).fillna(0).to_numpy() + rng.normal(0, 0.01, 200)
    b = pd.DataFrame({"Close": 50 * np.exp(np.cumsum(b_ret))}, index=a.index)
    c = _series(200, 3)
    return FakeMarket({"A": {"history": a}, "B": {"history": b}, "C": {"history": c}}), a, b, c


def _logret(df):
    return np.log(df.Close.to_numpy()[1:] / df.Close.to_numpy()[:-1])


def test_pairwise_identity_diagonal(fake_market_two_series):
    m, a, b, _ = fake_market_two_series
    corr = pairwise(["A", "B"], m)
    assert list(corr.index) == ["A", "B"] and list(corr.columns) == ["A", "B"]
    assert corr.loc["A", "A"] == pytest.approx(1.0)
    assert corr.loc["B", "B"] == pytest.approx(1.0)
    expected = np.corrcoef(_logret(a), _logret(b))[0, 1]
    assert corr.loc["A", "B"] == pytest.approx(expected)
    assert corr.loc["B", "A"] == pytest.approx(expected)
    assert 0.1 < expected < 0.95  # fixture sanity: genuinely in between


def test_pairwise_inner_joins_on_dates():
    a = _series(200, 1)
    b = _series(200, 2).drop(a.index[100:110])  # B missing 10 dates
    m = FakeMarket({"A": {"history": a}, "B": {"history": b}})
    corr = pairwise(["A", "B"], m)
    joined = pd.concat([a.Close, b.Close], axis=1, keys=["A", "B"]).dropna()
    ret = np.log(joined / joined.shift(1)).dropna()
    assert corr.loc["A", "B"] == pytest.approx(np.corrcoef(ret.A, ret.B)[0, 1])


def test_pairwise_raises_on_insufficient_overlap():
    m = FakeMarket({"A": {"history": _series(200, 1)}, "B": {"history": _series(50, 2)}})
    with pytest.raises(ValueError, match="insufficient overlap"):
        pairwise(["A", "B"], m)


def test_pairwise_accepts_exactly_60_overlapping_returns():
    m = FakeMarket({"A": {"history": _series(200, 1)}, "B": {"history": _series(61, 2)}})
    corr = pairwise(["A", "B"], m)  # 61 prices -> 60 returns
    assert corr.shape == (2, 2)


def test_max_corr_with_empty_held_is_zero(fake_market_two_series):
    m, *_ = fake_market_two_series
    assert max_corr_with("A", [], m) == 0.0


def test_max_corr_with_returns_max_over_held(fake_market_two_series):
    m, a, b, c = fake_market_two_series
    expected = max(np.corrcoef(_logret(a), _logret(x))[0, 1] for x in (b, c))
    assert max_corr_with("A", ["B", "C"], m) == pytest.approx(expected)


def test_max_corr_with_ignores_self_in_held(fake_market_two_series):
    m, a, b, _ = fake_market_two_series
    assert max_corr_with("A", ["A", "B"], m) == pytest.approx(np.corrcoef(_logret(a), _logret(b))[0, 1])
    assert max_corr_with("A", ["A"], m) == 0.0


def test_pairwise_differing_lengths_and_start_dates_no_warning():
    # shorter series first: the union index then differs from the first series' index (the warning trigger)
    m = FakeMarket({"A": {"history": _series(250, 2, end="2026-11-24")},
                    "B": {"history": _series(300, 1)}})
    out = pairwise(["A", "B"], m)
    assert out.shape == (2, 2)
    assert out.loc["A", "A"] == pytest.approx(1.0)
