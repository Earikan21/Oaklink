"""Assemble the weekly action set: trims, thesis sells, rebalance, and (on review runs) funded new picks.

`build_actions` is read-only with respect to the store: any ledger rows it wants persisted are
returned in `ActionSet.ledger_notes` for the run step to write. Research and market-data failures
become notes so the memo always goes out; the only error raised is an unpriceable index/bond ETF
on the first-run allocation, where no meaningful memo can be produced.
"""
import math
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel

from fundkit.ips import ComplianceReport, compliance
from fundkit.market import MarketData, MarketUnavailable
from fundkit.models import Holding, LedgerRow, Sleeve, Targets, WatchItem
from fundkit.portfolio import (CASH_TICKER, EPS, Proposal, rebalance_proposals, value_holdings,
                               whole_share_target)
from fundkit.research.estimates import revision_summary
from fundkit.screen import GateResult, gate, peers_for
from fundkit.store import Store

LONG_TERM_DAYS = 365
LT_RATE = 0.15
ST_RATE = 0.24
TAX_NOTE = "tax estimate assumes 15% LT / 24% ST flat rates"
MAX_PICKS = 3
MIN_PICK_DOLLARS = 1.0
INITIAL = "initial allocation"
WARNING_WINDOW_DAYS = 120  # two 6-week review cycles plus slack


class ActionSet(BaseModel):
    compliance: ComplianceReport
    proposals: list[Proposal] = []   # BUY/SELL pairs: rebalances, funded picks, first-run buys
    sells: list[Proposal] = []       # trims and thesis sells
    gated: list[GateResult] = []
    notes: list[str] = []
    unpriced: list[str] = []
    values: dict[str, float] = {}
    total: float = 0.0
    ledger_notes: list[LedgerRow] = []  # to be persisted by the run step, never here


@dataclass
class _Sale:
    dollars: float
    est_tax: float
    remaining: list[Holding]  # the ticker's lots after the sale (zero-share lots dropped)


def _valid_price(x) -> bool:
    try:
        return math.isfinite(float(x)) and float(x) > 0
    except (TypeError, ValueError):
        return False


def _sell_lots(lots: list[Holding], shares: float, price: float, today: date) -> _Sale:
    """Consume `shares` FIFO, long-term lots (held >= 365 days) first; estimate tax on gains only."""
    cutoff = today - timedelta(days=LONG_TERM_DAYS)
    ordered = sorted(lots, key=lambda h: (h.lot_date > cutoff, h.lot_date))
    left, tax, remaining = shares, 0.0, []
    for lot in ordered:
        take = min(lot.shares, left)
        if take > EPS:
            rate = LT_RATE if lot.lot_date <= cutoff else ST_RATE
            tax += max((price - lot.cost_basis) * take, 0.0) * rate
            left -= take
        if lot.shares - take > EPS:
            remaining.append(lot.model_copy(update={"shares": lot.shares - take}))
    return _Sale(dollars=shares * price, est_tax=tax, remaining=remaining)


class _Book:
    """Simulated post-trade state used to feed rebalance and the gate. Moves conserve the total."""

    def __init__(self, holdings: list[Holding], values: dict[str, float], today: date):
        self.holdings = [h.model_copy() for h in holdings]
        self.values = dict(values)
        self.today = today
        self._ensure(CASH_TICKER, "cash")

    def _ensure(self, ticker: str, sleeve: Sleeve) -> None:
        if not any(h.ticker == ticker for h in self.holdings):  # zero-share lot: sleeve mapping only
            self.holdings.append(Holding(ticker=ticker, sleeve=sleeve, shares=0.0, cost_basis=0.0,
                                         lot_date=self.today))
        self.values.setdefault(ticker, 0.0)

    def move(self, action: str, ticker: str, dollars: float, sleeve: Sleeve) -> None:
        """BUY moves dollars from CASH into `ticker`; SELL moves them back."""
        self._ensure(ticker, sleeve)
        signed = dollars if action == "BUY" else -dollars
        self.values[ticker] += signed
        self.values[CASH_TICKER] -= signed

    def replace_lots(self, ticker: str, lots: list[Holding]) -> None:
        self.holdings = [h for h in self.holdings if h.ticker != ticker] + lots

    def sleeve_of(self, ticker: str) -> Sleeve | None:
        return next((h.sleeve for h in self.holdings if h.ticker == ticker), None)

    def active(self) -> dict[str, float]:
        tickers = {h.ticker for h in self.holdings if h.sleeve == "active"}
        return {t: v for t, v in self.values.items() if t in tickers and v > EPS}


def _first_run(holdings: list[Holding], market: MarketData, targets: Targets, total: float) -> list[Proposal]:
    prices: dict[str, float] = {}
    for t in (targets.index_etf, targets.bond_etf):
        try:
            p = market.quote(t).price
        except MarketUnavailable as e:
            raise ValueError(f"cannot price {t} for the initial allocation") from e
        if not _valid_price(p):
            raise ValueError(f"cannot price {t} for the initial allocation (price {p!r})")
        prices[t] = float(p)
    shares = whole_share_target(targets.bonds * total, prices[targets.bond_etf])
    return [
        Proposal(action="BUY", ticker=targets.index_etf, dollars=targets.stocks * total, reason=INITIAL),
        Proposal(action="BUY", ticker=targets.bond_etf, dollars=shares * prices[targets.bond_etf],
                 shares=shares, reason=INITIAL),
    ]


def _has_recent_warning(ledger: list[LedgerRow], ticker: str, today: date) -> bool:
    """A thesis warning counts only within WARNING_WINDOW_DAYS; older ones are ignored."""
    return any(r.action == "NOTE" and r.rationale == f"thesis_warning:{ticker}"
               and (today - r.date).days <= WARNING_WINDOW_DAYS for r in ledger)


def build_actions(store: Store, market: MarketData, targets: Targets, today: date, review: bool) -> ActionSet:
    notes: list[str] = []
    holdings = store.load_holdings()
    watch = _load_or_note(store.load_watchlist, "watchlist", notes)
    ledger = _load_or_note(store.load_ledger, "ledger", notes)

    values, unpriced = value_holdings(holdings, market)
    total = sum(values.values())
    report = compliance(holdings, values, targets, review, unpriced)
    out = ActionSet(compliance=report, notes=notes, unpriced=unpriced, values=values, total=total)
    notes = out.notes  # pydantic copies list inputs; append to the model's own list from here on

    if total <= EPS:
        notes.append("portfolio has no value; nothing to propose")
        return out
    if all(h.ticker == CASH_TICKER for h in holdings):
        out.proposals = _first_run(holdings, market, targets, total)
        return out

    prices = _prices(holdings, market, targets, unpriced, notes)
    lots_of: dict[str, list[Holding]] = {}
    for h in holdings:
        lots_of.setdefault(h.ticker, []).append(h)
    held_watch = {w.ticker: w for w in watch if w.status == "held"}
    book = _Book(holdings, values, today)
    if unpriced:
        notes.append(f"valuation incomplete (unpriced: {', '.join(unpriced)}): trims, rebalance and new picks skipped")

    def sell(ticker: str, shares: float, reason: str) -> None:
        sale = _sell_lots(lots_of[ticker], shares, prices[ticker], today)
        out.sells.append(Proposal(action="SELL", ticker=ticker, dollars=sale.dollars, reason=reason,
                                  est_tax=sale.est_tax))
        book.move("SELL", ticker, sale.dollars, "active")
        book.replace_lots(ticker, sale.remaining)

    # (2) thesis sells are decided first so a name that is sold outright is not also trimmed;
    #     they are listed after the trims.
    thesis: list[tuple[str, str]] = []
    for ticker in sorted({h.ticker for h in holdings if h.sleeve == "active"} & set(prices)):
        reason = _thesis_reason(ticker, held_watch.get(ticker), prices[ticker], review, ledger, market, today,
                                out)
        if reason:
            thesis.append((ticker, reason))
    sold_out = {t for t, _ in thesis}

    # (1) trims down to the per-name cap
    if not unpriced:
        for ticker in report.trim_tickers:
            if ticker in sold_out:
                continue
            if ticker not in prices or ticker not in values:
                notes.append(f"{ticker}: no price, trim skipped")
                continue
            excess = values[ticker] - targets.name_cap * total
            if excess <= EPS:
                continue
            pct = values[ticker] / total * 100
            sell(ticker, excess / prices[ticker], f"trim {pct:.1f}% → {targets.name_cap:.0%} cap")

    for ticker, reason in thesis:
        sell(ticker, sum(h.shares for h in lots_of[ticker]), reason)

    if any(p.est_tax for p in out.sells):
        notes.append(TAX_NOTE)

    # (3) rebalance on post-sell weights
    try:
        rebalance = rebalance_proposals(book.holdings, book.values, targets, prices, review, unpriced)
    except ValueError as e:
        notes.append(f"rebalance skipped: {e}")
        rebalance = []
    out.proposals.extend(rebalance)
    for p in rebalance:
        book.move(p.action, p.ticker, p.dollars, book.sleeve_of(p.ticker) or
                  ("bonds" if p.ticker == targets.bond_etf else "indexed_equity"))

    # (4) review runs: fund up to MAX_PICKS passed watchlist names from the index ETF
    if review and not unpriced:
        _picks(watch, book, sold_out, market, targets, total, out)
    return out


def _load_or_note(loader, name: str, notes: list[str]) -> list:
    try:
        return loader()
    except Exception as e:  # a corrupt side table must not stop the memo
        notes.append(f"{name} unreadable ({e}); treated as empty")
        return []


def _prices(holdings: list[Holding], market: MarketData, targets: Targets, unpriced: list[str],
            notes: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    wanted = dict.fromkeys([h.ticker for h in holdings if h.ticker != CASH_TICKER and h.ticker not in unpriced]
                           + [targets.index_etf, targets.bond_etf])
    for t in wanted:
        try:
            p = market.quote(t).price
        except Exception as e:
            notes.append(f"{t}: price unavailable ({type(e).__name__})")
            continue
        if not _valid_price(p):
            notes.append(f"{t}: price unavailable (invalid price {p!r})")
            continue
        prices[t] = float(p)
    return prices


def _thesis_reason(ticker: str, item: WatchItem | None, price: float, review: bool, ledger: list[LedgerRow],
                   market: MarketData, today: date, out: ActionSet) -> str | None:
    if item is None:
        return None
    if item.fair_value is not None and price >= item.fair_value:
        return "fair value reached"
    if not review or item.upside is None or item.upside >= 0:
        return None
    try:
        direction = revision_summary(ticker, market).direction
    except Exception as e:
        out.notes.append(f"{ticker}: revisions unavailable ({type(e).__name__}); thesis check skipped")
        return None
    if direction != "down":
        return None
    if _has_recent_warning(ledger, ticker, today):
        return "thesis broken: two reviews of negative revisions"
    out.ledger_notes.append(LedgerRow(
        id=f"tw-{today}-{ticker}", date=today, action="NOTE", ticker=ticker, amount=0,
        rationale=f"thesis_warning:{ticker}", ips_check="PASS", response="applied"))
    out.notes.append(f"thesis warning: {ticker} estimates revised down with negative upside "
                     f"({item.upside:.0%}); sell if repeated at the next review")
    return None


def _picks(watch: list[WatchItem], book: _Book, sold_out: set[str], market: MarketData, targets: Targets,
           total: float, out: ActionSet) -> None:
    passed = [w for w in watch if w.status == "passed" and w.ticker not in book.values and w.ticker not in sold_out]
    passed.sort(key=lambda w: -(w.upside if w.upside is not None else -math.inf))
    if len(passed) > MAX_PICKS:
        out.notes.append(f"{len(passed) - MAX_PICKS} more passed watchlist item(s) deferred: "
                         f"max {MAX_PICKS} picks per review")
    index = targets.index_etf
    for item in passed[:MAX_PICKS]:
        ticker = item.ticker
        active_value = sum(book.active().values())
        dollars = min(targets.name_cap * total, targets.active_ceiling * total - active_value)
        if dollars < MIN_PICK_DOLLARS:
            out.notes.append("active ceiling reached")
            return
        if book.values.get(index, 0.0) + EPS < dollars:
            out.notes.append(f"{ticker}: not enough {index} to fund ${dollars:,.0f}; pick skipped")
            continue
        try:
            peers = peers_for(ticker, market)
        except Exception as e:
            out.notes.append(f"{ticker}: peers error ({type(e).__name__}: {e}); pick skipped")
            continue
        try:
            g = gate(ticker, dollars, book.holdings, book.values, targets, market, peers, sorted(book.active()))
        except Exception as e:
            out.notes.append(f"{ticker}: gate error ({type(e).__name__}: {e}); pick skipped")
            continue
        out.gated.append(g)
        if not g.passed:
            out.notes.append(f"{ticker} failed gate: " + "; ".join(g.reasons))
            continue
        out.proposals += [
            Proposal(action="SELL", ticker=index, dollars=dollars, reason=f"fund {ticker}"),
            Proposal(action="BUY", ticker=ticker, dollars=dollars, reason=f"{g.upside:.0%} upside, gate passed"),
        ]
        book.move("SELL", index, dollars, "indexed_equity")
        book.move("BUY", ticker, dollars, "active")
