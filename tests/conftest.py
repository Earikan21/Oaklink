from datetime import date, timedelta

import pytest

from fundkit.models import Holding
from fundkit.store import Store


def d(offset_days: int) -> date:
    """Test calendar: d(0) is 'today' (2026-11-29)."""
    return date(2026, 11, 29) + timedelta(days=offset_days)


@pytest.fixture
def store_with_cash_3000(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.save_holdings([Holding(ticker="CASH", sleeve="cash", shares=3000.0, cost_basis=1.0, lot_date=date(2026, 11, 22))])
    return s


# ---------------------------------------------------------------------------
# Market-data test doubles
# ---------------------------------------------------------------------------
import numpy as np
import pandas as pd

from fundkit.market import MarketUnavailable
from fundkit.models import Quote


def make_history(start_price: float, days: int, drift: float = 0.0) -> pd.DataFrame:
    """Synthetic OHLCV frame, tz-naive business-day index ending 2026-11-27.

    Close compounds at `drift` per day from `start_price`; High/Low are +/-1%
    of Close, Open is the prior Close. Deterministic (no randomness).
    """
    idx = pd.bdate_range(end="2026-11-27", periods=days)
    close = start_price * (1.0 + drift) ** np.arange(days)
    open_ = np.concatenate([[start_price], close[:-1]])
    return pd.DataFrame(
        {"Open": open_, "High": close * 1.01, "Low": close * 0.99, "Close": close,
         "Volume": np.full(days, 1_000_000.0)},
        index=idx,
    )


class FakeMarket:
    """MarketData backed by dicts. `data[ticker]` may hold keys: quote, history,
    info, income_stmt, cashflow, balance_sheet, earnings_estimate, eps_trend,
    eps_revisions, earnings_dates. Unknown ticker or missing key raises
    MarketUnavailable. `data["__screen__"]` is returned for ANY screen query
    (default []); the query itself is ignored.
    """

    def __init__(self, data: dict):
        self.data = data

    def _get(self, ticker: str, key: str):
        entry = self.data.get(ticker)
        if not isinstance(entry, dict) or key not in entry:
            raise MarketUnavailable(ticker)
        return entry[key]

    def quote(self, ticker: str) -> Quote:
        return self._get(ticker, "quote")

    def history(self, ticker: str, period: str = "1y") -> pd.DataFrame:
        return self._get(ticker, "history")

    def info(self, ticker: str) -> dict:
        return self._get(ticker, "info")

    def income_stmt(self, ticker: str) -> pd.DataFrame:
        return self._get(ticker, "income_stmt")

    def cashflow(self, ticker: str) -> pd.DataFrame:
        return self._get(ticker, "cashflow")

    def balance_sheet(self, ticker: str) -> pd.DataFrame:
        return self._get(ticker, "balance_sheet")

    def earnings_estimate(self, ticker: str) -> pd.DataFrame:
        return self._get(ticker, "earnings_estimate")

    def eps_trend(self, ticker: str) -> pd.DataFrame:
        return self._get(ticker, "eps_trend")

    def eps_revisions(self, ticker: str) -> pd.DataFrame:
        return self._get(ticker, "eps_revisions")

    def earnings_dates(self, ticker: str) -> pd.DataFrame:
        return self._get(ticker, "earnings_dates")

    def screen(self, query: dict) -> list[str]:
        return list(self.data.get("__screen__", []))
