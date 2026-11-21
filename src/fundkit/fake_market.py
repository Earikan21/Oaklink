"""Offline MarketData double, shipped in the package so the CLI can rehearse a run without network."""
import json
from importlib.resources import files
from pathlib import Path

import pandas as pd

from fundkit.market import MarketUnavailable
from fundkit.models import Quote

EXAMPLE_JSON = "fake_market.json"


def packaged_example() -> Path:
    return Path(str(files("fundkit") / "examples" / EXAMPLE_JSON))


def _history_frame(spec: dict) -> pd.DataFrame:
    closes = [float(c) for c in spec["closes"]]
    idx = pd.bdate_range(start=spec["start"], periods=len(closes))
    close = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": float(spec.get("volume", 0))}, index=idx)


class FakeMarket:
    """MarketData backed by dicts. `data[ticker]` may hold keys: quote, history,
    info, income_stmt, cashflow, balance_sheet, earnings_estimate, eps_trend,
    eps_revisions, earnings_dates. Unknown ticker or missing key raises
    MarketUnavailable. `data["__screen__"]` is returned for ANY screen query
    (default []); the query itself is ignored.
    """

    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def from_json(cls, path) -> "FakeMarket":
        raw = json.loads(Path(path).read_text())
        data: dict = {}
        for ticker, entry in raw.items():
            if ticker == "__screen__":
                data[ticker] = list(entry)
                continue
            entry = dict(entry)
            if "quote" in entry:
                entry["quote"] = Quote(**entry["quote"])
            if "history" in entry:
                entry["history"] = _history_frame(entry["history"])
            data[ticker] = entry
        return cls(data)

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
