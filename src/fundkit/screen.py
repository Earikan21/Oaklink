"""Graham-style universe screen, quality filter, buy gate, and watchlist refresh."""
import math
from collections.abc import Iterable
from datetime import date

import pandas as pd
from pydantic import BaseModel

from fundkit.ips import buy_allowed, check_instrument
from fundkit.market import MarketData
from fundkit.models import Holding, Targets, WatchItem
from fundkit.research import liquidity, stage
from fundkit.research.correlation import max_corr_with
from fundkit.research.valuation import Valuation, value_company

UNIVERSE_QUERY = {"region": "us", "marketcap_gt": 2e9, "pe_lt": 20, "de_lt": 1.0, "eps_growth_5y_gt": 0}
STALE_DAYS = 42  # one review cycle (6 weekly runs): a valuation this old is stale
SEED_CAP = 10
SEED_THESIS = "screen: Graham universe"
MAX_PEERS = 5
SPREAD_DECIMALS = 10  # absorb float noise so a spread of exactly max_spread is not "below" it


class GateResult(BaseModel):
    passed: bool
    upside: float | None = None
    stage: int | None = None
    max_corr: float | None = None
    spread_pct: float | None = None
    reasons: list[str]
    valuation: Valuation | None = None
    confidence: str | None = None


def universe(market: MarketData) -> list[str]:
    return market.screen(UNIVERSE_QUERY)


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def quality_ok(ticker: str, market: MarketData) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    row = _fcf_row(market.cashflow(ticker))
    if row is None or not len(row):
        reasons.append("no FCF data")
    elif not _finite(row.iloc[-1]):
        reasons.append("quality data unavailable")
    elif float(row.iloc[-1]) <= 0:
        reasons.append("FCF <= 0")
    raw_de = market.info(ticker).get("debtToEquity")
    if raw_de is None:
        reasons.append("no debt/equity data")
    elif not _finite(raw_de):
        if "quality data unavailable" not in reasons:
            reasons.append("quality data unavailable")
    elif float(raw_de) / 100 >= 1.0:
        reasons.append(f"debt/equity {float(raw_de) / 100:.2f} >= 1.0")
    return (not reasons, reasons)


def _fcf_row(cashflow: pd.DataFrame) -> pd.Series | None:
    if "Free Cash Flow" not in cashflow.index:
        return None
    return cashflow.loc["Free Cash Flow"].sort_index()


def peers_for(ticker: str, market: MarketData) -> list[str]:
    try:
        sector = market.info(ticker).get("sector")
        if not sector:
            return []
        found = market.screen({"sector": sector, "region": "us", "limit": MAX_PEERS + 1})
    except Exception:  # peers are best-effort; the valuation flags `no_peers` and drops to low confidence
        return []
    return [t for t in found if t != ticker][:MAX_PEERS]


def gate(ticker: str, dollars: float, holdings: list[Holding], values: dict[str, float], targets: Targets,
         market: MarketData, peers: list[str], held_active: list[str]) -> GateResult:
    """Run every buy check (no short-circuit); `reasons` lists each failure."""
    reasons: list[str] = []

    valuation: Valuation | None = None
    try:
        valuation = value_company(ticker, market, peers)
    except ValueError:
        reasons.append("valuation impossible")
    except Exception:
        reasons.append("valuation unavailable")
    if valuation is not None and not _finite(valuation.upside):
        valuation = None
        reasons.append("valuation unavailable")
    upside = valuation.upside if valuation else None
    if upside is not None and upside < targets.min_upside:
        reasons.append(f"upside {upside:.0%} < {targets.min_upside:.0%}")
    if valuation and valuation.confidence == "low":
        reasons.append("low valuation confidence")

    stage_n: int | None = None
    try:
        stage_n = int(stage.classify(ticker, market))
    except ValueError:
        reasons.append("insufficient history for stage")
    except Exception:
        reasons.append("stage unavailable")
    if stage_n == 4:
        reasons.append("stage 4 downtrend")

    corr: float | None = None
    try:
        corr = max_corr_with(ticker, held_active, market)
        if not _finite(corr):
            corr = None
    except Exception:
        corr = None
    if corr is None:
        reasons.append("correlation unavailable")
    elif corr > targets.max_corr:
        reasons.append(f"correlation {corr:.2f} > {targets.max_corr:.2f}")

    spread: float | None = None
    try:
        spread = liquidity.snapshot(ticker, market).spread_pct
        if spread is not None and not _finite(spread):
            spread = None
    except Exception:
        spread = None
    if spread is None:
        reasons.append("spread unavailable")
    elif round(spread, SPREAD_DECIMALS) >= targets.max_spread:
        reasons.append(f"spread {spread:.2%} >= {targets.max_spread:.2%}")

    try:
        _, quality_reasons = quality_ok(ticker, market)
        reasons.extend(quality_reasons)
    except Exception:
        reasons.append("quality data unavailable")

    allowed, why = buy_allowed(ticker, dollars, holdings, values, targets)
    if not allowed:
        reasons.append(why)

    try:
        reasons.extend(check_instrument(market.quote(ticker), market.info(ticker), targets))
    except Exception:
        reasons.append("instrument data unavailable")

    return GateResult(passed=not reasons, upside=upside, stage=stage_n, max_corr=corr, spread_pct=spread,
                      reasons=reasons, valuation=valuation, confidence=valuation.confidence if valuation else None)


def _needs_revalue(item: WatchItem, market: MarketData, targets: Targets, today: date) -> bool:
    if item.last_valued is None or item.fair_value is None or item.upside is None:
        return True
    if item.fair_value <= 0 or 1 + item.upside <= 0:
        return True  # cannot derive a price at valuation; revalue instead of dividing
    if (today - item.last_valued).days >= STALE_DAYS:
        return True
    price_then = item.fair_value / (1 + item.upside)
    return abs(market.quote(item.ticker).price / price_then - 1) > targets.reval_move


def refresh_watchlist(watch: list[WatchItem], market: MarketData, targets: Targets, today: date,
                      force: bool = False) -> list[WatchItem]:
    """Revalue stale or moved items (`force`: every item). A failed valuation keeps the item unchanged."""
    out: list[WatchItem] = []
    for item in watch:
        try:
            if not force and not _needs_revalue(item, market, targets, today):
                out.append(item.model_copy())
                continue
            v = value_company(item.ticker, market, peers_for(item.ticker, market))
        except Exception:
            out.append(item.model_copy())  # stale date stays visible in the memo
            continue
        status = item.status
        if status not in ("held", "rejected"):
            status = "passed" if v.upside >= targets.min_upside else "candidate"
        out.append(item.model_copy(update={"fair_value": v.blended, "upside": v.upside,
                                           "last_valued": today, "status": status}))
    return out


def seed_candidates(watch: list[WatchItem], market: MarketData, targets: Targets, cap: int = SEED_CAP, *,
                    held: Iterable[str] = ()) -> list[WatchItem]:
    """Top up the watchlist to `cap` items with screened names that pass `quality_ok`, as candidates.

    Skips names already on the watchlist, `held` tickers and the index/bond ETF. A quality check that errors
    skips that name; a universe (screener) failure propagates for the caller to note. Returns a new list.
    """
    out = list(watch)
    if len(out) >= cap:
        return out
    skip = {w.ticker for w in watch} | set(held) | {targets.index_etf, targets.bond_etf}
    for ticker in universe(market):
        if len(out) >= cap:
            break
        if ticker in skip:
            continue
        skip.add(ticker)
        try:
            ok, _ = quality_ok(ticker, market)
        except Exception:
            continue
        if ok:
            out.append(WatchItem(ticker=ticker, thesis=SEED_THESIS, last_valued=None, fair_value=None,
                                 upside=None, status="candidate"))
    return out
