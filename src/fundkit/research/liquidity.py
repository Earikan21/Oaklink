"""Liquidity snapshot: bid-ask spread, dollar volume, Amihud illiquidity, impact estimate, grade."""
from typing import Literal

import numpy as np
from pydantic import BaseModel

from fundkit.market import MarketData

PERIOD = "3mo"
MIN_ROWS = 3


class Liquidity(BaseModel):
    spread_pct: float | None  # fraction ((ask-bid)/mid), e.g. 0.0005 = 0.05%
    adtv_dollars: float
    amihud_e9: float
    impact_bps_1pct_adv: float
    grade: Literal["Very High", "High", "Moderate", "Low", "Very Low"]


def _grade(adtv: float) -> str:
    if adtv > 500e6:
        return "Very High"
    if adtv >= 50e6:
        return "High"
    if adtv >= 5e6:
        return "Moderate"
    if adtv >= 0.5e6:
        return "Low"
    return "Very Low"


def snapshot(ticker: str, market: MarketData) -> Liquidity:
    quote = market.quote(ticker)
    hist = market.history(ticker, PERIOD)
    if len(hist) < MIN_ROWS:
        raise ValueError("insufficient history")
    close, volume = hist["Close"], hist["Volume"]
    dollars = close * volume

    spread = None
    if quote.bid and quote.ask and quote.bid > 0 and quote.ask > 0:
        spread = (quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2)

    ret = np.log(close / close.shift(1)).dropna()  # daily log returns
    traded = ret[dollars.loc[ret.index] > 0]  # Amihud skips zero-volume days
    amihud = float((traded.abs() / dollars.loc[traded.index]).mean() * 1e9) if len(traded) else 0.0
    impact = float(ret.std() * np.sqrt(0.01) * 1e4)  # sample std (ddof=1)

    adtv = float(dollars.mean())
    return Liquidity(spread_pct=spread, adtv_dollars=adtv, amihud_e9=amihud,
                     impact_bps_1pct_adv=impact, grade=_grade(adtv))
