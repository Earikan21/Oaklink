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

from fundkit.ips import ComplianceReport, compliance, instrument_violations
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
MAX_BUY_LINES = 3  # BUY proposals per (non-first) run, rebalance buys included
MIN_PICK_DOLLARS = 1.0
MIN_NET_DOLLARS = 1.0
FUND_REASON = "fund {ticker}"
INITIAL = "initial allocation"
WARNING_WINDOW_DAYS = 120  # two 6-week review cycles plus slack
BREACH_NOTE = "IPS breach unresolved (active ceiling / indexed<active): decide manually — candidates: {names}"
INDEX_RULE_NOTE = ("pick sized by indexed ≥ active rule "
                   "(effective active ceiling {ceiling:.0%} at {stocks:.0%} stocks)")


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

    def copy(self) -> "_Book":
        return _Book(self.holdings, self.values, self.today)

    def sleeve_total(self, sleeve: Sleeve) -> float:
        tickers = {h.ticker for h in self.holdings if h.sleeve == sleeve}
        return sum(v for t, v in self.values.items() if t in tickers)

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


def _screen_fund_buys(proposals: list[Proposal], market: MarketData, targets: Targets,
                      notes: list[str]) -> list[Proposal]:
    """Drop every BUY of the index/bond ETF that fails `check_instrument` (one note per ticker)."""
    violations: dict[str, list[str]] = {}
    kept: list[Proposal] = []
    for p in proposals:
        if p.action == "BUY" and p.ticker in (targets.index_etf, targets.bond_etf):
            if p.ticker not in violations:
                violations[p.ticker] = instrument_violations(p.ticker, market, targets)
                if violations[p.ticker]:
                    notes.append(f"{p.ticker} fails IPS instrument check: {'; '.join(violations[p.ticker])}")
            if violations[p.ticker]:
                continue
        kept.append(p)
    return kept


def _warning_ages(ledger: list[LedgerRow], ticker: str, today: date) -> list[int]:
    return [(today - r.date).days for r in ledger
            if r.action == "NOTE" and r.rationale == f"thesis_warning:{ticker}"]


def build_actions(store: Store, market: MarketData, targets: Targets, today: date, review: bool,
                  watch: list[WatchItem] | None = None) -> ActionSet:
    """`watch`, when given, replaces the stored watchlist (the run step passes this run's in-memory
    revaluations so a dry run need not write them first)."""
    notes: list[str] = []
    holdings = store.load_holdings()
    if watch is None:
        watch = _load_or_note(store.load_watchlist, "watchlist", notes)
    ledger = _load_or_note(store.load_ledger, "ledger", notes)

    values, unpriced = value_holdings(holdings, market)
    total = sum(values.values())
    report = compliance(holdings, values, targets, review, unpriced, market=market)
    out = ActionSet(compliance=report, notes=notes, unpriced=unpriced, values=values, total=total)
    notes = out.notes  # pydantic copies list inputs; append to the model's own list from here on

    if total <= EPS:
        notes.append("portfolio has no value; nothing to propose")
        return out
    if all(h.ticker == CASH_TICKER for h in holdings):
        out.proposals = _screen_fund_buys(_first_run(holdings, market, targets, total), market, targets, notes)
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
        reason = _thesis_reason(ticker, held_watch.get(ticker), prices[ticker], review, ledger, market, targets,
                                today, out)
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
    rebalance = _screen_fund_buys(rebalance, market, targets, notes)
    for p in rebalance:
        p = _taxed(p, book, prices)
        out.proposals.append(p)
        book.move(p.action, p.ticker, p.dollars, book.sleeve_of(p.ticker) or
                  ("bonds" if p.ticker == targets.bond_etf else "indexed_equity"))

    _note_unresolved_breach(report, book, targets, total, notes)

    # (4) review runs: fund up to MAX_PICKS passed watchlist names from the index ETF
    if review and not unpriced:
        _picks(watch, book, sold_out, market, targets, total, prices, out)

    # (5) at most MAX_BUY_LINES buys (later picks are deferred with their funding), then one line per ticker
    out.proposals = _net(_cap_buys(out.proposals, notes), lots_of, prices, today)
    if TAX_NOTE not in notes and any(p.est_tax for p in out.proposals):
        notes.append(TAX_NOTE)
    return out


def _cap_buys(proposals: list[Proposal], notes: list[str]) -> list[Proposal]:
    """Keep the first MAX_BUY_LINES BUYs; each later BUY (always a pick: rebalance buys come first and are at
    most two) is dropped with its funding SELL and noted "deferred: <ticker>". Counted before netting, so the
    cap also holds after it."""
    deferred: list[str] = []
    buys = 0
    for p in proposals:
        if p.action == "BUY":
            buys += 1
            if buys > MAX_BUY_LINES:
                deferred.append(p.ticker)
    notes.extend(f"deferred: {t}" for t in deferred)
    funding = {FUND_REASON.format(ticker=t) for t in deferred}
    return [p for p in proposals
            if not (p.action == "BUY" and p.ticker in deferred) and not (p.action == "SELL" and p.reason in funding)]


def _net(proposals: list[Proposal], lots_of: dict[str, list[Holding]], prices: dict[str, float],
         today: date) -> list[Proposal]:
    """Merge every ticker that has both BUY and SELL proposals into one line at its first position: the net
    dollars (and net shares, when every part has shares), reason "net: <a>; <b>", dropped when under $1.
    A net SELL's est_tax is recomputed from the ticker's lots; a net BUY carries none."""
    actions_of: dict[str, set[str]] = {}
    for p in proposals:
        actions_of.setdefault(p.ticker, set()).add(p.action)
    out: list[Proposal] = []
    netted: set[str] = set()
    for p in proposals:
        if actions_of[p.ticker] != {"BUY", "SELL"}:
            out.append(p)
            continue
        if p.ticker in netted:
            continue
        netted.add(p.ticker)
        group = [q for q in proposals if q.ticker == p.ticker]
        net = sum(q.dollars if q.action == "BUY" else -q.dollars for q in group)
        if abs(net) < MIN_NET_DOLLARS:
            continue
        shares = None
        if all(q.shares is not None for q in group):
            shares = abs(sum(q.shares if q.action == "BUY" else -q.shares for q in group))
        action = "BUY" if net > 0 else "SELL"
        est_tax = None
        if action == "SELL" and p.ticker in prices:
            price = prices[p.ticker]
            est_tax = _sell_lots(lots_of.get(p.ticker, []), abs(net) / price, price, today).est_tax
        out.append(Proposal(action=action, ticker=p.ticker, dollars=abs(net), shares=shares,
                            reason="net: " + "; ".join(q.reason for q in group), est_tax=est_tax))
    return out


def _taxed(p: Proposal, book: _Book, prices: dict[str, float]) -> Proposal:
    """A SELL with `est_tax` from the ticker's current simulated lots (long-term first); those lots are
    consumed in `book`. A BUY, or a SELL with no price, is returned unchanged."""
    if p.action != "SELL" or p.ticker not in prices:
        return p
    price = prices[p.ticker]
    lots = [h for h in book.holdings if h.ticker == p.ticker and h.shares > EPS]
    shares = p.shares if p.shares is not None else p.dollars / price
    sale = _sell_lots(lots, shares, price, book.today)
    book.replace_lots(p.ticker, sale.remaining)
    return p.model_copy(update={"est_tax": sale.est_tax})


def _note_unresolved_breach(report: ComplianceReport, book: _Book, targets: Targets, total: float,
                            notes: list[str]) -> None:
    """The engine never sells active names to fix the ceiling or indexed ≥ active (only trims and thesis
    sells); if those flags are set and the post-trade book still breaches, say so and list the candidates."""
    if not (report.active_ceiling_flag or report.indexed_lt_active):
        return
    active = book.sleeve_total("active")
    if active <= targets.active_ceiling * total + EPS and book.sleeve_total("indexed_equity") + EPS >= active:
        return
    names = sorted(book.active().items(), key=lambda kv: (-kv[1], kv[0]))
    notes.append(BREACH_NOTE.format(names=", ".join(t for t, _ in names)))


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
                   market: MarketData, targets: Targets, today: date, out: ActionSet) -> str | None:
    if item is None:
        return None
    if item.fair_value is not None and price >= item.fair_value:
        return "fair value reached"
    if not review or item.upside is None or item.upside >= targets.min_upside:
        return None
    try:
        direction = revision_summary(ticker, market).direction
    except Exception as e:
        out.notes.append(f"{ticker}: revisions unavailable ({type(e).__name__}); thesis check skipped")
        return None
    if direction != "down":
        return None
    ages = _warning_ages(ledger, ticker, today)
    if any(0 < age <= WARNING_WINDOW_DAYS for age in ages):  # an earlier review within the window
        return "thesis broken: two reviews of negative revisions"
    if 0 in ages:  # same-day re-run: the warning is already recorded; neither sell nor duplicate it
        out.notes.append(f"thesis warning: {ticker} already recorded today")
        return None
    out.ledger_notes.append(LedgerRow(
        id=f"tw-{today}-{ticker}", date=today, action="NOTE", ticker=ticker, amount=0,
        rationale=f"thesis_warning:{ticker}", ips_check="PASS", response="applied"))
    out.notes.append(f"thesis warning: {ticker} estimates revised down with thin upside "
                     f"(upside {item.upside:.0%} < {targets.min_upside:.0%}); sell if repeated at the next review")
    return None


def _ratio(targets: Targets) -> dict[str, float]:
    return {"ceiling": min(targets.active_ceiling, targets.stocks / 2), "stocks": targets.stocks}


def _picks(watch: list[WatchItem], book: _Book, sold_out: set[str], market: MarketData, targets: Targets,
           total: float, prices: dict[str, float], out: ActionSet) -> None:
    passed = [w for w in watch if w.status == "passed" and w.ticker not in book.values and w.ticker not in sold_out]
    passed.sort(key=lambda w: -(w.upside if w.upside is not None else -math.inf))
    if len(passed) > MAX_PICKS:
        out.notes.append(f"{len(passed) - MAX_PICKS} more passed watchlist item(s) deferred: "
                         f"max {MAX_PICKS} picks per review")
    index = targets.index_etf
    for item in passed[:MAX_PICKS]:
        ticker = item.ticker
        active_value = book.sleeve_total("active")
        by_ceiling = min(targets.name_cap * total, targets.active_ceiling * total - active_value)
        # the pair sells `dollars` of indexed and buys `dollars` of active: indexed >= active
        # after the pair needs dollars <= (indexed - active) / 2
        by_index_rule = max(0.0, (book.sleeve_total("indexed_equity") - active_value) / 2)
        if by_ceiling < MIN_PICK_DOLLARS:
            out.notes.append("active ceiling reached")
            return
        if by_index_rule < MIN_PICK_DOLLARS:
            out.notes.append("indexed ≥ active binds; no room for picks")
            return
        dollars = min(by_ceiling, by_index_rule)
        if by_index_rule < by_ceiling and INDEX_RULE_NOTE.format(**_ratio(targets)) not in out.notes:
            out.notes.append(INDEX_RULE_NOTE.format(**_ratio(targets)))
        if book.values.get(index, 0.0) + EPS < dollars:
            out.notes.append(f"{ticker}: not enough {index} to fund ${dollars:,.0f}; pick skipped")
            continue
        try:
            peers = peers_for(ticker, market)
        except Exception as e:
            out.notes.append(f"{ticker}: peers error ({type(e).__name__}: {e}); pick skipped")
            continue
        try:
            funded = book.copy()  # gate sees the post-pair state: index already sold to cash
            funded.move("SELL", index, dollars, "indexed_equity")
            g = gate(ticker, dollars, funded.holdings, funded.values, targets, market, peers,
                     sorted(book.active()))
        except Exception as e:
            out.notes.append(f"{ticker}: gate error ({type(e).__name__}: {e}); pick skipped")
            continue
        out.gated.append(g)
        if not g.passed:
            out.notes.append(f"{ticker} failed gate: " + "; ".join(g.reasons))
            continue
        out.proposals += [
            _taxed(Proposal(action="SELL", ticker=index, dollars=dollars, reason=FUND_REASON.format(ticker=ticker)),
                   book, prices),
            Proposal(action="BUY", ticker=ticker, dollars=dollars, reason=f"{g.upside:.0%} upside, gate passed"),
        ]
        book.move("SELL", index, dollars, "indexed_equity")
        book.move("BUY", ticker, dollars, "active")
