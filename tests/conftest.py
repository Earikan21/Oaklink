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


def make_history(start_price: float, days: int, drift: float = 0.0, volume: float = 1_000_000.0) -> pd.DataFrame:
    """Synthetic OHLCV frame, tz-naive business-day index ending 2026-11-27.

    Close compounds at `drift` per day from `start_price`; High/Low are +/-1%
    of Close, Open is the prior Close, Volume is constant `volume`. Deterministic (no randomness).
    """
    idx = pd.bdate_range(end="2026-11-27", periods=days)
    close = start_price * (1.0 + drift) ** np.arange(days)
    open_ = np.concatenate([[start_price], close[:-1]])
    return pd.DataFrame(
        {"Open": open_, "High": close * 1.01, "Low": close * 0.99, "Close": close,
         "Volume": np.full(days, volume)},
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


# ---------------------------------------------------------------------------
# $3k reference portfolio: VTI 1500 / BND 900 / CASH 600 (50/0/30/20)
# ---------------------------------------------------------------------------
def make_holding(ticker: str, sleeve: str, shares: float, cost_basis: float = 1.0) -> Holding:
    return Holding(ticker=ticker, sleeve=sleeve, shares=shares, cost_basis=cost_basis, lot_date=date(2026, 11, 22))


@pytest.fixture
def three_k_portfolio():
    holdings = [
        make_holding("VTI", "indexed_equity", 5, 300.0),
        make_holding("BND", "bonds", 12, 75.0),
        make_holding("CASH", "cash", 600.0),
    ]
    values = {"VTI": 1500.0, "BND": 900.0, "CASH": 600.0}
    return holdings, values


@pytest.fixture
def fake_market_3k():
    def entry(ticker, price, name):
        q = Quote(ticker=ticker, price=price, bid=None, ask=None, expense_ratio=0.0003, nav=None,
                  quote_type="ETF", avg_volume=None)
        return {"quote": q, "info": {"longName": name, "quoteType": "ETF", "netExpenseRatio": 0.03}}

    return FakeMarket({
        "VTI": entry("VTI", 300.0, "Vanguard Total Stock Market ETF"),
        "BND": entry("BND", 75.0, "Vanguard Total Bond Market ETF"),
    })


# ---------------------------------------------------------------------------
# ACME valuation fixtures (revenue 100 -> 110 -> 121; columns are NEWEST FIRST)
# ---------------------------------------------------------------------------
_ACME_YEARS = ["2025-12-31", "2024-12-31", "2023-12-31"]
_ACME_REV = [121.0, 110.0, 100.0]


def _acme_statements(ebit_margin: float = 0.20, drop: tuple[str, ...] = ()):
    rev = np.array(_ACME_REV)
    ebit = rev * ebit_margin
    tax = np.where(ebit > 0, ebit * 0.21, 0.0)
    income = pd.DataFrame(
        {
            "Total Revenue": rev,
            "Operating Income": ebit,
            "Pretax Income": ebit,
            "Tax Provision": tax,
            "Diluted EPS": [2.0, 1.8, 1.6],
            "Interest Expense": [0.0, 0.0, 0.0],
        },
        index=_ACME_YEARS,
    ).T
    cash = pd.DataFrame(
        {
            "Depreciation And Amortization": rev * 0.05,
            "Capital Expenditure": rev * -0.06,
            "Change In Working Capital": rev * -0.01,
            "Free Cash Flow": rev * 0.10,
        },
        index=_ACME_YEARS,
    ).T
    return income, cash.drop(index=list(drop))


def _acme_market(*, beta=1.0, ebit_margin=0.20, ebitda=24.2 + 6.05, drop=(), debt=0.0, interest=0.0,
                 n_years=3, info_overrides=None):
    income, cash = _acme_statements(ebit_margin, drop)
    income.loc["Interest Expense", income.columns[0]] = interest
    income, cash = income.iloc[:, :n_years], cash.iloc[:, :n_years]
    info = {
        "marketCap": 500.0, "sharesOutstanding": 10.0, "totalDebt": debt, "totalCash": 20.0,
        "beta": beta, "trailingEps": 2.0, "forwardEps": 2.0, "totalRevenue": 121.0, "ebitda": ebitda,
    }
    info.update(info_overrides or {})
    data = {
        "ACME": {
            "quote": Quote(ticker="ACME", price=50.0, bid=None, ask=None, expense_ratio=None, nav=None,
                           quote_type="EQUITY", avg_volume=None),
            "info": info, "income_stmt": income, "cashflow": cash,
        }
    }
    for p, (pe, evr, eve) in {"P1": (20, 4, 14), "P2": (22, 5, 15), "P3": (24, 6, 16)}.items():
        data[p] = {"info": {"forwardPE": pe, "enterpriseToRevenue": evr, "enterpriseToEbitda": eve}}
    return FakeMarket(data)


@pytest.fixture
def fake_market_acme():
    return _acme_market()


@pytest.fixture
def fake_market_acme_neg_ebitda():
    return _acme_market(ebit_margin=-0.20, ebitda=-5.0)


@pytest.fixture
def fake_market_acme_low_beta():
    return _acme_market(beta=-0.45)  # ke = 0.045 + (-0.45)(0.055) = 0.02025 = wacc (no debt)


@pytest.fixture
def fake_market_acme_no_beta():
    return _acme_market(beta=None)


@pytest.fixture
def fake_market_acme_no_nwc():
    return _acme_market(drop=("Change In Working Capital",))
