"""Market-data feed: dump everything a weekly run needs into one JSON file (where Yahoo Finance is
reachable), and load it back as a FakeMarket (where it is not).

File format (`feed/market.json`): one object per ticker with the MarketData method names as keys —
`quote` (dict), `info` (dict), and DataFrames as {"__df__": <pandas orient="split" JSON>} — plus
`"__screen__"` (universe tickers), `"__screens__"` (exact query → result, for peer lookups) and
`"__meta__"` ({generated, tickers, errors}). Methods that failed for a ticker are simply absent, so
the package fails closed on them exactly as it does for a live MarketUnavailable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from fundkit.market import MarketData, MarketUnavailable
from fundkit.models import Quote
from fundkit.screen import MAX_PEERS, UNIVERSE_QUERY, peer_query

FRAME_METHODS = ("income_stmt", "cashflow", "balance_sheet", "earnings_estimate", "eps_trend",
                 "eps_revisions", "earnings_dates")
LONG_HISTORY = "5y"   # held names, index and bond ETF: the benchmark runs from inception
SHORT_HISTORY = "2y"  # screen candidates and peers: stage/correlation need ~1 year


def _is_dt(axis) -> bool:
    return isinstance(axis, pd.DatetimeIndex)


def _labels(axis) -> list:
    if _is_dt(axis):
        a = axis.tz_convert("UTC").tz_localize(None) if axis.tz is not None else axis
        return [t.isoformat() for t in a]
    return [x if isinstance(x, (str, int, float)) else str(x) for x in axis]


def _frame_to_json(df: pd.DataFrame) -> dict:
    """Explicit labels + cell values, with which axes were datetimes (and the index tz)."""
    return {"__df__": True,
            "index": _labels(df.index), "columns": _labels(df.columns),
            "data": [[_jsonable(v) for v in row] for row in df.itertuples(index=False, name=None)],
            "index_dt": _is_dt(df.index), "columns_dt": _is_dt(df.columns),
            "index_tz": str(df.index.tz) if _is_dt(df.index) and df.index.tz is not None else None}


def frame_from_json(spec: dict) -> pd.DataFrame:
    index = pd.Index(spec["index"])
    columns = pd.Index(spec["columns"])
    if spec.get("index_dt"):
        index = pd.to_datetime(index, format="ISO8601")
        if spec.get("index_tz"):
            index = index.tz_localize("UTC").tz_convert(spec["index_tz"])
    if spec.get("columns_dt"):
        columns = pd.to_datetime(columns, format="ISO8601")
    df = pd.DataFrame(spec["data"], index=index, columns=columns)
    for c in df.columns:
        if df[c].dtype == object:
            try:
                df[c] = pd.to_numeric(df[c])
            except (ValueError, TypeError):
                pass
    return df


def _jsonable(v):
    if isinstance(v, (pd.Timestamp,)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, bool)) or v is None:
        return v
    if isinstance(v, float):
        return v if v == v and abs(v) != float("inf") else None
    if hasattr(v, "item"):  # numpy scalar
        return _jsonable(v.item())
    return str(v)


def screen_key(query: dict) -> str:
    return json.dumps(query, sort_keys=True, default=str)


def dump(market: MarketData, core: list[str], extra: list[str], out: Path,
         universe_limit: int = 30, log=print) -> dict:
    """Write the feed for `core` (long history; held, index, bond) and `extra` tickers, plus the Graham
    universe (first `universe_limit`) and each name's peer screen. Returns the meta block."""
    errors: list[str] = []
    data: dict = {}

    def grab(ticker: str, method: str, fn):
        try:
            data.setdefault(ticker, {})[method] = fn()
        except MarketUnavailable as e:
            errors.append(f"{ticker}.{method}: {e}")
        except Exception as e:  # yfinance raises many things; the run fails closed on the missing key
            errors.append(f"{ticker}.{method}: {type(e).__name__}: {e}")

    try:
        universe = list(market.screen(UNIVERSE_QUERY))[:universe_limit]
    except Exception as e:
        universe, _ = [], errors.append(f"screen: {type(e).__name__}: {e}")
    data["__screen__"] = universe
    data["__screens__"] = {}

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for t in core:
        if t and t not in seen:
            ordered.append((t, LONG_HISTORY)); seen.add(t)
    for t in list(extra) + universe:
        if t and t not in seen:
            ordered.append((t, SHORT_HISTORY)); seen.add(t)

    peers_needed: set[str] = set()
    for ticker, period in ordered:
        log(f"feed: {ticker}")
        grab(ticker, "quote", lambda: market.quote(ticker).model_dump())
        grab(ticker, "info", lambda: _jsonable(market.info(ticker)))
        grab(ticker, "history", lambda: _frame_to_json(market.history(ticker, period=period)))
        for m in FRAME_METHODS:
            grab(ticker, m, lambda m=m: _frame_to_json(getattr(market, m)(ticker)))
        q = peer_query(data.get(ticker, {}).get("info") or {})
        if q:
            try:
                found = [p for p in market.screen(q) if p != ticker][:MAX_PEERS]
                data["__screens__"][screen_key(q)] = found
                peers_needed.update(found)
            except Exception as e:
                errors.append(f"{ticker}.peers: {type(e).__name__}: {e}")
    for p in sorted(peers_needed - seen):
        log(f"feed: peer {p}")
        grab(p, "info", lambda: _jsonable(market.info(p)))
        grab(p, "quote", lambda: market.quote(p).model_dump())

    meta = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "core": core, "extra": extra, "universe": universe, "errors": errors}
    data["__meta__"] = meta
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))
    return meta


def load(path: Path) -> dict:
    """Decode a feed file into the dict FakeMarket takes."""
    raw = json.loads(Path(path).read_text())
    data: dict = {}
    for ticker, entry in raw.items():
        if ticker in ("__screen__",):
            data[ticker] = list(entry)
        elif ticker in ("__screens__", "__meta__"):
            data[ticker] = entry
        else:
            e = {}
            for k, v in dict(entry).items():
                if k == "quote":
                    e[k] = Quote(**v)
                elif isinstance(v, dict) and "__df__" in v:
                    e[k] = frame_from_json(v)
                elif k == "history" and isinstance(v, dict) and "closes" in v:
                    from fundkit.fake_market import _history_frame
                    e[k] = _history_frame(v)
                else:
                    e[k] = v
            data[ticker] = e
    return data
