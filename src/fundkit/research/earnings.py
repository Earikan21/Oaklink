"""Earnings calendar helpers: upcoming report dates and recent reports with price reaction."""
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel

from fundkit.market import MarketData, MarketUnavailable

ET = ZoneInfo("America/New_York")
AFTER_CLOSE_HOUR = 16  # reports at/after 16:00 ET react in the next session


class EarningsResult(BaseModel):
    report_ts: datetime  # tz-aware, US/Eastern
    eps_est: float | None
    eps_actual: float | None
    surprise_pct: float | None
    reaction_pct: float | None  # None when the pre/post close is unavailable (e.g. session not closed yet)


def _eastern_rows(market: MarketData, ticker: str) -> pd.DataFrame:
    """earnings_dates re-indexed to US/Eastern; empty frame when unavailable."""
    try:
        df = market.earnings_dates(ticker)
    except MarketUnavailable:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize(ET) if idx.tz is None else idx.tz_convert(ET)
    return df.set_axis(idx)


def _finite(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None



def upcoming_within(ticker: str, market: MarketData, today: date, days: int = 7) -> datetime | None:
    """Earliest not-yet-reported earnings timestamp (ET) dated in [today, today+days]."""
    df = _eastern_rows(market, ticker)
    if df.empty or "Reported EPS" not in df.columns:
        return None
    et_date = [ts.date() for ts in df.index]
    keep = [
        today <= d <= today + timedelta(days=days) and pd.isna(rep)
        for d, rep in zip(et_date, df["Reported EPS"])
    ]
    hits = df.index[keep]
    return None if len(hits) == 0 else min(hits).to_pydatetime()


def _surprise(row: pd.Series, est: float | None, actual: float | None) -> float | None:
    reported = _finite(row.get("Surprise(%)"))
    if reported is not None:
        return reported
    if est is None or actual is None or est == 0:
        return None
    return (actual - est) / abs(est) * 100


def _reaction(ticker: str, market: MarketData, report_ts: datetime) -> float | None:
    """Percent move across the report. Compared on calendar dates: the history index is
    tz-naive exchange-local dates, so report_ts is taken in ET and reduced to its date."""
    try:
        hist = market.history(ticker, period="1mo")
    except MarketUnavailable:
        return None
    close = hist["Close"].dropna()
    dates = pd.Series([ts.date() for ts in close.index], index=close.index)
    report_date = report_ts.date()
    if report_ts.hour >= AFTER_CLOSE_HOUR:
        pre, post = close[dates <= report_date], close[dates > report_date]
    else:
        pre, post = close[dates < report_date], close[dates >= report_date]
    if pre.empty or post.empty:
        return None
    pre_close, post_close = float(pre.iloc[-1]), float(post.iloc[0])
    if pre_close == 0:
        return None
    return (post_close / pre_close - 1) * 100


def reported_within(ticker: str, market: MarketData, today: date, days: int = 7) -> EarningsResult | None:
    """Most recent report (non-NaN Reported EPS) dated in [today-days, today], with price reaction."""
    df = _eastern_rows(market, ticker)
    if df.empty or "Reported EPS" not in df.columns:
        return None
    keep = [
        today - timedelta(days=days) <= ts.date() <= today and not pd.isna(rep)
        for ts, rep in zip(df.index, df["Reported EPS"])
    ]
    window = df[keep]
    if window.empty:
        return None
    ts = max(window.index)
    row = window.loc[ts]
    report_ts = ts.to_pydatetime()
    est, actual = _finite(row.get("EPS Estimate")), _finite(row.get("Reported EPS"))
    return EarningsResult(
        report_ts=report_ts,
        eps_est=est,
        eps_actual=actual,
        surprise_pct=_surprise(row, est, actual),
        reaction_pct=_reaction(ticker, market, report_ts),
    )
