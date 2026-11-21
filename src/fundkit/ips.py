"""IPS rules: instrument screening, compliance flags, and pre-trade buy checks."""
import re

from pydantic import BaseModel

from fundkit.market import MarketData
from fundkit.models import Holding, Quote, Targets
from fundkit.portfolio import CASH_TICKER, EPS, name_weights, outside_band, sleeve_weights

ALLOWED_TYPES = {"ETF", "MUTUALFUND", "EQUITY"}
FUND_TYPES = {"ETF", "MUTUALFUND"}
# Matched as whole words, case-insensitively, except "ultra", which matches as a word prefix
# (UltraPro, UltraShort are leveraged; "Ultra Short-Term Bond" false-positives by design). "short" is deliberately omitted: it would
# false-positive on names like "Short-Term Treasury"; inverse/bear funds are caught by
# "inverse"/"bear" instead.
DENIED_KEYWORDS = ("leveraged", "2x", "3x", "ultra", "inverse", "bear")


class ComplianceReport(BaseModel):
    sleeve_flags: list[str] = []
    active_ceiling_flag: str | None = None
    trim_flags: list[str] = []
    trim_tickers: list[str] = []  # same names, same order as trim_flags
    indexed_lt_active: bool = False
    unpriced: list[str] = []
    fund_flags: list[str] = []  # "<ticker>: <violation>" for held non-equity instruments


def check_instrument(q: Quote, info: dict, targets: Targets = Targets()) -> list[str]:
    violations: list[str] = []
    if q.expense_ratio is None:
        if q.quote_type in FUND_TYPES:  # a fund whose fee cannot be verified fails closed
            violations.append("expense ratio unavailable")
    elif q.expense_ratio >= targets.fee_cap:
        violations.append(f"expense ratio {q.expense_ratio * 100:.2f}% >= {targets.fee_cap * 100:.2f}% cap")
    if q.quote_type not in ALLOWED_TYPES:
        violations.append(f"quote type {q.quote_type} not allowed")
    name = f"{info.get('longName') or ''} {info.get('shortName') or ''}".lower()
    for kw in DENIED_KEYWORDS:
        tail = "" if kw == "ultra" else "(?![a-z0-9])"
        if re.search(rf"(?<![a-z0-9]){re.escape(kw)}{tail}", name):
            violations.append(f"denied keyword '{kw}' (leveraged/inverse product) in name")
    return violations


def instrument_violations(ticker: str, market: MarketData, targets: Targets) -> list[str]:
    """`check_instrument` on live data; a quote or info failure is itself a violation."""
    try:
        q, info = market.quote(ticker), market.info(ticker)
    except Exception:
        return ["instrument data unavailable"]
    return check_instrument(q, info, targets)


def _fund_flags(holdings: list[Holding], market: MarketData, targets: Targets) -> list[str]:
    """Instrument violations for every held non-equity ticker. Unquotable tickers are skipped here: they are
    already reported as unpriced."""
    flags: list[str] = []
    for t in dict.fromkeys(h.ticker for h in holdings if h.ticker != CASH_TICKER):
        try:
            q = market.quote(t)
        except Exception:
            continue
        if q.quote_type == "EQUITY":
            continue
        try:
            violations = check_instrument(q, market.info(t), targets)
        except Exception:
            violations = ["instrument data unavailable"]
        flags.extend(f"{t}: {v}" for v in violations)
    return flags


def compliance(holdings: list[Holding], values: dict[str, float], targets: Targets, review: bool,
               unpriced: list[str] = [], market: MarketData | None = None) -> ComplianceReport:
    """Band, ceiling and trim flags; with `market`, also instrument checks on held funds (`fund_flags`)."""
    w = sleeve_weights(holdings, values)
    stocks = w["indexed_equity"] + w["active"]
    current = {"stocks": stocks, "bonds": w["bonds"], "cash": w["cash"]}
    target = {"stocks": targets.stocks, "bonds": targets.bonds, "cash": targets.cash}

    sleeve_flags = [
        f"{s} {current[s] * 100:.1f}% vs {target[s] * 100:.0f}% ({(current[s] - target[s]) * 100:+.1f} pts)"
        for s in ("stocks", "bonds", "cash")
        if outside_band(current[s], target[s], targets.band)
    ]
    ceiling_flag = (f"active {w['active'] * 100:.1f}% > {targets.active_ceiling * 100:.0f}% ceiling"
                    if w["active"] > targets.active_ceiling + EPS else None)
    thr = targets.review_trim if review else targets.trim_threshold
    over = [(t, wt) for t, wt in sorted(name_weights(holdings, values).items()) if wt > thr + EPS]
    trim_flags = [f"{t} {wt * 100:.1f}% > {thr * 100:.1f}% trim threshold" for t, wt in over]
    return ComplianceReport(
        sleeve_flags=sleeve_flags, active_ceiling_flag=ceiling_flag, trim_flags=trim_flags,
        trim_tickers=[t for t, _ in over],
        indexed_lt_active=w["indexed_equity"] < w["active"], unpriced=list(unpriced),
        fund_flags=_fund_flags(holdings, market, targets) if market is not None else [])


def buy_allowed(ticker: str, dollars: float, holdings: list[Holding], values: dict[str, float],
                targets: Targets) -> tuple[bool, str]:
    """Check a prospective buy of an active name; total is assumed unchanged (funded from cash/index ETF)."""
    if ticker in (targets.index_etf, targets.bond_etf):
        return True, "ok"
    total = sum(values.values())
    if total == 0:
        return False, "portfolio has no value"
    name_pct = (values.get(ticker, 0.0) + dollars) / total
    if name_pct > targets.name_cap + EPS:
        return False, f"would be {name_pct * 100:.1f}% > {targets.name_cap * 100:.0f}% per-name cap"
    w = sleeve_weights(holdings, values)
    active_pct = w["active"] + dollars / total
    if active_pct > targets.active_ceiling + EPS:
        return False, f"active sleeve would be {active_pct * 100:.1f}% > {targets.active_ceiling * 100:.0f}% ceiling"
    if w["indexed_equity"] < active_pct:
        return False, "indexed equity would trail active sleeve"
    return True, "ok"
