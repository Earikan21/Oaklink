from dataclasses import dataclass, field
from datetime import date

from fundkit.models import Holding, LedgerRow
from fundkit.store import Store

PENDING_MAX_AGE_DAYS = 7
_EPSILON = 1e-9


@dataclass
class ReconcileResult:
    applied: list[str] = field(default_factory=list)
    carried: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def reconcile(store: Store, today: date) -> ReconcileResult:
    """Apply filled ledger rows to Holdings and expire stale pending rows."""
    result = ReconcileResult()
    ledger = store.load_ledger()
    holdings = store.load_holdings()
    try:
        watchlist = store.load_watchlist()
    except Exception as e:  # leave a corrupt file untouched for manual recovery; fills still apply
        watchlist = None
        result.warnings.append(f"watchlist unavailable: {type(e).__name__}; held status not updated")
    targets = store.load_targets()

    def sleeve_for(ticker: str) -> str:
        if ticker == targets.index_etf:
            return "indexed_equity"
        if ticker == targets.bond_etf:
            return "bonds"
        return "active"

    for i, row in enumerate(ledger):
        if row.response == "pending":
            if (today - row.date).days > PENDING_MAX_AGE_DAYS:
                ledger[i] = row.model_copy(update={"response": "skipped"})
                result.dropped.append(row.id)
            elif row.date < today:  # today's own proposals (a same-day re-run) are not "carried"
                result.carried.append(row.id)
        elif row.response == "done" and row.action in ("BUY", "SELL"):
            if row.fill_shares is None or row.fill_price is None:
                result.incomplete.append(row.id)
                continue
            proceeds = row.fill_shares * row.fill_price
            if row.action == "BUY":
                holdings.append(Holding(ticker=row.ticker, sleeve=sleeve_for(row.ticker), shares=row.fill_shares,
                                        cost_basis=row.fill_price, lot_date=row.date))
                for w in watchlist or []:
                    if w.ticker == row.ticker:
                        w.status = "held"
                _adjust_cash(holdings, -proceeds, row)
            else:
                holdings, sold = _sell_fifo(holdings, row.ticker, row.fill_shares)
                if sold < row.fill_shares - _EPSILON:
                    result.warnings.append(
                        f"{row.id}: SELL {row.ticker} short by {row.fill_shares - sold:g} shares "
                        f"(requested {row.fill_shares:g}, held {sold:g})")
                _adjust_cash(holdings, proceeds, row)
            ledger[i] = row.model_copy(update={"response": "applied"})
            result.applied.append(row.id)

    store.save_holdings(holdings)
    store.save_ledger(ledger)
    if watchlist is not None:
        store.save_watchlist(watchlist)
    return result


def _adjust_cash(holdings: list[Holding], delta: float, row: LedgerRow) -> None:
    for h in holdings:
        if h.ticker == "CASH":
            h.shares += delta
            return
    holdings.append(Holding(ticker="CASH", sleeve="cash", shares=delta, cost_basis=1.0, lot_date=row.date))


def _sell_fifo(holdings: list[Holding], ticker: str, shares: float) -> tuple[list[Holding], float]:
    """Reduce `ticker` lots oldest-first. Returns (new holdings, shares actually sold)."""
    lots = sorted((h for h in holdings if h.ticker == ticker), key=lambda h: h.lot_date)
    remaining = shares
    for lot in lots:
        take = min(lot.shares, remaining)
        lot.shares -= take
        remaining -= take
        if remaining <= _EPSILON:
            break
    kept = [h for h in holdings if h.ticker != ticker or h.shares > _EPSILON]
    return kept, shares - max(remaining, 0.0)
