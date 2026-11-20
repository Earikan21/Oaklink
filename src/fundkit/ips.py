"""IPS rules: instrument screening, compliance flags, and pre-trade buy checks."""
import re

from pydantic import BaseModel

from fundkit.models import Holding, Quote, Targets
from fundkit.portfolio import EPS, name_weights, outside_band, sleeve_weights

ALLOWED_TYPES = {"ETF", "MUTUALFUND", "EQUITY"}
# Matched as whole words, case-insensitively, except "ultra", which matches as a word prefix
# (UltraPro, UltraShort are leveraged; "Ultra Short-Term Bond" false-positives by design). "short" is deliberately omitted: it would
# false-positive on names like "Short-Term Treasury"; inverse/bear funds are caught by
# "inverse"/"bear" instead.
DENIED_KEYWORDS = ("leveraged", "2x", "3x", "ultra", "inverse", "bear")


class ComplianceReport(BaseModel):
    sleeve_flags: list[str] = []
    active_ceiling_flag: str | None = None
    trim_flags: list[str] = []
    indexed_lt_active: bool = False
    unpriced: list[str] = []


def check_instrument(q: Quote, info: dict, targets: Targets = Targets()) -> list[str]:
    violations: list[str] = []
    if q.expense_ratio is not None and q.expense_ratio >= targets.fee_cap:
        violations.append(f"expense ratio {q.expense_ratio * 100:.2f}% >= {targets.fee_cap * 100:.2f}% cap")
    if q.quote_type not in ALLOWED_TYPES:
        violations.append(f"quote type {q.quote_type} not allowed")
    name = f"{info.get('longName') or ''} {info.get('shortName') or ''}".lower()
    for kw in DENIED_KEYWORDS:
        tail = "" if kw == "ultra" else "(?![a-z0-9])"
        if re.search(rf"(?<![a-z0-9]){re.escape(kw)}{tail}", name):
            violations.append(f"denied keyword '{kw}' (leveraged/inverse product) in name")
    return violations


def compliance(holdings: list[Holding], values: dict[str, float], targets: Targets, review: bool,
               unpriced: list[str] = []) -> ComplianceReport:
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
    trim_flags = [f"{t} {wt * 100:.1f}% > {thr * 100:.1f}% trim threshold"
                  for t, wt in sorted(name_weights(holdings, values).items()) if wt > thr + EPS]
    return ComplianceReport(
        sleeve_flags=sleeve_flags, active_ceiling_flag=ceiling_flag, trim_flags=trim_flags,
        indexed_lt_active=w["indexed_equity"] < w["active"], unpriced=list(unpriced))


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
