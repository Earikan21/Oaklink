"""Graham-style universe screen, quality filter, buy gate, and watchlist refresh."""
from datetime import date

import pandas as pd
from pydantic import BaseModel

from fundkit.ips import buy_allowed, check_instrument
from fundkit.market import MarketData, MarketUnavailable
from fundkit.models import Holding, Targets, WatchItem
from fundkit.research import liquidity, stage
from fundkit.research.correlation import max_corr_with
from fundkit.research.valuation import Valuation, value_company

UNIVERSE_QUERY = {"region": "us", "marketcap_gt": 2e9, "pe_lt": 20, "de_lt": 1.0, "eps_growth_5y_gt": 0}
STALE_DAYS = 42
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


def quality_ok(ticker: str, market: MarketData) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    fcf = _latest_fcf(market.cashflow(ticker))
    if fcf is None:
        reasons.append("no FCF data")
    elif fcf <= 0:
        reasons.append("FCF <= 0")
    raw_de = market.info(ticker).get("debtToEquity")
    if raw_de is None:
        reasons.append("no debt/equity data")
    elif raw_de / 100 >= 1.0:
        reasons.append(f"debt/equity {raw_de / 100:.2f} >= 1.0")
    return (not reasons, reasons)


def _latest_fcf(cashflow: pd.DataFrame) -> float | None:
    if "Free Cash Flow" not in cashflow.index:
        return None
    row = cashflow.loc["Free Cash Flow"].dropna().sort_index()
    return float(row.iloc[-1]) if len(row) else None


def peers_for(ticker: str, market: MarketData) -> list[str]:
    try:
        industry = market.info(ticker).get("industry")
        if not industry:
            return []
        found = market.screen({"industry": industry, "limit": MAX_PEERS + 1})
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
    except MarketUnavailable:
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
    except MarketUnavailable:
        reasons.append("stage unavailable")
    if stage_n == 4:
        reasons.append("stage 4 downtrend")

    corr: float | None = None
    try:
        corr = max_corr_with(ticker, held_active, market)
    except (ValueError, MarketUnavailable):
        reasons.append("correlation unavailable")
    if corr is not None and corr > targets.max_corr:
        reasons.append(f"correlation {corr:.2f} > {targets.max_corr:.2f}")

    spread: float | None = None
    try:
        spread = liquidity.snapshot(ticker, market).spread_pct
    except (ValueError, MarketUnavailable):
        pass
    if spread is None:
        reasons.append("spread unavailable")
    elif round(spread, SPREAD_DECIMALS) >= targets.max_spread:
        reasons.append(f"spread {spread:.2%} >= {targets.max_spread:.2%}")

    try:
        _, quality_reasons = quality_ok(ticker, market)
        reasons.extend(quality_reasons)
    except MarketUnavailable:
        reasons.append("quality data unavailable")

    allowed, why = buy_allowed(ticker, dollars, holdings, values, targets)
    if not allowed:
        reasons.append(why)

    try:
        reasons.extend(check_instrument(market.quote(ticker), market.info(ticker), targets))
    except MarketUnavailable:
        reasons.append("instrument data unavailable")

    return GateResult(passed=not reasons, upside=upside, stage=stage_n, max_corr=corr, spread_pct=spread,
                      reasons=reasons, valuation=valuation, confidence=valuation.confidence if valuation else None)


def _needs_revalue(item: WatchItem, market: MarketData, targets: Targets, today: date) -> bool:
    if item.last_valued is None or item.fair_value is None or item.upside is None:
        return True
    if (today - item.last_valued).days > STALE_DAYS:
        return True
    price_then = item.fair_value / (1 + item.upside)
    return abs(market.quote(item.ticker).price / price_then - 1) > targets.reval_move


def refresh_watchlist(watch: list[WatchItem], market: MarketData, targets: Targets, today: date) -> list[WatchItem]:
    out: list[WatchItem] = []
    for item in watch:
        try:
            if not _needs_revalue(item, market, targets, today):
                out.append(item.model_copy())
                continue
            v = value_company(item.ticker, market, peers_for(item.ticker, market))
        except (ValueError, MarketUnavailable):
            out.append(item.model_copy())  # stale date stays visible in the memo
            continue
        status = item.status
        if status not in ("held", "rejected"):
            status = "passed" if v.upside >= targets.min_upside else "candidate"
        out.append(item.model_copy(update={"fair_value": v.blended, "upside": v.upside,
                                           "last_valued": today, "status": status}))
    return out
