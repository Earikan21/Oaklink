"""Portfolio math: valuation, sleeve/name weights, and band-driven rebalance proposals."""
from typing import Literal

from pydantic import BaseModel

from fundkit.market import MarketData, MarketUnavailable
from fundkit.models import Holding, Sleeve, Targets

SLEEVES: tuple[Sleeve, ...] = ("indexed_equity", "active", "bonds", "cash")
CASH_TICKER = "CASH"


class Proposal(BaseModel):
    action: Literal["BUY", "SELL"]
    ticker: str
    dollars: float
    shares: float | None = None  # set only for whole-share (bond ETF) proposals
    reason: str
    est_tax: float | None = None


def value_holdings(holdings: list[Holding], market: MarketData) -> tuple[dict[str, float], list[str]]:
    """Return ({ticker: market_value}, unpriced tickers). Lots of one ticker are summed.

    CASH is valued at its dollar amount and never priced.
    """
    values: dict[str, float] = {}
    unpriced: list[str] = []
    prices: dict[str, float] = {}
    for h in holdings:
        if h.ticker == CASH_TICKER:
            values[CASH_TICKER] = values.get(CASH_TICKER, 0.0) + h.shares
            continue
        if h.ticker in unpriced:
            continue
        if h.ticker not in prices:
            try:
                prices[h.ticker] = market.quote(h.ticker).price
            except MarketUnavailable:
                unpriced.append(h.ticker)
                continue
        values[h.ticker] = values.get(h.ticker, 0.0) + h.shares * prices[h.ticker]
    return values, unpriced


def sleeve_weights(holdings: list[Holding], values: dict[str, float]) -> dict[Sleeve, float]:
    """Fraction of total (sum of values) in each sleeve; all zeros when total is 0."""
    total = sum(values.values())
    out: dict[Sleeve, float] = {s: 0.0 for s in SLEEVES}
    if total == 0:
        return out
    sleeve_of: dict[str, Sleeve] = {}
    for h in holdings:
        sleeve_of.setdefault(h.ticker, h.sleeve)
    for ticker, value in values.items():
        if ticker in sleeve_of:
            out[sleeve_of[ticker]] += value / total
    return out


def name_weights(holdings: list[Holding], values: dict[str, float]) -> dict[str, float]:
    """Weight of each active-sleeve ticker as a fraction of the total portfolio."""
    total = sum(values.values())
    if total == 0:
        return {}
    active = {h.ticker for h in holdings if h.sleeve == "active"}
    return {t: values[t] / total for t in active if t in values}


def whole_share_target(dollars: float, price: float) -> int:
    return max(0, round(dollars / price))


def rebalance_proposals(holdings: list[Holding], values: dict[str, float], targets: Targets,
                        prices: dict[str, float], review: bool) -> list[Proposal]:
    """Propose trades only for sleeves outside the band.

    Stocks are corrected via the index ETF (active names are never touched); bonds via whole
    shares of the bond ETF, ignoring gaps of one share or less; cash is the residual.
    `review` is accepted for a uniform call signature; rebalancing does not depend on it.
    """
    total = sum(values.values())
    if total == 0:
        return []
    w = sleeve_weights(holdings, values)
    stocks = w["indexed_equity"] + w["active"]
    props: list[Proposal] = []

    if abs(stocks - targets.stocks) > targets.band + 1e-9:
        gap = (targets.stocks - stocks) * total
        props.append(Proposal(
            action="BUY" if gap > 0 else "SELL", ticker=targets.index_etf, dollars=abs(gap),
            reason=f"rebalance stocks {stocks * 100:.1f}% → {targets.stocks * 100:.0f}%"))

    if abs(w["bonds"] - targets.bonds) > targets.band + 1e-9:
        gap = (targets.bonds - w["bonds"]) * total
        price = prices[targets.bond_etf]
        shares = whole_share_target(abs(gap), price)
        if shares > 1:
            if gap < 0:
                held = sum(h.shares for h in holdings if h.ticker == targets.bond_etf)
                shares = min(shares, int(held))
            props.append(Proposal(
                action="BUY" if gap > 0 else "SELL", ticker=targets.bond_etf, dollars=shares * price,
                shares=shares, reason=f"rebalance bonds {w['bonds'] * 100:.1f}% → {targets.bonds * 100:.0f}%"))
    return props
