"""Analyst-estimate revisions: EPS trend direction, revision breadth, recent beat rate."""
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from fundkit.market import MarketData, MarketUnavailable

PERIOD = "0y"
DIRECTION_THRESHOLD = 0.01
BEAT_WINDOW = 4


class Revisions(BaseModel):
    direction: Literal["up", "down", "flat"]
    pct_30d: float
    up_30d: int
    down_30d: int
    breadth: float | None  # up/(up+down); None when there are no revisions
    beat_rate_4q: float | None  # share of the last 4 reported quarters that beat; None if none reported


def _col(df: pd.DataFrame, name: str) -> str | None:
    """Case-insensitive column lookup (yfinance mixes `downLast7Days` / `upLast7days`)."""
    lowered = {str(c).lower(): c for c in df.columns}
    return lowered.get(name.lower())


def _cell(df: pd.DataFrame, period: str, column: str) -> float | None:
    col = _col(df, column)
    if col is None or period not in df.index:
        return None
    value = df.loc[period, col]
    return None if pd.isna(value) else float(value)


def _trend_change(trend: pd.DataFrame) -> tuple[float, Literal["up", "down", "flat"]]:
    current = _cell(trend, PERIOD, "current")
    ago = _cell(trend, PERIOD, "30daysAgo")
    if current is None or not ago:
        return 0.0, "flat"
    pct = current / ago - 1
    if pct > DIRECTION_THRESHOLD:
        return pct, "up"
    if pct < -DIRECTION_THRESHOLD:
        return pct, "down"
    return pct, "flat"


def _beat_rate(ticker: str, market: MarketData) -> float | None:
    try:
        dates = market.earnings_dates(ticker)
    except MarketUnavailable:
        return None
    reported = _col(dates, "Reported EPS")
    estimate = _col(dates, "EPS Estimate")
    if reported is None or estimate is None:
        return None
    rows = dates[[reported, estimate]].dropna().sort_index(ascending=False).head(BEAT_WINDOW)
    if rows.empty:
        return None
    return float((rows[reported] > rows[estimate]).mean())


def revision_summary(ticker: str, market: MarketData) -> Revisions:
    pct, direction = _trend_change(market.eps_trend(ticker))
    revisions = market.eps_revisions(ticker)
    up = int(_cell(revisions, PERIOD, "upLast30days") or 0)
    down = int(_cell(revisions, PERIOD, "downLast30days") or 0)
    return Revisions(
        direction=direction,
        pct_30d=pct,
        up_30d=up,
        down_30d=down,
        breadth=up / (up + down) if up + down else None,
        beat_rate_4q=_beat_rate(ticker, market),
    )
