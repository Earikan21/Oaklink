"""Market data layer: a MarketData protocol and a cached yfinance implementation."""
import math
import pickle
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Protocol

import pandas as pd
import yfinance as yf

from fundkit.models import Quote


class MarketUnavailable(Exception):
    """Raised when market data for a ticker cannot be obtained."""

    def __init__(self, ticker: str):
        super().__init__(f"market data unavailable for {ticker}")
        self.ticker = ticker


class MarketData(Protocol):
    def quote(self, ticker: str) -> Quote: ...
    def history(self, ticker: str, period: str = "1y") -> pd.DataFrame: ...
    def info(self, ticker: str) -> dict: ...
    def income_stmt(self, ticker: str) -> pd.DataFrame: ...
    def cashflow(self, ticker: str) -> pd.DataFrame: ...
    def balance_sheet(self, ticker: str) -> pd.DataFrame: ...
    def earnings_estimate(self, ticker: str) -> pd.DataFrame: ...
    def eps_trend(self, ticker: str) -> pd.DataFrame: ...
    def eps_revisions(self, ticker: str) -> pd.DataFrame: ...
    def earnings_dates(self, ticker: str) -> pd.DataFrame: ...
    def screen(self, query: dict) -> list[str]: ...


# screen() query key -> (yfinance field, operator). `sector` and `region` are equality.
# (yfinance 1.8 validates `industry` against sector names only, so peers are screened by sector.)
_SCREEN_FIELDS: dict[str, tuple[str, str]] = {
    "region": ("region", "eq"),
    "sector": ("sector", "eq"),
    "marketcap_gt": ("lastclosemarketcap.lasttwelvemonths", "gt"),
    "marketcap_lt": ("lastclosemarketcap.lasttwelvemonths", "lt"),
    "pe_lt": ("peratio.lasttwelvemonths", "lt"),
    "de_lt": ("totaldebtequity.lasttwelvemonths", "lt"),
    # Substitution: yfinance has no 5y EPS CAGR screener field, so this uses
    # trailing-twelve-month EPS growth as the closest available proxy.
    "eps_growth_5y_gt": ("epsgrowth.lasttwelvemonths", "gt"),
}
_DEFAULT_SCREEN_LIMIT = 25
US_EXCHANGES = frozenset({"NMS", "NGM", "NCM", "NYQ", "ASE", "BTS"})  # Nasdaq tiers, NYSE, NYSE American, Cboe


def build_equity_query(query: dict):
    """Map a fundkit screen dict to (yfinance EquityQuery, limit). Unknown keys raise ValueError."""
    unknown = set(query) - set(_SCREEN_FIELDS) - {"limit"}
    if unknown:
        raise ValueError(f"unknown screen keys: {sorted(unknown)}")
    operands = [
        yf.EquityQuery(op, [field, query[key]])
        for key, (field, op) in _SCREEN_FIELDS.items()
        if key in query
    ]
    if not operands:
        raise ValueError("screen query needs at least one criterion")
    q = operands[0] if len(operands) == 1 else yf.EquityQuery("and", operands)
    return q, int(query.get("limit", _DEFAULT_SCREEN_LIMIT))


def _usable_price(x) -> float | None:
    """`x` as a float if it is a finite, positive price; otherwise None."""
    try:
        p = float(x)
    except (TypeError, ValueError):
        return None
    return p if math.isfinite(p) and p > 0 else None


def _expense_ratio_fraction(raw: float | None) -> float | None:
    # yfinance quotes netExpenseRatio in percent (VTI: 0.03 means 0.03%; a 0.9% fund: 0.9).
    # Quote.expense_ratio is a fraction, so always divide by 100.
    return None if raw is None else raw / 100


class YFMarket:
    """yfinance-backed MarketData with a per-call pickle cache.

    Layout: cache_dir/<ticker>/<method>[-<argkey>].pkl holding (fetch_date, value).
    An entry is valid while the file is younger than ttl_hours (ttl_hours=0 disables caching).
    """

    def __init__(self, cache_dir: Path, ttl_hours: int = 20):
        self.cache_dir = Path(cache_dir)
        self.ttl_hours = ttl_hours

    def _cached(self, ticker: str, method: str, fetch: Callable[[], Any], argkey: str = "") -> Any:
        name = method + (f"-{re.sub(r'[^A-Za-z0-9_.]', '_', argkey)}" if argkey else "")
        path = self.cache_dir / ticker / f"{name}.pkl"
        if path.exists() and time.time() - path.stat().st_mtime < self.ttl_hours * 3600:
            with path.open("rb") as f:
                return pickle.load(f)[1]
        value = fetch()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump((date.today(), value), f)
        return value

    def quote(self, ticker: str) -> Quote:
        return self._cached(ticker, "quote", lambda: self._fetch_quote(ticker))

    def _fetch_quote(self, ticker: str) -> Quote:
        t = yf.Ticker(ticker)
        try:
            price = _usable_price(t.fast_info["lastPrice"])
        except Exception:
            price = None
        info: dict = {}
        try:
            info = t.info or {}
        except Exception:
            pass
        if price is None:  # missing, NaN, inf or <= 0: try the info price, else the ticker is unavailable
            price = _usable_price(info.get("regularMarketPrice"))
        if price is None:
            raise MarketUnavailable(ticker)
        return Quote(
            ticker=ticker,
            price=price,
            bid=info.get("bid"),
            ask=info.get("ask"),
            expense_ratio=_expense_ratio_fraction(info.get("netExpenseRatio")),
            nav=info.get("navPrice"),
            quote_type=info.get("quoteType") or "",
            avg_volume=info.get("averageVolume"),
        )

    def history(self, ticker: str, period: str = "1y") -> pd.DataFrame:
        """Daily OHLCV, tz-naive. Unadjusted (`auto_adjust=False`): Close is the traded price, consistent
        with `quote()` prices and the cost basis of lots; dividends are not folded into past closes."""
        def fetch() -> pd.DataFrame:
            h = yf.Ticker(ticker).history(period=period, auto_adjust=False)
            if h is None or h.empty:
                raise MarketUnavailable(ticker)
            h = h.copy()
            h.index = h.index.tz_localize(None)
            return h

        return self._cached(ticker, "history", fetch, argkey=period)

    def _attr(self, ticker: str, name: str):
        def fetch():
            try:
                v = getattr(yf.Ticker(ticker), name)
            except Exception as e:
                raise MarketUnavailable(ticker) from e
            if v is None:
                raise MarketUnavailable(ticker)
            return v

        return self._cached(ticker, name, fetch)

    def info(self, ticker: str) -> dict:
        return self._attr(ticker, "info")

    def income_stmt(self, ticker: str) -> pd.DataFrame:
        return self._attr(ticker, "income_stmt")

    def cashflow(self, ticker: str) -> pd.DataFrame:
        return self._attr(ticker, "cashflow")

    def balance_sheet(self, ticker: str) -> pd.DataFrame:
        return self._attr(ticker, "balance_sheet")

    def earnings_estimate(self, ticker: str) -> pd.DataFrame:
        return self._attr(ticker, "earnings_estimate")

    def eps_trend(self, ticker: str) -> pd.DataFrame:
        return self._attr(ticker, "eps_trend")

    def eps_revisions(self, ticker: str) -> pd.DataFrame:
        return self._attr(ticker, "eps_revisions")

    def earnings_dates(self, ticker: str) -> pd.DataFrame:
        return self._attr(ticker, "earnings_dates")

    def screen(self, query: dict) -> list[str]:
        """Largest-first symbols on the main US exchanges that match `query`.

        Yahoo's screener returns OTC/pink-sheet ADRs and foreign listings under region "us" and its
        default order is arbitrary, so results are sorted by market cap and filtered to US_EXCHANGES
        (oversampled 4x so the filter still returns up to `limit` names)."""
        q, limit = build_equity_query(query)
        result = yf.screen(q, size=min(4 * limit, 250), sortField="intradaymarketcap", sortAsc=False)
        quotes = (result or {}).get("quotes", [])
        return [r["symbol"] for r in quotes if r.get("exchange", "NMS") in US_EXCHANGES][:limit]
