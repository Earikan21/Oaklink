import os
import time

import pandas as pd
import pytest

from conftest import FakeMarket, make_history
from fundkit.market import MarketUnavailable, YFMarket, build_equity_query
from fundkit.models import Quote


class _StubTicker:
    def __init__(self, price=100.0, info=None, hist=None):
        self.fast_info = {"lastPrice": price}
        self.info = info if info is not None else {}
        self._hist = hist
        self.income_stmt = pd.DataFrame({"a": [1]})

    def history(self, period="1y", auto_adjust=True):
        return self._hist if self._hist is not None else pd.DataFrame()


def _patch(monkeypatch, **kw):
    calls = {"n": 0}

    def fake_ticker(t):
        calls["n"] += 1
        return _StubTicker(**kw)

    monkeypatch.setattr("fundkit.market.yf.Ticker", fake_ticker)
    return calls


def test_quote_cached_within_ttl(tmp_path, monkeypatch):
    calls = _patch(monkeypatch, price=100.0)
    m = YFMarket(tmp_path)
    assert m.quote("VTI").price == 100.0
    assert m.quote("VTI").price == 100.0
    assert calls["n"] == 1
    assert (tmp_path / "VTI" / "quote.pkl").exists()


def test_cache_miss_after_ttl(tmp_path, monkeypatch):
    calls = _patch(monkeypatch, price=100.0)
    m = YFMarket(tmp_path, ttl_hours=1)
    m.quote("VTI")
    old = time.time() - 2 * 3600
    os.utime(tmp_path / "VTI" / "quote.pkl", (old, old))
    m.quote("VTI")
    assert calls["n"] == 2


def test_ttl_zero_always_refetches(tmp_path, monkeypatch):
    calls = _patch(monkeypatch)
    m = YFMarket(tmp_path, ttl_hours=0)
    m.quote("VTI")
    m.quote("VTI")
    assert calls["n"] == 2


def test_unpriceable_ticker_raises(tmp_path, monkeypatch):
    _patch(monkeypatch, price=None)
    with pytest.raises(MarketUnavailable) as e:
        YFMarket(tmp_path).quote("ZZZZ")
    assert e.value.ticker == "ZZZZ"


def test_quote_falls_back_to_info_price(tmp_path, monkeypatch):
    _patch(monkeypatch, price=None, info={"regularMarketPrice": 42.0})
    assert YFMarket(tmp_path).quote("X").price == 42.0


def test_quote_tolerates_missing_info_keys(tmp_path, monkeypatch):
    _patch(monkeypatch, price=10.0, info={})
    q = YFMarket(tmp_path).quote("X")
    assert (q.bid, q.ask, q.expense_ratio, q.nav, q.avg_volume) == (None,) * 5


def test_quote_fills_from_info(tmp_path, monkeypatch):
    _patch(monkeypatch, price=10.0, info={"bid": 9.9, "ask": 10.1, "navPrice": 10.0,
                                          "quoteType": "ETF", "averageVolume": 5e6})
    q = YFMarket(tmp_path).quote("X")
    assert (q.bid, q.ask, q.nav, q.quote_type, q.avg_volume) == (9.9, 10.1, 10.0, "ETF", 5e6)


@pytest.mark.parametrize("raw,expected", [(0.03, 0.0003), (0.9, 0.009)])
def test_expense_ratio_normalised(tmp_path, monkeypatch, raw, expected):
    _patch(monkeypatch, price=10.0, info={"netExpenseRatio": raw})
    assert YFMarket(tmp_path).quote("X").expense_ratio == pytest.approx(expected)


def test_history_is_tz_naive(tmp_path, monkeypatch):
    h = make_history(100, 5)
    h.index = h.index.tz_localize("America/New_York")
    _patch(monkeypatch, hist=h)
    out = YFMarket(tmp_path).history("VTI")
    assert out.index.tz is None
    assert len(out) == 5


def test_empty_history_raises(tmp_path, monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(MarketUnavailable):
        YFMarket(tmp_path).history("VTI")


def test_history_cache_keyed_by_period(tmp_path, monkeypatch):
    calls = _patch(monkeypatch, hist=make_history(100, 5))
    m = YFMarket(tmp_path)
    m.history("VTI", "1y")
    m.history("VTI", "5y")
    m.history("VTI", "1y")
    assert calls["n"] == 2


def test_statement_passthrough_cached(tmp_path, monkeypatch):
    calls = _patch(monkeypatch)
    m = YFMarket(tmp_path)
    assert m.income_stmt("AAA").equals(pd.DataFrame({"a": [1]}))
    m.income_stmt("AAA")
    assert calls["n"] == 1


def test_equity_query_mapping_and_unknown_key():
    q, limit = build_equity_query({"region": "us", "pe_lt": 15, "industry": "Banks—Regional", "limit": 6})
    assert limit == 6
    d = q.to_dict()
    assert d["operator"] == "AND"
    assert {"operator": "LT", "operands": ["peratio.lasttwelvemonths", 15]} in d["operands"]
    assert {"operator": "EQ", "operands": ["industry", "Banks—Regional"]} in d["operands"]
    with pytest.raises(ValueError):
        build_equity_query({"bogus": 1})


def test_screen_returns_symbols(tmp_path, monkeypatch):
    seen = {}

    def fake_screen(query, size=None, **kw):
        seen["size"] = size
        return {"quotes": [{"symbol": "AAA"}, {"symbol": "BBB"}]}

    monkeypatch.setattr("fundkit.market.yf.screen", fake_screen)
    assert YFMarket(tmp_path).screen({"industry": "Banks—Regional", "limit": 2}) == ["AAA", "BBB"]
    assert seen["size"] == 2


def test_fake_market_unknown_ticker_raises():
    fm = FakeMarket({"VTI": {"quote": Quote(ticker="VTI", price=1, bid=None, ask=None,
                                            expense_ratio=None, nav=None, quote_type="ETF",
                                            avg_volume=None)}})
    assert fm.quote("VTI").price == 1
    with pytest.raises(MarketUnavailable):
        fm.quote("NOPE")
    with pytest.raises(MarketUnavailable):
        fm.history("VTI")
    assert fm.screen({"x": 1}) == []


def test_make_history_shape():
    h = make_history(100.0, 10, drift=0.01)
    assert list(h.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(h) == 10 and h.index.tz is None
    assert h["Close"].iloc[0] == 100.0 and h["Close"].iloc[-1] > 100.0


# ---------------------------------------------------------------- I5: invalid prices are unavailable
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0, None])
def test_invalid_price_raises_market_unavailable(tmp_path, monkeypatch, bad):
    _patch(monkeypatch, price=bad, info={"regularMarketPrice": bad})
    with pytest.raises(MarketUnavailable):
        YFMarket(tmp_path).quote("ZZZZ")


def test_invalid_fast_price_falls_back_to_valid_info_price(tmp_path, monkeypatch):
    _patch(monkeypatch, price=float("nan"), info={"regularMarketPrice": 42.0})
    assert YFMarket(tmp_path).quote("X").price == 42.0


def test_history_is_unadjusted_like_quotes(tmp_path, monkeypatch):
    seen = {}

    class Rec(_StubTicker):
        def history(self, period="1y", **kw):
            seen.update(kw, period=period)
            return make_history(100, 5)

    monkeypatch.setattr("fundkit.market.yf.Ticker", lambda t: Rec())
    YFMarket(tmp_path).history("VTI", "2y")
    assert seen == {"period": "2y", "auto_adjust": False}
