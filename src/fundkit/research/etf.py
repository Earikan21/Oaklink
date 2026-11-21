"""ETF premium/discount to NAV versus the quoted bid-ask spread."""
from pydantic import BaseModel

from fundkit.market import MarketData

FALLBACK_THRESHOLD_PCT = 0.25  # used when no bid/ask is available


class EtfPremium(BaseModel):
    price: float
    nav: float
    premium_pct: float  # PERCENT, e.g. 0.40 = +0.40%
    spread_pct: float | None  # PERCENT in this module (unlike liquidity.Liquidity, which uses a fraction)
    meaningful: bool


def premium(ticker: str, market: MarketData) -> EtfPremium:
    q = market.quote(ticker)
    if q.quote_type != "ETF":
        raise ValueError(f"{ticker} is not an ETF (quote_type={q.quote_type})")
    if q.nav is None or q.nav <= 0:
        raise ValueError(f"{ticker} has no usable NAV")
    prem = (q.price - q.nav) / q.nav * 100
    spread = None
    if q.bid and q.ask and q.bid > 0 and q.ask > 0:
        spread = (q.ask - q.bid) / ((q.ask + q.bid) / 2) * 100
    threshold = spread if spread is not None else FALLBACK_THRESHOLD_PCT
    return EtfPremium(price=q.price, nav=q.nav, premium_pct=prem, spread_pct=spread,
                      meaningful=abs(prem) > threshold)
