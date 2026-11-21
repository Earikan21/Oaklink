"""Performance snapshot and the passive 50/30/20 benchmark.

A ledger row counts as executed only when ``response == "applied"`` (the reconcile
step sets it once the row reached Holdings); ``done`` rows are not counted.
"""
from datetime import date, timedelta

import pandas as pd

from fundkit.market import MarketData, MarketUnavailable
from fundkit.models import Holding, LedgerRow, Snapshot, Targets
from fundkit.portfolio import value_holdings
from fundkit.store import Store

REBALANCE_EVERY_DAYS = 42


def _daily_close(market: MarketData, ticker: str, today: date) -> pd.Series:
    """Close series on every calendar day (non-trading days forward-filled) through `today`."""
    close = market.history(ticker, period="max")["Close"].dropna()
    close.index = pd.DatetimeIndex(close.index).normalize()
    end = max(close.index[-1], pd.Timestamp(today))
    return close.reindex(pd.date_range(close.index[0], end, freq="D")).ffill()


def benchmark_value(start_value: float, start_date: date, today: date, market: MarketData,
                    targets: Targets, cash_yield: float = 0.04) -> float:
    """Value of a passive portfolio (targets.stocks in index ETF, bonds in bond ETF, cash accruing
    `cash_yield`/365 per calendar day) started at `start_date` and rebalanced to targets every 42 days."""
    idx = _daily_close(market, targets.index_etf, today)
    bnd = _daily_close(market, targets.bond_etf, today)
    start = pd.Timestamp(start_date)
    if start < idx.index[0] or start < bnd.index[0]:
        raise ValueError("insufficient history for benchmark")
    days = (today - start_date).days
    daily = 1.0 + cash_yield / 365.0

    def allocate(total: float, day: pd.Timestamp) -> tuple[float, float, float]:
        return (targets.stocks * total / idx[day], targets.bonds * total / bnd[day], targets.cash * total)

    idx_units, bnd_units, cash = allocate(start_value, start)
    total = start_value
    for i in range(1, days + 1):
        day = start + pd.Timedelta(days=i)
        cash *= daily
        total = idx_units * idx[day] + bnd_units * bnd[day] + cash
        if i % REBALANCE_EVERY_DAYS == 0:
            idx_units, bnd_units, cash = allocate(total, day)
    return float(total)


def active_vs_index(ledger: list[LedgerRow], holdings: list[Holding], values: dict[str, float],
                    market: MarketData, targets: Targets) -> float | None:
    """Sum over applied active-name BUYs of (current value of the shares) - (same dollars in the index
    ETF from the fill date). Rows whose ticker is no longer held (sold) or cannot be priced are skipped;
    returns None when no row contributes. "Today" is the latest index close available."""
    passive = {targets.index_etf, targets.bond_etf}
    index_close = None
    total = 0.0
    counted = False
    for row in ledger:
        if (row.action != "BUY" or row.response != "applied" or row.ticker in passive
                or row.fill_shares is None or row.fill_price is None):
            continue
        if row.ticker not in values:
            continue
        try:
            price = market.quote(row.ticker).price
        except MarketUnavailable:
            continue
        if index_close is None:
            index_close = _daily_close(market, targets.index_etf, date.min)
        fill_ts = pd.Timestamp(row.date)
        if fill_ts < index_close.index[0]:
            continue
        counterfactual = row.fill_shares * row.fill_price * index_close.iloc[-1] / index_close[fill_ts]
        total += row.fill_shares * price - counterfactual
        counted = True
    return total if counted else None


def turnover_qtd(ledger: list[LedgerRow], today: date, total: float) -> float:
    """Applied BUY/SELL dollars since the start of the current calendar quarter, as a fraction of `total`."""
    if total <= 0:
        return 0.0
    quarter_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    traded = sum(abs(r.fill_shares * r.fill_price) for r in ledger
                 if r.action in ("BUY", "SELL") and r.response == "applied"
                 and r.fill_shares is not None and r.fill_price is not None
                 and quarter_start <= r.date <= today)
    return traded / total


def snapshot(store: Store, market: MarketData, targets: Targets, today: date, inception: date,
             inception_value: float) -> Snapshot:
    """Point-in-time Snapshot. Does not append it to the store (the caller does)."""
    holdings = store.load_holdings()
    ledger = store.load_ledger()
    values, _unpriced = value_holdings(holdings, market)
    sleeve_of: dict[str, str] = {}
    for h in holdings:
        sleeve_of.setdefault(h.ticker, h.sleeve)
    by_sleeve = {"indexed_equity": 0.0, "active": 0.0, "bonds": 0.0, "cash": 0.0}
    for ticker, value in values.items():
        by_sleeve[sleeve_of[ticker]] += value
    total = sum(values.values())
    return Snapshot(
        date=today, total=total,
        indexed_equity=by_sleeve["indexed_equity"], active=by_sleeve["active"],
        bonds=by_sleeve["bonds"], cash=by_sleeve["cash"],
        bench_total=benchmark_value(inception_value, inception, today, market, targets),
        active_vs_index=active_vs_index(ledger, holdings, values, market, targets),
        turnover_qtd=turnover_qtd(ledger, today, total),
    )
